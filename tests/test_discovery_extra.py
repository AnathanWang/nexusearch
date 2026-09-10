"""Extra discovery adapter tests."""

from nexusearch.discovery import is_allowed_domain, search_tavily


def test_is_allowed_seen_blocks():
    assert not is_allowed_domain("a.example", {"a.example"}, ())


def test_tavily_without_key(monkeypatch):
    import nexusearch.discovery as disc

    monkeypatch.setattr(disc, "TavilyClient", object)
    hits, used = search_tavily("q", api_key=None)
    assert hits == []
    assert used is False
