"""ExpoAdapter tests."""

from nexusearch.adapters_expo import ExpoAdapter
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


def test_expo_adapter_filters_and_relabels():
    backend = _FakeBackend([
        _hit("https://www.10times.com/camping-expo"),
        _hit("https://randomblog.example/post/1"),
        _hit("https://acme.example/exhibitors"),
        _hit("https://messefrankfurt.com/some-show"),
    ])
    expo = ExpoAdapter(backend=backend)
    hits, used = expo.discover("camping furniture")
    assert used is True
    assert backend.queries == ["camping furniture trade show exhibitors list"]
    urls = [h.url for h in hits]
    assert "https://www.10times.com/camping-expo" in urls
    assert "https://acme.example/exhibitors" in urls
    assert "https://messefrankfurt.com/some-show" in urls
    assert "https://randomblog.example/post/1" not in urls
    assert all(h.channel == "expo" and h.source_adapter == "expo" for h in hits)


def test_expo_adapter_backend_down():
    class _Down:
        name = "down"
        channel = "serp"

        def discover(self, query, **kw):
            return [], False

    hits, used = ExpoAdapter(backend=_Down()).discover("q")
    assert hits == []
    assert used is False
