"""Ops helpers: search result cache and wall-clock budget guard."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field

from nexusearch.models import SearchBundle


class SearchCache:
    """Thread-safe TTL cache keyed by (profile, query, options-digest)."""

    def __init__(self, ttl_seconds: float = 900.0, max_entries: int = 256) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be > 0")
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._store: dict[str, tuple[float, SearchBundle]] = {}

    @staticmethod
    def key(profile_name: str, query: str, options_digest: str = "") -> str:
        raw = f"{profile_name}|{query.strip().lower()}|{options_digest}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def get(self, key: str) -> SearchBundle | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            ts, bundle = entry
            if time.monotonic() - ts > self.ttl:
                self._store.pop(key, None)
                return None
            return bundle

    def set(self, key: str, bundle: SearchBundle) -> None:
        with self._lock:
            if key not in self._store and len(self._store) >= self.max_entries:
                oldest = min(self._store.items(), key=lambda kv: kv[1][0])[0]
                self._store.pop(oldest, None)
            self._store[key] = (time.monotonic(), bundle)


@dataclass
class Budget:
    """Simple wall-clock budget guard for a search run (0/negative rejected)."""

    max_seconds: float = 60.0
    _started: float = field(default_factory=time.monotonic)

    def __post_init__(self) -> None:
        if self.max_seconds <= 0:
            raise ValueError("max_seconds must be > 0")

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._started

    @property
    def expired(self) -> bool:
        return self.elapsed >= self.max_seconds

    def remaining(self) -> float:
        return max(0.0, self.max_seconds - self.elapsed)


@dataclass
class CostBudget:
    """Credits budget for paid APIs (thread-safe).

    Separate from the wall-clock Budget: charge() returns False (without
    charging) if the spend would exceed max_credits.
    """

    max_credits: float
    _spent: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        if self.max_credits <= 0:
            raise ValueError("max_credits must be > 0")

    def charge(self, credits: float = 1.0) -> bool:
        if credits <= 0:
            raise ValueError("credits must be > 0")
        with self._lock:
            if self._spent + credits > self.max_credits:
                return False
            self._spent += credits
            return True

    @property
    def spent(self) -> float:
        with self._lock:
            return self._spent

    @property
    def remaining(self) -> float:
        with self._lock:
            return max(0.0, self.max_credits - self._spent)
