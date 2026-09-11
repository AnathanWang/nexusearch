"""Ops helpers tests."""

import time

from nexusearch.models import SearchBundle, SearchHit
from nexusearch.ops import Budget, SearchCache


def _bundle(domain: str = "a.example") -> SearchBundle:
    return SearchBundle(hits=[SearchHit(url=f"https://{domain}/", domain=domain)])


def test_cache_roundtrip_and_key_stability():
    cache = SearchCache(ttl_seconds=60)
    k1 = SearchCache.key("supplier", " CNC Machining ")
    k2 = SearchCache.key("supplier", "cnc machining")
    assert k1 == k2
    b = _bundle()
    cache.set(k1, b)
    assert cache.get(k1) is b
    assert cache.get("missing") is None


def test_cache_ttl_expiry():
    cache = SearchCache(ttl_seconds=0.05)
    k = SearchCache.key("p", "q")
    cache.set(k, _bundle())
    time.sleep(0.08)
    assert cache.get(k) is None


def test_cache_evicts_oldest():
    cache = SearchCache(ttl_seconds=60, max_entries=2)
    cache.set("a", _bundle("a.example"))
    time.sleep(0.01)
    cache.set("b", _bundle("b.example"))
    time.sleep(0.01)
    cache.set("c", _bundle("c.example"))
    assert cache.get("a") is None
    assert cache.get("b") is not None
    assert cache.get("c") is not None


def test_budget_expiry():
    budget = Budget(max_seconds=0.05)
    assert not budget.expired
    time.sleep(0.08)
    assert budget.expired
    assert budget.remaining() == 0.0
