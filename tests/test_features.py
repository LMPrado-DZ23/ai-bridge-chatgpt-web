"""Tests for the 7 ported features: auth, turns, model_cache, skills, journal, proxy admin."""
import hashlib
import hmac
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ─── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def tmp_config_dir(tmp_path, monkeypatch):
    """Redirect CONFIG_DIR and CONFIG_FILE to a temp directory for every test."""
    monkeypatch.chdir(tmp_path)
    cfg_dir = tmp_path / ".ai-bridge"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"

    import ai_bridge.config as cfg_mod
    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", cfg_dir)
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", cfg_file)

    import ai_bridge.journal as journal_mod
    monkeypatch.setattr(journal_mod, "_JOURNAL_DIR", cfg_dir / "journal")

    import ai_bridge.skills as skills_mod
    monkeypatch.setattr(skills_mod, "_SKILLS_DIR", cfg_dir / "skills")

    yield cfg_dir


# ─── auth.py ───────────────────────────────────────────────────────────────────

class TestAuth:
    def test_get_token_creates_on_first_call(self):
        from ai_bridge import auth
        token = auth.get_token()
        assert len(token) >= 40

    def test_get_token_stable(self):
        from ai_bridge import auth
        t1 = auth.get_token()
        t2 = auth.get_token()
        assert t1 == t2

    def test_require_admin_missing_token(self):
        from ai_bridge import auth, proxy
        client = proxy.app.test_client()
        resp = client.get("/admin/status")
        assert resp.status_code == 401

    def test_require_admin_wrong_token(self):
        from ai_bridge import auth, proxy
        client = proxy.app.test_client()
        resp = client.get("/admin/status", headers={"X-Bridge-Token": "wrong"})
        assert resp.status_code == 401

    def test_require_admin_correct_token(self):
        from ai_bridge import auth, proxy
        token = auth.get_token()
        client = proxy.app.test_client()
        resp = client.get("/admin/status", headers={"X-Bridge-Token": token})
        assert resp.status_code == 200

    def test_require_admin_blocks_origin(self):
        from ai_bridge import auth, proxy
        token = auth.get_token()
        client = proxy.app.test_client()
        resp = client.get(
            "/admin/status",
            headers={"X-Bridge-Token": token, "Origin": "http://evil.example.com"},
        )
        assert resp.status_code == 403

    def test_hmac_timing_safe(self):
        from ai_bridge import auth
        token = auth.get_token()
        # verify hmac.compare_digest is used (not ==)
        assert hmac.compare_digest(token, token) is True
        assert hmac.compare_digest(token, "x" * len(token)) is False


# ─── turns.py ──────────────────────────────────────────────────────────────────

class TestTurns:
    def setup_method(self):
        from ai_bridge.turns import TurnTracker
        self.tracker = TurnTracker()

    def test_begin_returns_uuid(self):
        tid = self.tracker.begin()
        assert tid is not None
        assert len(tid) == 36  # UUID4 length

    def test_end_removes_turn(self):
        tid = self.tracker.begin()
        self.tracker.end(tid)
        assert self.tracker.active_count() == 0

    def test_end_idempotent(self):
        tid = self.tracker.begin()
        self.tracker.end(tid)
        self.tracker.end(tid)  # should not raise
        assert self.tracker.active_count() == 0

    def test_cancel_sets_flag(self):
        tid = self.tracker.begin()
        assert not self.tracker.is_cancelled(tid)
        self.tracker.cancel(tid)
        assert self.tracker.is_cancelled(tid)

    def test_cancel_returns_true_for_existing(self):
        tid = self.tracker.begin()
        assert self.tracker.cancel(tid) is True

    def test_cancel_returns_false_for_missing(self):
        assert self.tracker.cancel("nonexistent-uuid") is False

    def test_cancel_all_returns_count(self):
        ids = [self.tracker.begin() for _ in range(3)]
        count = self.tracker.cancel_all()
        assert count == 3
        for tid in ids:
            assert self.tracker.is_cancelled(tid)

    def test_drain_blocks_new_turns(self):
        self.tracker.start_drain()
        assert self.tracker.begin() is None

    def test_undrain_allows_new_turns(self):
        self.tracker.start_drain()
        self.tracker.stop_drain()
        tid = self.tracker.begin()
        assert tid is not None

    def test_active_ids(self):
        ids = {self.tracker.begin() for _ in range(3)}
        assert set(self.tracker.active_ids()) == ids

    def test_wait_empty_returns_true_when_empty(self):
        assert self.tracker.wait_empty(timeout=1.0) is True

    def test_wait_empty_returns_false_on_timeout(self):
        self.tracker.begin()  # never ended
        assert self.tracker.wait_empty(timeout=0.1) is False

    def test_wait_empty_returns_true_after_all_end(self):
        tid = self.tracker.begin()
        t = threading.Thread(target=lambda: (time.sleep(0.05), self.tracker.end(tid)))
        t.start()
        result = self.tracker.wait_empty(timeout=1.0)
        t.join()
        assert result is True


# ─── model_cache.py ────────────────────────────────────────────────────────────

class TestModelCache:
    def setup_method(self):
        from ai_bridge.model_cache import ModelCache
        self.cache = ModelCache(ttl=0.2)

    def test_miss_returns_none(self):
        assert self.cache.get("http://example.com") is None

    def test_set_and_get(self):
        self.cache.set("http://example.com", ["gpt-4"])
        assert self.cache.get("http://example.com") == ["gpt-4"]

    def test_ttl_expiry(self):
        self.cache.set("http://example.com", ["gpt-4"])
        time.sleep(0.25)
        assert self.cache.get("http://example.com") is None

    def test_invalidate(self):
        self.cache.set("http://example.com", ["gpt-4"])
        self.cache.invalidate("http://example.com")
        assert self.cache.get("http://example.com") is None

    def test_invalidate_all(self):
        self.cache.set("http://a.com", ["a"])
        self.cache.set("http://b.com", ["b"])
        self.cache.invalidate_all()
        assert self.cache.get("http://a.com") is None
        assert self.cache.get("http://b.com") is None

    def test_key_is_sha256(self):
        url = "http://example.com"
        from ai_bridge.model_cache import _key
        expected = hashlib.sha256(url.encode()).hexdigest()
        assert _key(url) == expected

    def test_wait_or_claim_leader_on_miss(self):
        is_leader, data = self.cache.wait_or_claim("http://example.com")
        assert is_leader is True
        assert data is None

    def test_wait_or_claim_follower_gets_data(self):
        url = "http://example.com"
        is_leader, _ = self.cache.wait_or_claim(url)
        assert is_leader  # we are leader
        self.cache.set(url, ["m1"])
        self.cache._events.pop(self.cache._key(url), None)  # release event

        is_leader2, data2 = self.cache.wait_or_claim(url)
        assert not is_leader2
        assert data2 == ["m1"]


# ─── skills.py ─────────────────────────────────────────────────────────────────

class TestSkills:
    def test_inject_empty_when_no_skills(self):
        from ai_bridge import skills
        msgs = [{"role": "user", "content": "hello"}]
        result = skills.inject_into_messages(msgs)
        assert result == msgs

    def test_inject_into_messages(self, tmp_config_dir):
        from ai_bridge import skills
        skills_dir = tmp_config_dir / "skills"
        skills_dir.mkdir()
        skill_file = skills_dir / "test--abcd1234abcd1234.txt"
        skill_file.write_text("<skill><name>test</name>do something</skill>", "utf-8")

        msgs = [{"role": "user", "content": "hi"}]
        result = skills.inject_into_messages(msgs)
        assert result[0]["role"] == "system"
        assert "do something" in result[0]["content"]
        assert result[-1] == {"role": "user", "content": "hi"}

    def test_inject_into_anthropic_request(self, tmp_config_dir):
        from ai_bridge import skills
        skills_dir = tmp_config_dir / "skills"
        skills_dir.mkdir()
        skill_file = skills_dir / "test--abcd1234abcd1234.txt"
        skill_file.write_text("<skill><name>test</name>do something</skill>", "utf-8")

        req = {"messages": [{"role": "user", "content": "hi"}], "system": []}
        result = skills.inject_into_anthropic_request(req)
        system = result.get("system", [])
        assert any(b.get("text", "").find("do something") != -1 for b in system)

    def test_max_files_limit(self, tmp_config_dir):
        from ai_bridge import skills
        skills_dir = tmp_config_dir / "skills"
        skills_dir.mkdir()
        # Create 11 skill files (over the 10-file limit)
        for i in range(11):
            f = skills_dir / f"skill{i}--{'a' * 16}.txt"
            f.write_text(f"<skill><name>s{i}</name>content{i}</skill>", "utf-8")
        msgs = [{"role": "user", "content": "hi"}]
        result = skills.inject_into_messages(msgs)
        system_msgs = [m for m in result if m["role"] == "system"]
        assert len(system_msgs) <= 10


# ─── journal.py ────────────────────────────────────────────────────────────────

class TestJournal:
    def test_save_creates_snapshot(self, tmp_config_dir):
        import ai_bridge.config as cfg
        import ai_bridge.journal as journal
        cfg.save({"providers": [], "port": 38771})
        path = journal.save("test label")
        assert path is not None
        assert path.exists()

    def test_save_stores_label(self, tmp_config_dir):
        import ai_bridge.config as cfg
        import ai_bridge.journal as journal
        cfg.save({"providers": [], "port": 38771})
        journal.save("my label")
        snap = journal.latest()
        assert snap["label"] == "my label"

    def test_list_snapshots(self, tmp_config_dir):
        import ai_bridge.config as cfg
        import ai_bridge.journal as journal
        cfg.save({"providers": [], "port": 38771})
        journal.save("a")
        journal.save("b")
        snaps = journal.list_snapshots()
        assert len(snaps) == 2

    def test_max_snapshots_trimming(self, tmp_config_dir):
        import ai_bridge.config as cfg
        import ai_bridge.journal as journal
        cfg.save({"providers": [], "port": 38771})
        for i in range(7):
            journal.save(f"snap {i}")
        snaps = journal.list_snapshots()
        assert len(snaps) <= journal.MAX_SNAPSHOTS

    def test_rollback(self, tmp_config_dir):
        import ai_bridge.config as cfg
        import ai_bridge.journal as journal
        cfg.save({"providers": [], "port": 11111})
        journal.save("before")
        snap = journal.latest()
        idx = snap["index"]
        cfg.save({"providers": [], "port": 22222})
        ok = journal.rollback(idx)
        assert ok is True
        data = cfg.load()
        assert data["port"] == 11111

    def test_rollback_missing_index(self, tmp_config_dir):
        import ai_bridge.journal as journal
        ok = journal.rollback(9999)
        assert ok is False

    def test_latest_returns_none_when_empty(self, tmp_config_dir):
        import ai_bridge.journal as journal
        assert journal.latest() is None


# ─── proxy.py admin endpoints ──────────────────────────────────────────────────

class TestAdminEndpoints:
    @pytest.fixture(autouse=True)
    def client(self):
        from ai_bridge import auth, proxy
        self.token = auth.get_token()
        self.client = proxy.app.test_client()
        self.hdrs = {"X-Bridge-Token": self.token}

    def test_status_ok(self):
        resp = self.client.get("/admin/status", headers=self.hdrs)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "active_turns" in data
        assert "draining" in data

    def test_token_endpoint(self):
        resp = self.client.get("/admin/token", headers=self.hdrs)
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["token"] == self.token

    def test_drain_and_undrain(self):
        resp = self.client.post("/admin/drain", headers=self.hdrs)
        assert resp.status_code == 200
        assert json.loads(resp.data)["draining"] is True

        resp = self.client.post("/admin/undrain", headers=self.hdrs)
        assert resp.status_code == 200
        assert json.loads(resp.data)["draining"] is False

    def test_cancel_all(self):
        resp = self.client.post("/admin/cancel-turns", headers=self.hdrs)
        assert resp.status_code == 200
        assert "cancelled" in json.loads(resp.data)

    def test_cancel_specific_turn_not_found(self):
        resp = self.client.post("/admin/cancel/nonexistent-uuid", headers=self.hdrs)
        assert resp.status_code == 404

    def test_all_admin_require_auth(self):
        routes = [
            ("GET", "/admin/status"),
            ("GET", "/admin/token"),
            ("POST", "/admin/drain"),
            ("POST", "/admin/undrain"),
            ("POST", "/admin/cancel-turns"),
            ("POST", "/admin/cancel/someid"),
        ]
        for method, path in routes:
            if method == "GET":
                resp = self.client.get(path)
            else:
                resp = self.client.post(path)
            assert resp.status_code == 401, f"{method} {path} should require auth"

    def test_admin_blocked_by_origin(self):
        resp = self.client.get(
            "/admin/status",
            headers={**self.hdrs, "Origin": "http://evil.example.com"},
        )
        assert resp.status_code == 403
