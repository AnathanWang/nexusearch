"""Per-adapter rate-limiting: thread-safe token bucket."""

from __future__ import annotations

import threading
import time


class RateLimiter:
    """Token bucket: `rate` calls per `per_seconds`, safe across threads.
    
    Shared limiter instance =  shared budget (parallel_adapters runs adapters 
    in a thread pool, so the lock matters).
    """

    def __init__(self, rate: int, per_seconds: float = 60.0) -> None:
        if rate < 1:
            raise ValueError("rate must be >= 1")
        if per_seconds <= 0:
            raise ValueError("per_seconds must be > 0")
        self.rate = rate
        self.per_seconds = per_seconds
        self._tokens = float(rate)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 10.0) -> bool:
        """Take one token, waiting if needed. False if not taken in `timeout`. """
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                now = time.monotonic()
                refill = (now - self._updated) * self.rate / self.per_seconds
                self._tokens = min(float(self.rate), self._tokens + refill)
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                wait = (1.0 - self._tokens) * self.per_seconds / self.rate
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 0.05))

