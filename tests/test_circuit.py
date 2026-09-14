import time

import pytest

from nexusearch.circuit import CircuitBreaker


def test_opens_after_threshold():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60.0)
    for _ in range(3):
        assert cb.allow() is True
        cb.record_failure()
    assert cb.allow() is False

def test_success_resets():
    cb = CircuitBreaker(failure_threshold=3, cooldown_seconds=60.0)
    cb.record_failure()
    cb.record_success()
    cb.record_failure()
    assert cb.allow() is True

def test_half_open_after_cooldown():
    cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.1)
    cb.record_failure()
    assert cb.allow() is False
    time.sleep(0.15)
    assert cb.allow() is True

def test_validation():
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(cooldown_seconds=0)