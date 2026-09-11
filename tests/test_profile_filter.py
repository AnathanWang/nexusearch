"""Profile-level geo filter (allowed_domains / url_path_patterns) in client."""

from __future__ import annotations

import nexusearch.client as client_mod
from nexusearch.client import NexusSearchClient, _apply_profile_geo_filter
from nexusearch.models import NexusSearchOptions, SearchHit
from nexusearch.simple_profile import SimpleSearchProfile


def _hit(url: str) -> SearchHit:
    return SearchHit(title=url, url=url, snippet="", domain="")


def _profile(**kw) -> SimpleSearchProfile:
    kw.setdefault("name", "geo")
    kw.setdefault("query_templates", ["{query} опт"])
    return SimpleSearchProfile(**kw)


def test_empty_config_is_noop():
    hits = [_hit("https://a.example/"), _hit("https://b.example/opt")]
    assert _apply_profile_geo_filter(hits, _profile()) is hits


def test_allowed_domains_whitelist_exact_and_subdomain():
    hits = [
        _hit("https://b2b-center.ru/catalog"),
        _hit("https://www.b2b-center.ru/catalog"),
        _hit("https://notb2b-center.ru/fake"),
        _hit("https://supl.biz/opt"),
        _hit("https://random.example/"),
    ]
    out = _apply_profile_geo_filter(hits, _profile(allowed_domains=["b2b-center.ru", "supl.biz"]))
    urls = [h.url for h in out]
    assert urls == [
        "https://b2b-center.ru/catalog",
        "https://www.b2b-center.ru/catalog",
        "https://supl.biz/opt",
    ]


def test_url_path_patterns_filter():
    hits = [
        _hit("https://a.example/wholesale/catalog"),
        _hit("https://b.example/"),
        _hit("https://c.example/opt/pricelist"),
        _hit("https://d.example/blog/post"),
    ]
    out = _apply_profile_geo_filter(hits, _profile(url_path_patterns=[r"/(wholesale|opt|b2b)"]))
    assert [h.url for h in out] == [
        "https://a.example/wholesale/catalog",
        "https://c.example/opt/pricelist",
    ]


def test_whitelist_and_path_patterns_combine():
    hits = [
        _hit("https://b2b-center.ru/wholesale"),
        _hit("https://b2b-center.ru/blog"),
        _hit("https://other.example/wholesale"),
    ]
    out = _apply_profile_geo_filter(
        hits,
        _profile(allowed_domains=["b2b-center.ru"], url_path_patterns=[r"/wholesale"]),
    )
    assert [h.url for h in out] == ["https://b2b-center.ru/wholesale"]


def test_invalid_pattern_skipped_not_fatal():
    # SimpleSearchProfile validates patterns at init; the client-side guard
    # covers custom profiles carrying raw attrs.
    class _RawProfile:
        name = "raw"
        url_path_patterns = ("([unclosed", r"/opt")
        allowed_domains: tuple[str, ...] = ()

    hits = [_hit("https://a.example/opt")]
    out = _apply_profile_geo_filter(hits, _RawProfile())
    assert [h.url for h in out] == ["https://a.example/opt"]


def test_client_applies_geo_filter(monkeypatch):
    hits = [
        _hit("https://b2b-center.ru/catalog"),
        _hit("https://eda.ru/recipe"),
    ]

    def fake_discover(queries, **kw):
        return list(hits), ["stub"], ["stub"]

    monkeypatch.setattr(client_mod, "discover_for_queries", fake_discover)
    client = NexusSearchClient(
        profile=_profile(allowed_domains=["b2b-center.ru"]),
        adapters=[],
    )
    bundle = client.search("говядина", NexusSearchOptions(deep_read=False, enable_iter2=False))
    assert [h.url for h in bundle.hits] == ["https://b2b-center.ru/catalog"]
    assert bundle.meta.profile == "geo"
