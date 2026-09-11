"""DomainChannelAdapter tests (declarative consumer channel)."""

import pytest

from nexusearch.channel_adapter import DomainChannelAdapter
from nexusearch.discovery import DiscoveryAdapter, extract_domain
from nexusearch.models import SearchHit


class _FakeBackend:
    name = "fake_serp"
    channel = "serp"

    def __init__(self, hits) -> None:
        self._hits = hits
        self.queries: list[str] = []

    def discover(self, query, *, max_results=10, iteration=1, ignored_domains=()):
        self.queries.append(query)
        return self._hits, True


def _hit(url: str) -> SearchHit:
    return SearchHit(url=url, title=url, snippet="", domain="")


def _expo(**kw) -> DomainChannelAdapter:
    return DomainChannelAdapter(
        name="expo",
        query_templates=["{query} trade show exhibitors list"],
        allowed_domains=["10times.com", "messefrankfurt.com"],
        path_patterns=[r"(?:^|[/._-])(expos?|exhibitors?)(?:[/._-]|$)"],
        **kw,
    )


def test_satisfies_protocol():
    assert isinstance(_expo(backend=_FakeBackend([])), DiscoveryAdapter)


def test_filters_and_relabels():
    backend = _FakeBackend([
        _hit("https://www.10times.com/camping-expo"),
        _hit("https://randomblog.example/post/1"),
        _hit("https://acme.example/exhibitors"),
        _hit("https://messefrankfurt.com/some-show"),
    ])
    adapter = _expo(backend=backend)
    hits, used = adapter.discover("camping furniture")
    assert used is True
    assert backend.queries == ["camping furniture trade show exhibitors list"]
    urls = [h.url for h in hits]
    assert "https://www.10times.com/camping-expo" in urls
    assert "https://acme.example/exhibitors" in urls
    assert "https://messefrankfurt.com/some-show" in urls
    assert "https://randomblog.example/post/1" not in urls
    assert all(h.channel == "expo" and h.source_adapter == "expo" for h in hits)
    assert all(h.discovery_query == "camping furniture trade show exhibitors list" for h in hits)
    assert all(h.domain for h in hits)


def test_whitelist_matches_subdomains_only():
    backend = _FakeBackend([
        _hit("https://sub.10times.com/event"),
        _hit("https://not10times.com/event"),
        _hit("https://10times.com.evil.example/event"),
    ])
    hits, _ = _expo(backend=backend).discover("q")
    assert [h.url for h in hits] == ["https://sub.10times.com/event"]


def test_path_boundaries_no_false_positives():
    backend = _FakeBackend([
        _hit("https://a.example/fair-trade-coffee"),
        _hit("https://b.example/blog/exponential-growth"),
        _hit("https://c.example/exposure-tips"),
        _hit("https://d.example/exhibitors"),
        _hit("https://e.example/expo-2027"),
    ])
    hits, _ = _expo(backend=backend).discover("coffee", max_results=10)
    urls = [h.url for h in hits]
    assert "https://d.example/exhibitors" in urls
    assert "https://e.example/expo-2027" in urls
    assert not any("fair-trade" in u or "exponential" in u or "exposure" in u for u in urls)


def test_domain_dedupe():
    backend = _FakeBackend([
        _hit("https://10times.com/expo-a"),
        _hit("https://10times.com/expo-b"),
        _hit("https://messefrankfurt.com/show"),
    ])
    hits, _ = _expo(backend=backend).discover("q", max_results=10)
    assert [h.url for h in hits] == [
        "https://10times.com/expo-a",
        "https://messefrankfurt.com/show",
    ]


def test_max_results_respected():
    backend = _FakeBackend([_hit(f"https://10times.com/expo-{i}") for i in range(10)])
    # Same domain dedupes to 1, so use distinct whitelisted subdomains.
    backend._hits = [_hit(f"https://sub{i}.10times.com/expo") for i in range(10)]
    hits, _ = _expo(backend=backend).discover("q", max_results=3)
    assert len(hits) == 3


def test_backend_down_propagates_attempted_ok_false():
    class _Down:
        name = "down"
        channel = "serp"

        def discover(self, query, **kw):
            return [], False

    hits, used = _expo(backend=_Down()).discover("q")
    assert hits == []
    assert used is False


def test_multiple_templates_until_filled():
    first = _FakeBackend([])
    adapter = DomainChannelAdapter(
        name="expo",
        query_templates=["{query} expo", "{query} exhibitors list"],
        allowed_domains=["10times.com"],
        backend=first,
    )
    hits, used = adapter.discover("camping")
    assert used is True
    assert first.queries == ["camping expo", "camping exhibitors list"]
    assert hits == []

    second = _FakeBackend([_hit("https://10times.com/a")])
    adapter = DomainChannelAdapter(
        name="expo",
        query_templates=["{query} expo", "{query} exhibitors list"],
        allowed_domains=["10times.com"],
        backend=second,
    )
    hits, _ = adapter.discover("camping", max_results=1)
    assert len(hits) == 1
    assert second.queries == ["camping expo"]  # filled after first template


def test_template_with_other_braces_does_not_crash():
    backend = _FakeBackend([_hit("https://10times.com/a")])
    adapter = DomainChannelAdapter(
        name="expo",
        query_templates=['{query} expo "a{b}"'],
        allowed_domains=["10times.com"],
        backend=backend,
    )
    hits, _ = adapter.discover("camping")
    assert backend.queries == ['camping expo "a{b}"']
    assert len(hits) == 1


def test_config_validation():
    with pytest.raises(ValueError, match="non-empty name"):
        DomainChannelAdapter(name=" ", query_templates=["{query}"], allowed_domains=["a.com"])
    with pytest.raises(ValueError, match="at least one query template"):
        DomainChannelAdapter(name="x", query_templates=[], allowed_domains=["a.com"])
    with pytest.raises(ValueError, match="\\{query\\}"):
        DomainChannelAdapter(name="x", query_templates=["no placeholder"], allowed_domains=["a.com"])
    with pytest.raises(ValueError, match="allowed_domains and/or path_patterns"):
        DomainChannelAdapter(name="x", query_templates=["{query}"])
    with pytest.raises(ValueError, match="oversample"):
        DomainChannelAdapter(
            name="x", query_templates=["{query}"], allowed_domains=["a.com"], oversample=0
        )


def test_custom_channel_name():
    adapter = DomainChannelAdapter(
        name="expo-ru",
        channel="expo",
        query_templates=["{query} выставка"],
        path_patterns=["expo"],
        backend=_FakeBackend([_hit("https://e.example/expo-2027")]),
    )
    hits, _ = adapter.discover("кемпинг")
    assert hits[0].source_adapter == "expo-ru"
    assert hits[0].channel == "expo"


def test_extract_domain_used_when_hit_domain_empty():
    backend = _FakeBackend([_hit("https://www.10times.com/expo")])
    hits, _ = _expo(backend=backend).discover("q")
    assert hits[0].domain == extract_domain("https://www.10times.com/expo")
