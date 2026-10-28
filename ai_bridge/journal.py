"""Versioned journal — 5-file atomic snapshot with rollback.

Each snapshot is a numbered JSON file written atomically.  Keeps at most
MAX_SNAPSHOTS files; the oldest is deleted before a new one is written.
"""
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from . import config as cfg

log = logging.getLogger(__name__)

MAX_SNAPSHOTS = 5
_JOURNAL_DIR = cfg.CONFIG_DIR / "journal"
_lock = threading.Lock()


def _snapshot_paths() -> list[Path]:
    """Return existing snapshot paths sorted oldest→newest by filename number."""
    if not _JOURNAL_DIR.exists():
        return []
    paths = sorted(_JOURNAL_DIR.glob("snapshot-*.json"))
    return paths


def _next_index() -> int:
    paths = _snapshot_paths()
    if not paths:
        return 1
    # extract number from last file
    try:
        last = int(paths[-1].stem.split("-")[1])
    except (IndexError, ValueError):
        last = len(paths)
    return last + 1


def save(label: str = "") -> Optional[Path]:
    """Write a new snapshot of the current config.

    Returns the path of the written snapshot, or None on failure.
    """
    with _lock:
        try:
            _JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
            existing = _snapshot_paths()
            # trim to MAX_SNAPSHOTS - 1 before adding new one
            while len(existing) >= MAX_SNAPSHOTS:
                existing[0].unlink(missing_ok=True)
                existing = existing[1:]

            idx = _next_index()
            path = _JOURNAL_DIR / f"snapshot-{idx:06d}.json"
            tmp = path.with_name(path.name + f".tmp-{os.getpid()}")

            data = cfg.load()
            entry = {"index": idx, "label": label, "config": data}
            tmp.write_text(json.dumps(entry, indent=2, ensure_ascii=False), "utf-8")
            tmp.replace(path)
            log.debug("journal: saved snapshot %d (%s)", idx, path.name)
            return path
        except Exception:
            log.exception("journal: failed to save snapshot")
            return None


def list_snapshots() -> list[dict]:
    """Return a list of snapshot metadata dicts (index, label, path)."""
    result = []
    with _lock:
        for p in _snapshot_paths():
            try:
                raw = json.loads(p.read_text("utf-8"))
                result.append({
                    "index": raw.get("index"),
                    "label": raw.get("label", ""),
                    "path": str(p),
                })
            except Exception:
                result.append({"index": None, "label": "", "path": str(p)})
    return result


def rollback(index: int) -> bool:
    """Restore config from a previous snapshot by index.

    Returns True on success, False if the snapshot was not found or failed.
    """
    with _lock:
        for p in _snapshot_paths():
            try:
                raw = json.loads(p.read_text("utf-8"))
            except Exception:
                continue
            if raw.get("index") == index:
                try:
                    cfg.save(raw["config"])
                    log.info("journal: rolled back to snapshot %d", index)
                    return True
                except Exception:
                    log.exception("journal: rollback to %d failed", index)
                    return False
        log.warning("journal: snapshot %d not found", index)
        return False


def latest() -> Optional[dict]:
    """Return the most recent snapshot dict, or None."""
    with _lock:
        paths = _snapshot_paths()
        if not paths:
            return None
        try:
            return json.loads(paths[-1].read_text("utf-8"))
        except Exception:
            return None
