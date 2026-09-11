"""DiscoveryAdapter protocol & provenance tests."""

from nexusearch.client import NexusSearchClient
from nexusearch.discovery import DiscoveryAdapter
from nexusearch.models import NexusSearchOptions, SearchBundle, SearchHit


class _StubAdapter:
    name = "stub"
    channel = "serp"

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def discover(self, query, *, max_results=10, iteration=1, ignored_domains=()):
        self.calls.append((query, iteration))
        return [
            SearchHit(
                title="T",
                url=f"https://{query.replace(' ', '')}.example.com/",
                snippet="s",
                domain=f"{query.replace(' ', '')}.example.com",
                discovery_query=query,
                iteration=iteration,
                source_adapter=self.name,
                channel=self.channel,
            )
        ], True


class _Profile:
    name = "p"

    def plan_iter1(self, query: str, *, max_queries: int = 6, llm=None) -> list[str]:
        return [query]

    def plan_iter2(self, hits, *, max_queries: int = 6, llm=None, **kw) -> list[str]:
        return []

    def extract_hints(self, text: str) -> dict:
        return {}

    @property
    def ignored_domains(self) -> tuple[str, ...]:
        return ()

    @property
    def deep_paths(self) -> tuple[str, ...]:
        return ()

    def score_confidence(self, ev, hit) -> object:
        return None


def test_stub_adapter_satisfies_protocol():
    assert isinstance(_StubAdapter(), DiscoveryAdapter)


def test_client_uses_custom_adapters_with_provenance():
    stub = _StubAdapter()
    client = NexusSearchClient(profile=_Profile(), adapters=[stub])
    bundle = client.search("cnc machining", NexusSearchOptions(deep_read=False))
    assert isinstance(bundle, SearchBundle)
    assert stub.calls, "custom adapter must be invoked"
    assert len(bundle.hits) == 1
    hit = bundle.hits[0]
    assert hit.source_adapter == "stub"
    assert hit.channel == "serp"
    assert bundle.meta.engines == ["stub"]
    assert bundle.meta.engines_with_hits == ["stub"]


def test_search_hit_provenance_defaults():
    h = SearchHit(url="https://x.example/", domain="x.example")
    assert h.source_adapter == ""
    assert h.channel == "serp"


def test_profile_channels_filter_adapters():
    class _LlmCh:
        name = "llm_ch"
        channel = "llm_grounded"

        def __init__(self):
            self.calls = 0

        def discover(self, query, *, max_results=10, iteration=1, ignored_domains=()):
            self.calls += 1
            return [], True

    stub = _StubAdapter()
    llm_ch = _LlmCh()

    class _SerpOnlyProfile(_Profile):
        channels = ("serp",)

    client = NexusSearchClient(profile=_SerpOnlyProfile(), adapters=[stub, llm_ch])
    client.search("q", NexusSearchOptions(deep_read=False))
    assert stub.calls, "serp adapter must run"
    assert llm_ch.calls == 0, "llm_grounded adapter must be filtered out by profile channels"


def test_parallel_discover_merges_all_adapters():
    from nexusearch.discovery import discover_for_queries

    class _A:
        name = "a"
        channel = "serp"

        def discover(self, query, *, max_results=10, iteration=1, ignored_domains=()):
            return [SearchHit(url="https://a.example/", domain="a.example",
                              source_adapter="a", channel="serp")], True

    class _B:
        name = "b"
        channel = "serp"

        def discover(self, query, *, max_results=10, iteration=1, ignored_domains=()):
            return [SearchHit(url="https://b.example/", domain="b.example",
                              source_adapter="b", channel="serp")], True

    hits, engines, with_hits = discover_for_queries(
        ["q"], adapters=[_A(), _B()], iteration=1, max_hits=10, parallel=True
    )
    assert {h.domain for h in hits} == {"a.example", "b.example"}
    assert engines == ["a", "b"]
    assert with_hits == ["a", "b"]
