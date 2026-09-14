"""Live smoke tests agains the real search API's

Skipped by default; run mannualy or nightly:
    NEXUSEARCH_LIVE_TESTS=1 uv run pytest tests/test_live_smoke.py -q
"""

import os

import pytest

from nexusearch import (
    DuckDuckGoAdapter,
    NexusSearchClient,
    NexusSearchOptions,
    SimpleSearchProfile,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("NEXUSEARCH_LIVE_TESTS"),
    reason="live tests disabled (set NEXUSEARCH_LIVE_TESTS=1)"
)

def _profile() -> SimpleSearchProfile:
    return SimpleSearchProfile(
        name="live-smoke",
        query_templates=["{query} manufacturer"],
    )

def test_ddg_live_returns_real_hits():
    client = NexusSearchClient(profile = _profile(), adapters =[DuckDuckGoAdapter()])
    bundle = client.search(
        "cnc machining suppliers",
        NexusSearchOptions(deep_read = False, enable_iter2=False, max_hits=5),
        )
    assert bundle.hits, "DDG live returned nothing - markup or content broken?"
    assert all(h.url.startswith("https") for h in bundle.hits)
    assert all(h.domain for h in bundle.hits)

@pytest.mark.skipif(not os.getenv("TAVILY_API_KEY"), reason = "no TAVILY_API_KEY")
def test_tavily_live_returns_real_hits():
    from nexusearch import TavilyAdapter

    adapter = TavilyAdapter(os.environ["TAVILY_API_KEY"])
    hits, ok = adapter.discover("cnc machining suppliers", max_results=3)
    assert ok
    assert hits and all(h.url.startswith("https") for h in hits)