"""
Model catalog cache with TTL and request coalescing.

Avoids hammering provider /models endpoints when multiple calls arrive
simultaneously or within the cache window. Uses SHA256(base_url) as the
cache key so distinct providers never collide.

Internal layout:
  _entries  — {key: (models, fetched_at)}  persists across calls
  _events   — {key: threading.Event}       exists only while a fill is in flight
"""
import hashlib
import threading
import time
from typing import Optional


_TTL = 30.0  # seconds before a cached entry is considered stale


class ModelCache:
    def __init__(self, ttl: float = _TTL):
        self._ttl = ttl
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[list, float]] = {}
        self._events: dict[str, threading.Event] = {}

    @staticmethod
    def _key(base_url: str) -> str:
        return hashlib.sha256(base_url.encode()).hexdigest()

    def get(self, base_url: str) -> Optional[list]:
        """Return cached models if fresh, else None."""
        key = self._key(base_url)
        with self._lock:
            entry = self._entries.get(key)
        if entry is not None:
            models, fetched_at = entry
            if time.monotonic() - fetched_at < self._ttl:
                return models
        return None

    def wait_or_claim(self, base_url: str) -> tuple[bool, Optional[list]]:
        """
        Try to claim ownership of a cache fill for base_url.

        Returns (is_owner, cached_models):
          - (True, None)    → caller must fetch and then call set()
          - (False, models) → another thread already fetched; use the result
        """
        key = self._key(base_url)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                models, fetched_at = entry
                if time.monotonic() - fetched_at < self._ttl:
                    return False, models
            # No fresh data — check if a fill is already in flight
            event = self._events.get(key)
            if event is None:
                # Become leader: register an event and return
                self._events[key] = threading.Event()
                return True, None
            # Another thread is filling — hold a local ref before dropping lock

        # Wait outside the lock
        event.wait(timeout=10.0)
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                return False, entry[0]
            # Timed out with no result — become the new leader
            self._events[key] = threading.Event()
        return True, None

    def set(self, base_url: str, models: list) -> None:
        """Store fetched models and signal any waiting threads."""
        key = self._key(base_url)
        with self._lock:
            self._entries[key] = (models, time.monotonic())
            event = self._events.pop(key, None)
        if event is not None:
            event.set()

    def invalidate(self, base_url: str) -> None:
        key = self._key(base_url)
        with self._lock:
            self._entries.pop(key, None)
            self._events.pop(key, None)

    def invalidate_all(self) -> None:
        with self._lock:
            self._entries.clear()
            self._events.clear()


def _key(base_url: str) -> str:
    """Module-level alias for ModelCache._key — used by tests."""
    return ModelCache._key(base_url)


# Module-level singleton
cache = ModelCache()
