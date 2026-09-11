"""Regression tests for the v0.2.0 review remediation (see NEXUSEARCH_V02_REVIEW.md)."""

import asyncio
import math

import pytest

from nexusearch.adapters_llm import LlmGroundedAdapter, _extract_candidates
from nexusearch.async_client import AsyncNexusSearchClient
from nexusearch.client import NexusSearchClient
from nexusearch.config import NexusSearchSettings
from nexusearch.discovery import discover_for_queries, extract_domain, merge_hits
from nexusearch.models import NexusSearchOptions, SearchHit
from nexusearch.ops import Budget, SearchCache
from tests.test_adapters import _Profile, _StubAdapter

# --- #1 adapter crash isolation ---------------------------------------------

class _BoomAdapter:
    name = "boom"
    channel = "serp"

    def discover(self, query, **kw):
        raise RuntimeError("custom adapter crash")


def test_crashing_adapter_isolated_sequential():
    hits, engines, _ = discover_for_queries(
        ["q"], adapters=[_BoomAdapter(), _StubAdapter()], iteration=1, max_hits=10
    )
    assert [h.domain for h in hits] == ["q.example.com"]
    assert "boom" not in engines
    assert "stub" in engines


def test_crashing_adapter_isolated_parallel():
    hits, engines, _ = discover_for_queries(
        ["q"], adapters=[_BoomAdapter(), _StubAdapter()], iteration=1, max_hits=10, parallel=True
    )
    assert [h.domain for h in hits] == ["q.example.com"]
    assert "boom" not in engines


# --- #3 deep_read honored + extra forbid ------------------------------------

def test_deep_read_option_exists_and_disables():
    opts = NexusSearchOptions(deep_read=False)
    assert opts.deep_read is False
    client = NexusSearchClient(profile=_Profile(), adapters=[_StubAdapter()])
    bundle = client.search("cnc machining", opts)
    assert bundle.meta.deep_read_count == 0


def test_extra_fields_forbidden():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        NexusSearchOptions(nonexistent_field=True)


# --- async client keys pass-through ------------------------------------------

def test_async_client_passes_all_keys():
    s = NexusSearchSettings(
        tavily_api_key="t", firecrawl_api_key="f",
        brave_api_key="b", serpapi_api_key="s", proxy_url=None,
    )
    client = AsyncNexusSearchClient.from_env(profile=_Profile(), settings=s, adapters=[])
    assert client._sync.brave_api_key == "b"
    assert client._sync.serpapi_api_key == "s"


# --- #4/#5 merge cap + telemetry ---------------------------------------------

def test_merge_hits_never_exceeds_cap():
    existing = [SearchHit(url=f"https://d{i}.example/", domain=f"d{i}.example") for i in range(20)]
    out = merge_hits(existing, [SearchHit(url="https://new.example/", domain="new.example")], max_hits=20)
    assert len(out) == 20


def test_parallel_engines_with_hits_post_merge():
    class _Dupe:
        name = "dupe"
        channel = "serp"

        def discover(self, q, **kw):
            return [SearchHit(url="https://a.example/", domain="a.example")], True

    class _Other:
        name = "other"
        channel = "serp"

        def discover(self, q, **kw):
            return [], True

    existing = [SearchHit(url="https://a.example/", domain="a.example")]
    _, _, wh = discover_for_queries(
        ["q"], adapters=[_Dupe(), _Other()], iteration=2, max_hits=10,
        existing=existing, parallel=True,
    )
    assert "dupe" not in wh


# --- #6/#16 www. normalization -----------------------------------------------

def test_extract_domain_removeprefix_only():
    assert extract_domain("https://awww.example.com/x") == "awww.example.com"
    assert extract_domain("https://www.www.example.com/") == "www.example.com"
    assert extract_domain("https://www.acme.com/") == "acme.com"


def test_llm_www_dedupes_with_serp():
    class _L:
        def generate_json(self, prompt):
            return {"candidates": [{"name": "Acme", "url": "https://www.acme.com/"}]}

    hits, _ = LlmGroundedAdapter(_L()).discover("q")
    assert hits[0].domain == "acme.com"


# --- #9/#10 cache edges --------------------------------------------------------

def test_cache_rejects_zero_max_entries():
    with pytest.raises(ValueError):
        SearchCache(max_entries=0)


def test_cache_update_existing_key_no_innocent_eviction():
    from nexusearch.models import SearchBundle

    c = SearchCache(ttl_seconds=60, max_entries=2)
    c.set("a", SearchBundle())
    c.set("b", SearchBundle())
    c.set("b", SearchBundle())
    assert c.get("a") is not None


# --- #25 budget validation ------------------------------------------------------

def test_budget_rejects_nonpositive():
    with pytest.raises(ValueError):
        Budget(max_seconds=0)
    with pytest.raises(ValueError):
        Budget(max_seconds=-1)


# --- #20 grounding_score sanitization -------------------------------------------

def test_grounding_score_clamped_and_nan_dropped():
    class _L:
        def generate_json(self, prompt):
            return {"candidates": [
                {"name": "A", "url": "https://a.example/", "confidence": 42.0},
                {"name": "B", "url": "https://b.example/", "confidence": "nan"},
            ]}

    hits, _ = LlmGroundedAdapter(_L()).discover("q")
    assert hits[0].grounding_score == 1.0
    assert hits[1].grounding_score is None or math.isfinite(hits[1].grounding_score)


# --- #19 bare JSON array strings -------------------------------------------------

def test_extract_candidates_bare_array_string():
    assert _extract_candidates('[{"name": "A", "url": "https://a.example/"}]') == [
        {"name": "A", "url": "https://a.example/"}
    ]


# --- #11 discarded_entities bounded + query attribution ---------------------------

def test_discarded_entities_bounded_with_query():
    class _L:
        def generate_json(self, prompt):
            return {"candidates": [{"name": "NoUrl"}]}

    a = LlmGroundedAdapter(_L(), max_discarded=3)
    for i in range(10):
        a.discover(f"q{i}")
    d = a.discarded_entities
    assert len(d) == 3
    assert d[-1]["query"] == "q9"


# --- #12/#13 hooks ---------------------------------------------------------------

def test_on_queries_planned_emitted_sync():
    events = []

    class _H:
        def on_queries_planned(self, queries, *, iteration):
            events.append((tuple(queries), iteration))

    client = NexusSearchClient(profile=_Profile(), adapters=[_StubAdapter()], hooks=[_H()])
    client.search("abc", NexusSearchOptions(deep_read=False))
    assert (("abc",), 1) in events


def test_on_search_error_emitted():
    errors = []

    class _H:
        def on_search_error(self, error, *, elapsed_ms):
            errors.append(str(error))

    class _BadProfile(_Profile):
        def plan_iter1(self, query, **kw):
            raise RuntimeError("planner down")

    client = AsyncNexusSearchClient(profile=_BadProfile(), adapters=[], hooks=[_H()])
    with pytest.raises(RuntimeError):
        asyncio.run(client.search("q", NexusSearchOptions(deep_read=False)))
    assert errors == ["planner down"]


def test_future_returning_hook_awaited():
    done = []

    class _H:
        def on_search_start(self, query, options):
            loop = asyncio.get_running_loop()
            fut = loop.create_future()
            fut.set_result(None)
            return fut.then(lambda _: done.append(query)) if hasattr(fut, "then") else _wrap(fut, done, query)

    def _wrap(fut, done, query):
        async def _c():
            await fut
            done.append(query)
        return _c()

    client = AsyncNexusSearchClient(profile=_Profile(), adapters=[_StubAdapter()], hooks=[_H()])
    asyncio.run(client.search("fq", NexusSearchOptions(deep_read=False)))
    assert done == ["fq"]
