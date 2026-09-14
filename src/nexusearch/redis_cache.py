"""Multi-instance cache backend. Requires the optional `redis` package."""

from __future__ import annotations

from nexusearch.models import SearchBundle
from nexusearch.ops import SearchCache


class RedisSearchCache:
    """Same key format and TTL semantics as SearchCache, stored in Redis."""

    def __init__(self, url: str, *, ttl_seconds: int = 900, prefix: str = "nexusearch:") -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        import redis

        self._client = redis.Redis.from_url(url)
        self.ttl_seconds = ttl_seconds
        self.prefix = prefix
    
    @staticmethod
    def key(profile_name: str, query: str, options_digest: str = "") -> str:
        return SearchCache.key(profile_name, query, options_digest)

    def get(self, key: str) -> SearchBundle | None:
        raw = self._client.get(self.prefix + key)
        if raw is None:
            return None
        return SearchBundle.model_validate_json(raw)
    
    def set(self, key: str, bundle: SearchBundle) -> None:
        self._client.set(self.prefix + key, bundle.model_dump_json(), ex=self.ttl_seconds)
