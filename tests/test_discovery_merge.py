"""Core discovery merge / ignore matching (profile supplies ignore list)."""

from nexusearch.discovery import extract_domain, is_allowed_domain, merge_hits
from nexusearch.models import SearchHit

IGNORED = ("amazon.", "x.com", "thomasnet.com")


def test_extract_domain():
    assert extract_domain("https://www.Example.com/path") == "example.com"


def test_is_allowed_ignores_marketplace_prefix():
    assert not is_allowed_domain("amazon.com", set(), IGNORED)
    assert not is_allowed_domain("amazon.co.uk", set(), IGNORED)
    assert is_allowed_domain("notamazon.com", set(), IGNORED)


def test_is_allowed_exact_host():
    assert not is_allowed_domain("x.com", set(), IGNORED)
    assert not is_allowed_domain("foo.x.com", set(), IGNORED)
    assert is_allowed_domain("prefixx.com", set(), IGNORED)


def test_merge_hits_dedupes_and_caps():
    existing = [SearchHit(domain="a.example", url="https://a.example", title="A")]
    new = [
        SearchHit(domain="a.example", url="https://a.example/x", title="A2"),
        SearchHit(domain="amazon.com", url="https://amazon.com/x", title="Bad"),
        SearchHit(domain="b.example", url="https://b.example", title="B"),
    ]
    out = merge_hits(existing, new, max_hits=2, ignored_domains=IGNORED)
    assert [h.domain for h in out] == ["a.example", "b.example"]
