"""Scenario routing tests (RuleRouter / HybridRouter / RoutedSearchClient)."""

import pytest

from nexusearch.models import NexusSearchOptions, SearchHit
from nexusearch.routing import HybridRouter, RoutedSearchClient, RouteRule, RuleRouter
from nexusearch.simple_profile import SimpleSearchProfile

RULES = [RouteRule("expo", r"выставк|expo|trade.?show")]
DESCS = {"supplier": "B2B wholesale suppliers", "expo": "Trade shows and exhibitor lists"}


class _SpyLlm:
    def __init__(self, payload, error=None) -> None:
        self.payload = payload
        self.error = error
        self.calls = 0

    def generate_json(self, prompt):
        self.calls += 1
        if self.error:
            raise self.error
        return self.payload


# --- RuleRouter --------------------------------------------------------------


def test_rule_router_first_match_and_default():
    r = RuleRouter(RULES, default="supplier")
    assert r.route("выставка кемпинга") == "expo"
    assert r.route("Camping EXPO 2027") == "expo"  # case-insensitive
    assert r.route("кирпич оптом") == "supplier"


def test_rule_router_order_matters():
    r = RuleRouter(
        [RouteRule("a", r"foo"), RouteRule("b", r"foo")],
        default="a",
    )
    assert r.route("foo bar") == "a"


def test_rule_router_validation():
    with pytest.raises(ValueError, match="default"):
        RuleRouter([], default=" ")
    with pytest.raises(ValueError, match="invalid RouteRule pattern"):
        RuleRouter([RouteRule("x", "([unclosed")], default="x")


# --- HybridRouter ------------------------------------------------------------


def test_hybrid_rule_hit_does_not_call_llm():
    llm = _SpyLlm({"profile": "supplier"})
    r = HybridRouter(RULES, default="supplier")
    assert r.route("выставка мебели", llm=llm, descriptions=DESCS) == "expo"
    assert llm.calls == 0


def test_hybrid_llm_classifies_when_no_rule():
    llm = _SpyLlm({"profile": "expo"})
    r = HybridRouter(RULES, default="supplier")
    assert r.route("где производители показывают новинки", llm=llm, descriptions=DESCS) == "expo"
    assert llm.calls == 1


def test_hybrid_llm_unknown_or_garbage_falls_back():
    r = HybridRouter(RULES, default="supplier")
    assert r.route("q", llm=_SpyLlm({"profile": "evil"}), descriptions=DESCS) == "supplier"
    assert r.route("q", llm=_SpyLlm({"wrong_key": 1}), descriptions=DESCS) == "supplier"
    assert r.route("q", llm=_SpyLlm(["not", "a", "dict"]), descriptions=DESCS) == "supplier"
    assert r.route("q", llm=_SpyLlm(None, error=RuntimeError("boom")), descriptions=DESCS) == "supplier"


def test_hybrid_without_llm_or_descriptions_uses_default():
    r = HybridRouter(RULES, default="supplier")
    assert r.route("q", llm=None, descriptions=DESCS) == "supplier"
    assert r.route("q", llm=_SpyLlm({"profile": "expo"}), descriptions=None) == "supplier"


# --- RoutedSearchClient ------------------------------------------------------


class _StubAdapter:
    def __init__(self, name: str, channel: str) -> None:
        self.name = name
        self.channel = channel
        self.calls: list[str] = []

    def discover(self, query, *, max_results=10, iteration=1, ignored_domains=()):
        self.calls.append(query)
        return [
            SearchHit(
                title="T",
                url=f"https://{self.name}.example/",
                domain=f"{self.name}.example",
                discovery_query=query,
                source_adapter=self.name,
                channel=self.channel,
            )
        ], True


def _profiles():
    return {
        "supplier": SimpleSearchProfile(name="supplier", query_templates=["{query} опт"]),
        "expo": SimpleSearchProfile(
            name="expo", channels=("expo",), query_templates=["{query} выставка"]
        ),
    }


def test_routed_client_validation():
    with pytest.raises(ValueError, match="at least one profile"):
        RoutedSearchClient(profiles={}, router=RuleRouter([], default="x"))
    with pytest.raises(ValueError, match="unknown profile"):
        RoutedSearchClient(profiles=_profiles(), router=RuleRouter([RouteRule("nope", "x")], default="supplier"))
    with pytest.raises(ValueError, match="not in profiles"):
        RoutedSearchClient(profiles=_profiles(), router=RuleRouter([], default="nope"))


def test_routed_client_delegates_with_channel_isolation():
    serp = _StubAdapter("serp_stub", "serp")
    expo = _StubAdapter("expo_stub", "expo")
    client = RoutedSearchClient(
        profiles=_profiles(),
        router=RuleRouter(RULES, default="supplier"),
        adapters=[serp, expo],
    )
    opts = NexusSearchOptions(deep_read=False, enable_iter2=False)

    bundle = client.search("кирпич оптом", opts)
    assert bundle.meta.profile == "supplier"
    assert [h.domain for h in bundle.hits] == ["serp_stub.example"]
    assert serp.calls and not expo.calls  # expo channel filtered out for supplier

    bundle = client.search("выставка кемпинга", opts)
    assert bundle.meta.profile == "expo"
    assert [h.domain for h in bundle.hits] == ["expo_stub.example"]
    assert not serp.calls or len(serp.calls) == 1  # serp not called for expo scenario


def test_routed_client_hybrid_llm_path():
    llm = _SpyLlm({"profile": "expo"})
    client = RoutedSearchClient(
        profiles=_profiles(),
        router=HybridRouter(RULES, default="supplier"),
        llm=llm,
        adapters=[_StubAdapter("expo_stub", "expo")],
    )
    assert client.route("где производители показывают новинки") == "expo"
    assert llm.calls == 1
