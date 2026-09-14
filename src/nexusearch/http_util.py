"""Shared HTTP helpers: retries with jitter."""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})


def backoff_delay(attempt: int, *, base: float = 0.35) -> float:
    """Exponential backoff for manual retry loops (with_retries adds its own jitter)."""
    return base * (2**attempt)


def with_retries(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.35,
    retry_exceptions: tuple[type[BaseException], ...] = (
        httpx.TimeoutException,
        httpx.NetworkError,
        httpx.RemoteProtocolError,
    ),
) -> T:
    """
    Run fn up to `attempts` times. Retries on transport errors and when fn raises
    httpx.HTTPStatusError with a retryable status. Caller may also return and
    inspect status itself — use `request_with_retries` for that path.
    """
    last_exc: BaseException | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except httpx.HTTPStatusError as e:
            last_exc = e
            if e.response.status_code not in _RETRY_STATUS or i >= attempts - 1:
                raise
        except retry_exceptions as e:
            last_exc = e
            if i >= attempts - 1:
                raise
        delay = base_delay * (2**i) + random.uniform(0, 0.15)
        logger.debug("Retry %s/%s after %s (delay=%.2fs)", i + 1, attempts, last_exc, delay)
        time.sleep(delay)
    assert last_exc is not None
    raise last_exc


def request_with_retries(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = 3,
    base_delay: float = 0.35,
    **kwargs,
) -> httpx.Response:
    """GET/POST with retries on 429/5xx and transport errors."""

    def _once() -> httpx.Response:
        resp = client.request(method, url, **kwargs)
        if resp.status_code in _RETRY_STATUS:
            # Raise so with_retries can back off; re-raise final attempt below.
            raise httpx.HTTPStatusError(
                f"retryable status {resp.status_code}",
                request=resp.request,
                response=resp,
            )
        return resp

    try:
        return with_retries(_once, attempts=attempts, base_delay=base_delay)
    except httpx.HTTPStatusError as e:
        return e.response
