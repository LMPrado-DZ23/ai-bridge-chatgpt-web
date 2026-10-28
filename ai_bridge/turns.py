"""
Turn tracking and drain mode.

Each in-flight request gets a UUID turn_id. The TurnTracker lets admin
endpoints cancel individual turns or all turns, and enables a "drain"
mode that blocks new turns while existing ones finish (used for graceful
shutdown).
"""
import threading
import time
import uuid
from typing import Optional


class TurnTracker:
    def __init__(self):
        self._lock = threading.Lock()
        self._turns: dict[str, threading.Event] = {}
        self._drain = threading.Event()

    # ── Turn lifecycle ────────────────────────────────────────────────────────

    def begin(self) -> Optional[str]:
        """Register a new turn. Returns None if drain mode is active."""
        if self._drain.is_set():
            return None
        turn_id = str(uuid.uuid4())
        with self._lock:
            self._turns[turn_id] = threading.Event()
        return turn_id

    def end(self, turn_id: str) -> None:
        """Unregister a completed or cancelled turn."""
        with self._lock:
            self._turns.pop(turn_id, None)

    # ── Cancellation ─────────────────────────────────────────────────────────

    def cancel(self, turn_id: str) -> bool:
        """Signal cancellation for a specific turn. Returns False if not found."""
        with self._lock:
            ev = self._turns.get(turn_id)
        if ev:
            ev.set()
            return True
        return False

    def cancel_all(self) -> int:
        """Signal cancellation for all active turns. Returns count cancelled."""
        with self._lock:
            turns = list(self._turns.values())
        for ev in turns:
            ev.set()
        return len(turns)

    def is_cancelled(self, turn_id: str) -> bool:
        """Check whether a specific turn has been cancelled."""
        with self._lock:
            ev = self._turns.get(turn_id)
        return ev.is_set() if ev else True

    # ── Inspection ───────────────────────────────────────────────────────────

    def active_count(self) -> int:
        with self._lock:
            return len(self._turns)

    def active_ids(self) -> list[str]:
        with self._lock:
            return list(self._turns.keys())

    # ── Drain mode ───────────────────────────────────────────────────────────

    def start_drain(self) -> None:
        """Enter drain mode: reject new turns, wait for existing ones to finish."""
        self._drain.set()

    def stop_drain(self) -> None:
        """Exit drain mode: allow new turns again."""
        self._drain.clear()

    def is_draining(self) -> bool:
        return self._drain.is_set()

    def wait_empty(self, timeout: float = 30.0) -> bool:
        """Block until all active turns complete or timeout. Returns True if drained."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.active_count() == 0:
                return True
            time.sleep(0.05)
        return self.active_count() == 0


# Module-level singleton — imported by proxy.py
tracker = TurnTracker()
