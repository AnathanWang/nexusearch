"""Circuit breaker: after N consecutive failures stay open M seconds."""

from __future__ import annotations

import threading
import time


class CircuitBreaker:
    """Per-adapter breaker. Closed -> calls pass; open -> calls rejected
    until cooldown elapses, than half-open (one trial call)."""

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 60.0) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be > 0")
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures = 0
        self._opened_at: float | None = None
        self._lock =  threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if time.monotonic() - self._opened_at >= self.cooldown_seconds:
                self._opened_at = None #Half-open: let one call through
                self._failures = 0
                return True
            return False
    
    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
    
    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold and self._opened_at is None:
                self._opened_at = time.monotonic()