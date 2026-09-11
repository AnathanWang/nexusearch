"""Content-policy validation (stop/required words) during deep-read."""

from __future__ import annotations

import nexusearch.client as client_mod
import nexusearch.page_reader as pr
from nexusearch.client import NexusSearchClient
from nexusearch.models import NexusSearchOptions, SearchHit
from nexusearch.page_reader import deep_read_hits, read_domain_evidence


class _PolicyProfile:
    """Profile with content-policy hooks (stop/required words)."""

    name = "policy"
    ignored_domains: tuple[str, ...] = ()
    deep_paths: tuple[str, ...] = ("/",)

    def __init__(self, stop=(), required=()) -> None:
        self._stop = tuple(s.lower() for s in stop)
        self._required = tuple(s.lower() for s in required)

    def plan_iter1(self, query, *, llm, max_queries):
        return [query]

    def plan_iter2(self, hits, *, already_used, llm, max_queries):
        return []

    def extract_hints(self, text, *, url):
        return {}

    def score_confidence(self, pages, *, pages_with_hints, has_any_hint, contributing_excerpt_len=0):
        return None

    def reject_page(self, text, *, url):
        lower = text.lower()
        return any(s in lower for s in self._stop)

    def accept_domain(self, texts, *, domain):
        if not self._required:
            return True
        blob = " ".join(texts).lower()
        return any(w in blob for w in self._required)


def _fake_fetch(monkeypatch, text_by_url):
    def fake_pinned(url, **kw):
        if isinstance(text_by_url, str):
            return text_by_url
        return text_by_url.get(url, "")

    monkeypatch.setattr(pr, "_pinned_fetch_text", fake_pinned)
    monkeypatch.setattr(pr, "_firecrawl_fetch", lambda *a, **k: "")


def _hit(domain: str) -> SearchHit:
    return SearchHit(title=domain, url=f"https://{domain}/", domain=domain)


def test_stop_word_rejects_domain_immediately(monkeypatch):
    _fake_fetch(monkeypatch, "Добро пожаловать! Положить в корзину и купить в розницу.")
    ev = read_domain_evidence(_hit("shop.example"), profile=_PolicyProfile(stop=["положить в корзину"]))
    assert ev.rejected is True
    assert ev.pages == []  # aborted before collecting pages


def test_required_word_missing_rejects_at_end(monkeypatch):
    _fake_fetch(monkeypatch, "Мы продаём товары для всех. Добро пожаловать на сайт.")
    ev = read_domain_evidence(_hit("vendor.example"), profile=_PolicyProfile(required=["прайс"]))
    assert ev.rejected is True
    assert ev.pages != []  # pages were fetched; rejected by domain policy


def test_required_word_present_keeps_domain(monkeypatch):
    _fake_fetch(monkeypatch, "Оптовые поставки. Скачать прайс-лист. Отгрузка фурами.")
    ev = read_domain_evidence(_hit("vendor.example"), profile=_PolicyProfile(required=["прайс"]))
    assert ev.rejected is False
    assert ev.pages != []


def test_deep_read_drops_rejected_and_counts(monkeypatch):
    _fake_fetch(monkeypatch, {
        "https://a.example/": "опт прайс-лист отгрузка",
        "https://b.example/": "положить в корзину розница",
    })
    profile = _PolicyProfile(stop=["положить в корзину"], required=["прайс"])
    out, rejected = deep_read_hits(
        [_hit("a.example"), _hit("b.example")], profile=profile, max_deep_read=5
    )
    assert [h.domain for h in out] == ["a.example"]
    assert rejected == 1


def test_fetch_failure_is_not_rejection(monkeypatch):
    _fake_fetch(monkeypatch, "")  # site down / blocked
    profile = _PolicyProfile(stop=["корзина"], required=["прайс"])
    out, rejected = deep_read_hits([_hit("down.example")], profile=profile, max_deep_read=1)
    assert rejected == 0
    assert len(out) == 1
    assert out[0].page_evidence is None


def test_hook_exception_is_fail_open(monkeypatch):
    _fake_fetch(monkeypatch, "любой текст страницы")

    class _BoomProfile(_PolicyProfile):
        def reject_page(self, text, *, url):
            raise RuntimeError("hook bug")

        def accept_domain(self, texts, *, domain):
            raise RuntimeError("hook bug")

    ev = read_domain_evidence(_hit("vendor.example"), profile=_BoomProfile())
    assert ev.rejected is False
    assert ev.pages != []


def test_profile_without_hooks_keeps_old_behavior(monkeypatch):
    _fake_fetch(monkeypatch, "положить в корзину розница рецепт")

    class _PlainProfile:
        name = "plain"
        ignored_domains: tuple[str, ...] = ()
        deep_paths: tuple[str, ...] = ("/",)

        def plan_iter1(self, query, *, llm, max_queries):
            return [query]

        def plan_iter2(self, hits, *, already_used, llm, max_queries):
            return []

        def extract_hints(self, text, *, url):
            return {}

        def score_confidence(self, pages, *, pages_with_hints, has_any_hint, contributing_excerpt_len=0):
            return None

    ev = read_domain_evidence(_hit("shop.example"), profile=_PlainProfile())
    assert ev.rejected is False
    assert ev.pages != []


def test_client_meta_rejected_count_and_profile(monkeypatch):
    hits = [_hit("a.example"), _hit("b.example")]

    def fake_discover(queries, **kw):
        return list(hits), ["stub"], ["stub"]

    monkeypatch.setattr(client_mod, "discover_for_queries", fake_discover)
    _fake_fetch(monkeypatch, {
        "https://a.example/": "опт прайс-лист отгрузка",
        "https://b.example/": "положить в корзину розница",
    })
    profile = _PolicyProfile(stop=["положить в корзину"], required=["прайс"])
    bundle = NexusSearchClient(profile=profile, adapters=[]).search(
        "мраморная говядина", NexusSearchOptions(enable_iter2=False)
    )
    assert [h.domain for h in bundle.hits] == ["a.example"]
    assert bundle.meta.rejected_count == 1
    assert bundle.meta.profile == "policy"
