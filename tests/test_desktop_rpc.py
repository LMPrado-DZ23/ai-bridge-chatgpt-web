import pytest

from ai_bridge import proxy


@pytest.fixture
def desktop_rpc():
    class FakeBridge:
        def state(self):
            return {"ok": True, "admin_token": "must-not-reach-renderer", "providers": []}

        def set_active_model(self, value):
            return {"ok": True, "active_model": value}

    token = "a" * 64
    proxy.configure_desktop_rpc(FakeBridge(), token)
    yield token
    proxy.configure_desktop_rpc(None, None)


def test_desktop_rpc_requires_local_token_and_rejects_origin(desktop_rpc):
    client = proxy.app.test_client()
    denied = client.post("/__desktop/rpc", json={"method": "state", "args": []})
    assert denied.status_code == 401

    csrf = client.post(
        "/__desktop/rpc",
        json={"method": "state", "args": []},
        headers={"X-AI-Bridge-IPC": desktop_rpc, "Origin": "https://attacker.example"},
    )
    assert csrf.status_code == 403


def test_desktop_rpc_dispatches_only_allowlisted_methods_and_redacts_admin_token(desktop_rpc):
    client = proxy.app.test_client()
    response = client.post(
        "/__desktop/rpc",
        json={"method": "state", "args": []},
        headers={"X-AI-Bridge-IPC": desktop_rpc},
    )
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "providers": []}

    changed = client.post(
        "/__desktop/rpc",
        json={"method": "set_active_model", "args": ["openrouter/model-x"]},
        headers={"X-AI-Bridge-IPC": desktop_rpc},
    )
    assert changed.status_code == 200
    assert changed.get_json() == {"ok": True, "active_model": "openrouter/model-x"}

    forbidden = client.post(
        "/__desktop/rpc",
        json={"method": "_disconnect_all", "args": []},
        headers={"X-AI-Bridge-IPC": desktop_rpc},
    )
    assert forbidden.status_code == 400
