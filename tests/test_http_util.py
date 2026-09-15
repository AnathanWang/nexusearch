"""http_util tests: retry policy, status handling, backoff schedule."""

import httpx
import pytest

from nexusearch.http_util import backoff_delay, request_with_retries, with_retries


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code
        self.request = httpx.Request("GET", "https://x.example")


class _FakeClient:
    def __init__(self, statuses: list[int]):
        self.statuses = list(statuses)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        return _FakeResponse(self.statuses.pop(0))


def test_with_retries_first_try():
    assert with_retries(lambda: "ok", attempts=3, base_delay=0.01) == "ok"


def test_with_retries_recovers_after_timeout():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.TimeoutException("slow")
        return "ok"

    assert with_retries(flaky, attempts=3, base_delay=0.01) == "ok"
    assert calls["n"] == 3


def test_with_retries_exhausted_raises():
    def always_fail():
        raise httpx.TimeoutException("slow")

    with pytest.raises(httpx.TimeoutException):
        with_retries(always_fail, attempts=2, base_delay=0.01)


def test_with_retries_non_retryable_status_raises_immediately():
    resp = _FakeResponse(404)
    err = httpx.HTTPStatusError("not found", request=resp.request, response=resp)
    calls = {"n": 0}

    def fail_404():
        calls["n"] += 1
        raise err

    with pytest.raises(httpx.HTTPStatusError):
        with_retries(fail_404, attempts=3, base_delay=0.01)
    assert calls["n"] == 1  # no retries on 404


def test_request_with_retries_retryable_status_then_ok():
    client = _FakeClient([429, 200])
    resp = request_with_retries(client, "GET", "https://x.example", attempts=3, base_delay=0.01)
    assert resp.status_code == 200
    assert client.calls == 2


def test_request_with_retries_persistent_429_returns_last_response():
    client = _FakeClient([429, 429, 429])
    resp = request_with_retries(client, "GET", "https://x.example", attempts=3, base_delay=0.01)
    assert resp.status_code == 429
    assert client.calls == 3


def test_backoff_delay_schedule():
    assert backoff_delay(0) == pytest.approx(0.35)
    assert backoff_delay(1) == pytest.approx(0.70)
    assert backoff_delay(2) == pytest.approx(1.40)
