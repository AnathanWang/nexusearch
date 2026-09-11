"""DiscoveryAdapter protocol & provenance tests."""

from nexusearch.client import NexusSearchClient
from nexusearch.discovery import DiscoveryAdapter
from nexusearch.models import NexusSearchOptions, SearchBundle, SearchHit
from nexusearch.profile import SearchProfile


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
