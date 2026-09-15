"""RedisSearchCache tests with a stubbed redis client (no server needed)."""

import pytest

from nexusearch.models import SearchBundle, SearchHit, SearchMeta
from nexusearch.ops import SearchCache
from nexusearch.redis_cache import RedisSearchCache


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        self.ttls[key] = ex


def _bundle() -> SearchBundle:
    return SearchBundle(
        hits=[SearchHit(title="t", url="https://a.example", domain="a.example")],
        meta=SearchMeta(iterations_run=1, queries_used=["q"], profile="p"),
    )


def _cache(monkeypatch) -> tuple[RedisSearchCache, _FakeRedis]:
    import redis

    fake = _FakeRedis()
    monkeypatch.setattr(redis.Redis, "from_url", classmethod(lambda cls, url: fake))
    return RedisSearchCache("redis://localhost:6379/0", ttl_seconds=60), fake


def test_roundtrip(monkeypatch):
    cache, _ = _cache(monkeypatch)
    key = RedisSearchCache.key("p", "Some Query")
    assert cache.get(key) is None

    cache.set(key, _bundle())
    out = cache.get(key)
    assert out is not None
    assert out.hits[0].domain == "a.example"
    assert out.meta.profile == "p"


def test_ttl_and_prefix(monkeypatch):
    cache, fake = _cache(monkeypatch)
    key = RedisSearchCache.key("p", "q")
    cache.set(key, _bundle())
    assert fake.ttls["nexusearch:" + key] == 60
    assert "nexusearch:" + key in fake.store


def test_key_format_matches_search_cache():
    assert RedisSearchCache.key("p", "Q", "digest") == SearchCache.key("p", "Q", "digest")


def test_ttl_validation():
    with pytest.raises(ValueError):
        RedisSearchCache("redis://localhost:6379/0", ttl_seconds=0)
