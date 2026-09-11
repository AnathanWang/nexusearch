"""Extra discovery adapter tests."""

from nexusearch.discovery import TavilyAdapter, is_allowed_domain


def test_is_allowed_seen_blocks():
    assert not is_allowed_domain("a.example", {"a.example"}, ())


def test_tavily_without_key(monkeypatch):
    import nexusearch.discovery as disc

    monkeypatch.setattr(disc, "TavilyClient", object)
    hits, used = TavilyAdapter(api_key=None).discover("q")
    assert hits == []
    assert used is False
