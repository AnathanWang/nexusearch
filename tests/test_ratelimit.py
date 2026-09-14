"""Ratelimiter tests: burst, refill, thread-safety."""

import threading
import time

import pytest

from nexusearch.ratelimit import RateLimiter


def test_burst_than_exhausted():
    limiter = RateLimiter(2, per_seconds=60.0)
    assert limiter.acquire(timeout = 0.01) is True
    assert limiter.acquire(timeout = 0.01) is True
    assert limiter.acquire(timeout = 0.01) is False

def test_refill_over_time():
    limiter = RateLimiter(2, per_seconds=0.2)
    assert limiter.acquire() is True
    assert limiter.acquire() is True
    time.sleep(0.25)
    assert limiter.acquire(timeout = 0.2) is True

def test_thread_safety_no_overissue():
    limiter = RateLimiter(5, per_seconds=60.0)
    taken: list[bool] = []

    def worker():
        taken.append(limiter.acquire(timeout = 0.01))
    
    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(taken) == 5

def test_validation():
    with pytest.raises(ValueError):
        RateLimiter(0)
    with pytest.raises(ValueError):
        RateLimiter(1, per_seconds=0)