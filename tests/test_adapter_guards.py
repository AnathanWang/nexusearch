"""Adapter guard wiring: rate_limiter and circuit_breaker in discovery adapters."""

from nexusearch import discovery
from nexusearch.circuit import CircuitBreaker
from nexusearch.discovery import BraveAdapter, SerpApiAdapter
from nexusearch.ratelimit import RateLimiter


def test_exhausted_limiter_skips_call(monkeypatch):
    called = []

    def fake_request(*args, **kwargs):
        called.append(True)
        raise AssertionError("network must not be touched when limiter is exhausted")

    monkeypatch.setattr(discovery, "request_with_retries", fake_request)
    limiter = RateLimiter(1, per_seconds=60.0)
    assert limiter.acquire()  # exhaust the single token

    adapter = BraveAdapter("test-key", rate_limiter=limiter)
    hits, ok = adapter.discover("query", max_results=3)
    assert hits == []
    assert ok is False
    assert not called


def test_breaker_records_success_and_failure(monkeypatch):
    class Resp:
        status_code = 200

        def json(self):
            return {"organic_results": []}

    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0)
    adapter = SerpApiAdapter("test-key", circuit_breaker=breaker)

    monkeypatch.setattr(discovery, "request_with_retries", lambda *a, **k: Resp())
    hits, ok = adapter.discover("query", max_results=3)
    assert ok is True
    assert breaker._failures == 0

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(discovery, "request_with_retries", boom)
    hits, ok = adapter.discover("query", max_results=3)
    assert (hits, ok) == ([], False)
    assert breaker._failures == 1

    # Second consecutive failure opens the breaker.
    adapter.discover("query", max_results=3)
    assert breaker.allow() is False


def test_success_path_returns_hits_with_breaker(monkeypatch):
    """Regression: breaker attached must not swallow successful hits."""

    class Resp:
        status_code = 200

        def json(self):
            return {
                "organic_results": [
                    {"title": "Acme", "link": "https://acme.example/about", "snippet": "CNC"}
                ]
            }

    adapter = SerpApiAdapter("test-key", circuit_breaker=CircuitBreaker())
    monkeypatch.setattr(discovery, "request_with_retries", lambda *a, **k: Resp())
    hits, ok = adapter.discover("query", max_results=3)
    assert ok is True
    assert [h.domain for h in hits] == ["acme.example"]
