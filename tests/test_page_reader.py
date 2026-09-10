"""Page reader with profile-driven hints."""

from typing import Any

import nexusearch.page_reader as pr
from nexusearch.models import HintConfidence, PageEvidence, PageSnippet, SearchHit
from nexusearch.page_reader import deep_read_hits, read_domain_evidence


class HintProfile:
    name = "hint"
    ignored_domains: tuple[str, ...] = ()
    deep_paths: tuple[str, ...] = ("/",)

    def plan_iter1(self, query: str, *, llm, max_queries: int) -> list[str]:
        return [query]

    def plan_iter2(self, hits, *, already_used, llm, max_queries: int) -> list[str]:
        return []

    def extract_hints(self, text: str, *, url: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if "US-based" in text or "usa" in text.lower():
            out["country"] = "US"
        if "dropship" in text.lower():
            out["dropship"] = True
        if "400 skus" in text.lower():
            out["sku_count"] = 400
        if "Minimum order" in text:
            out["moq"] = "Minimum order 50 units"
        return out

    def score_confidence(
        self,
        pages,
        *,
        pages_with_hints: int,
        has_any_hint: bool,
        contributing_excerpt_len: int = 0,
    ) -> HintConfidence | None:
        if not has_any_hint:
            return None
        if pages_with_hints == 1 and contributing_excerpt_len >= 400:
            return "MEDIUM"
        return "LOW"


def test_read_domain_evidence_blocks_private_url():
    hit = SearchHit(
        title="Internal",
        url="https://127.0.0.1/admin",
        domain="127.0.0.1",
        snippet="dropship USA 500 products",
    )
    ev = read_domain_evidence(hit, profile=HintProfile(), firecrawl_api_key=None, proxy=None, max_pages=2)
    assert ev.pages == []
    assert ev.extracted == {}
    assert ev.hint_confidence is None


def test_deep_read_hits_skips_empty_evidence(monkeypatch):
    def fake_read(hit, **kwargs):
        return PageEvidence(domain=hit.domain, pages=[])

    monkeypatch.setattr(pr, "read_domain_evidence", fake_read)
    hits = [SearchHit(title="A", url="https://a.example", domain="a.example")]
    out = deep_read_hits(hits, profile=HintProfile(), max_deep_read=1)
    assert out[0].page_evidence is None


def test_deep_read_budget_stops_early(monkeypatch):
    calls = {"n": 0}
    clock = {"t": 0.0}

    def fake_mono():
        return clock["t"]

    def fake_read(hit, **kwargs):
        calls["n"] += 1
        clock["t"] += 10.0
        return PageEvidence(
            domain=hit.domain,
            pages=[PageSnippet(url=hit.url, text_excerpt="US-based " + ("x" * 400))],
            extracted={"country": "US"},
            sources={"country": hit.url},
            hint_confidence="MEDIUM",
        )

    monkeypatch.setattr(pr.time, "monotonic", fake_mono)
    monkeypatch.setattr(pr, "read_domain_evidence", fake_read)

    hits = [
        SearchHit(title="A", url="https://a.example", domain="a.example"),
        SearchHit(title="B", url="https://b.example", domain="b.example"),
        SearchHit(title="C", url="https://c.example", domain="c.example"),
    ]
    out = deep_read_hits(hits, profile=HintProfile(), max_deep_read=3, max_deep_read_seconds=15.0)
    assert calls["n"] == 2
    assert out[0].page_evidence is not None
    assert out[1].page_evidence is not None
    assert out[2].page_evidence is None


def test_hints_include_source_url_and_confidence(monkeypatch):
    monkeypatch.setattr(pr, "is_safe_url", lambda *a, **k: True)

    def fake_pinned(url, *, expected_domain, attempts=2, timeout=8.0):
        return (
            "US-based manufacturer. Minimum order 50 units. "
            "Catalog of 400 skus. We offer dropship. " + ("body " * 80)
        )

    monkeypatch.setattr(pr, "_pinned_fetch_text", fake_pinned)
    monkeypatch.setattr(pr, "_firecrawl_fetch", lambda *a, **k: "")

    hit = SearchHit(title="Vendor", url="https://vendor.example/", domain="vendor.example")
    ev = read_domain_evidence(hit, profile=HintProfile(), max_pages=1)
    assert ev.pages
    assert ev.extracted.get("country") == "US"
    assert ev.sources.get("country")
    assert ev.extracted.get("sku_count") == 400
    assert ev.extracted.get("dropship") is True
    assert ev.hint_confidence in ("MEDIUM", "HIGH")
