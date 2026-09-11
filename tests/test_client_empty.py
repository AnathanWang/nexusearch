"""Client orchestration with FakeProfile."""

from typing import Any

from nexusearch import NexusSearchClient, NexusSearchOptions
from nexusearch import client as client_mod
from nexusearch.models import HintConfidence, PageEvidence, PageSnippet, SearchHit


class FakeProfile:
    name = "fake"
    ignored_domains: tuple[str, ...] = ()
    deep_paths: tuple[str, ...] = ("/",)

    def plan_iter1(self, query: str, *, llm, max_queries: int) -> list[str]:
        return ["q1"]

    def plan_iter2(self, hits, *, already_used, llm, max_queries: int) -> list[str]:
        return []

    def extract_hints(self, text: str, *, url: str) -> dict[str, Any]:
        return {}

    def score_confidence(
        self,
        pages,
        *,
        pages_with_hints: int,
        has_any_hint: bool,
        contributing_excerpt_len: int = 0,
    ) -> HintConfidence | None:
        return None


def test_empty_search(monkeypatch):
    monkeypatch.setattr(
        client_mod,
        "discover_for_queries",
        lambda *a, **k: ([], [], []),
    )
    bundle = NexusSearchClient(profile=FakeProfile()).search(
        "anything", NexusSearchOptions(max_deep_read=0, enable_iter2=False)
    )
    assert bundle.hits == []
    assert bundle.meta.message == "No live search hits found."
    assert bundle.meta.engines_with_hits == []


def test_deep_read_count(monkeypatch):
    hit = SearchHit(title="A", url="https://a.example", domain="a.example")

    def fake_discover(*a, **k):
        return [hit], ["ddg"], ["ddg"]

    def fake_deep(hits, **kwargs):
        enriched = []
        for h in hits:
            ev = PageEvidence(
                domain=h.domain,
                pages=[PageSnippet(url=h.url, text_excerpt="body")],
                extracted={"country": "US"},
                sources={"country": h.url},
                hint_confidence="MEDIUM",
            )
            enriched.append(h.model_copy(update={"page_evidence": ev}))
        return enriched, 0

    monkeypatch.setattr(client_mod, "discover_for_queries", fake_discover)
    monkeypatch.setattr(client_mod, "deep_read_hits", fake_deep)
    bundle = NexusSearchClient(profile=FakeProfile()).search(
        "q",
        NexusSearchOptions(max_deep_read=5, enable_iter2=False, max_hits=10),
    )
    assert bundle.meta.deep_read_count == 1
    assert bundle.meta.engines_with_hits == ["ddg"]
    assert bundle.hits[0].page_evidence is not None


def test_requires_profile():
    try:
        NexusSearchClient(profile=None)  # type: ignore[arg-type]
        raised = False
    except TypeError:
        raised = True
    assert raised
