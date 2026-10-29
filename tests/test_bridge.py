"""Regression tests for provider validation in the desktop bridge."""
from unittest.mock import Mock

import httpx

from ai_bridge import bridge as bridge_module
from ai_bridge.bridge import Bridge


def test_chatgpt_web_runtime_discovery_uses_bounded_powershell_fallback(monkeypatch):
    expected = r"C:\Users\test-user\AppData\Local\Programs\Codex Web GPT\Codex Web GPT.exe"
    monkeypatch.setattr(bridge_module.sys, "platform", "win32")
    monkeypatch.setattr(bridge_module.Path, "is_file", lambda _path: False)
    monkeypatch.setattr(
        bridge_module.subprocess,
        "run",
        Mock(return_value=Mock(returncode=0, stdout=expected, stderr="")),
    )

    result = Bridge._chatgpt_web_executable()

    assert str(result) == expected
    bridge_module.subprocess.run.assert_called_once()
    assert "-EncodedCommand" in bridge_module.subprocess.run.call_args.args[0]


def test_chatgpt_web_status_reports_installed_runtime_without_exposing_profile(monkeypatch, tmp_path):
    executable = tmp_path / "Codex Web GPT.exe"
    executable.write_text("test executable")
    monkeypatch.setattr(Bridge, "_chatgpt_web_executable", staticmethod(lambda: executable))

    result = Bridge().chatgpt_web_status()

    assert result == {
        "ok": True,
        "installed": True,
        "name": "Codex Web GPT",
        "integration": "separate-desktop-runtime",
    }
    assert "path" not in result
    assert "profile" not in result


def test_chatgpt_web_launch_preserves_existing_profile_and_checks_start(monkeypatch, tmp_path):
    executable = tmp_path / "Codex Web GPT.exe"
    executable.write_text("test executable")
    process = Mock()
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr(Bridge, "_chatgpt_web_executable", staticmethod(lambda: executable))
    monkeypatch.setattr(bridge_module.subprocess, "Popen", popen)
    monkeypatch.setattr(bridge_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bridge_module.sys, "platform", "linux")

    result = Bridge().launch_chatgpt_web()

    assert result == {"ok": True, "launched": True, "profile_preserved": True}
    popen.assert_called_once()
    assert popen.call_args.args[0] == [str(executable)]


def test_chatgpt_web_launch_accepts_existing_single_instance(monkeypatch, tmp_path):
    executable = tmp_path / "Codex Web GPT.exe"
    executable.write_text("test executable")
    process = Mock()
    process.poll.return_value = 0
    monkeypatch.setattr(Bridge, "_chatgpt_web_executable", staticmethod(lambda: executable))
    monkeypatch.setattr(bridge_module.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(bridge_module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        bridge_module.subprocess,
        "run",
        Mock(return_value=Mock(returncode=0, stdout='"Codex Web GPT.exe","123","Console","1","1,024 K"\n')),
    )
    monkeypatch.setattr(bridge_module.sys, "platform", "win32")
    monkeypatch.setattr(bridge_module.subprocess, "CREATE_NO_WINDOW", 0, raising=False)

    result = Bridge().launch_chatgpt_web()

    assert result == {
        "ok": True,
        "launched": True,
        "already_running": True,
        "profile_preserved": True,
    }
    bridge_module.subprocess.Popen.assert_called_once()
    bridge_module.subprocess.run.assert_called_once()


def test_chatgpt_web_launch_fails_closed_when_runtime_is_missing(monkeypatch):
    monkeypatch.setattr(Bridge, "_chatgpt_web_executable", staticmethod(lambda: None))

    result = Bridge().launch_chatgpt_web()

    assert result["ok"] is False
    assert "não foi encontrado" in result["error"]


def test_resize_window_clamps_to_minimum_and_maximum():
    app = Bridge()
    window = Mock()
    app.set_window(window)

    result = app.resize_window(100, 100)

    assert result == {"ok": True, "width": 520, "height": 500}
    window.resize.assert_called_once_with(520, 500)

    result = app.resize_window(9000, 9000)

    assert result == {"ok": True, "width": 1600, "height": 1200}
    window.resize.assert_called_with(1600, 1200)


def test_resize_window_rejects_invalid_dimensions_without_crashing():
    app = Bridge()
    window = Mock()
    app.set_window(window)

    result = app.resize_window("invalid", 700)

    assert result == {"ok": False, "error": "Dimensões inválidas."}
    window.resize.assert_not_called()


def test_resize_window_reports_missing_window():
    result = Bridge().resize_window(680, 700)

    assert result == {"ok": False, "error": "Janela indisponível."}


def test_test_provider_returns_model_count(monkeypatch):
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "data": [{"id": "model-a"}, {"id": "model-b"}]
    }
    monkeypatch.setattr("ai_bridge.bridge.httpx.get", lambda *args, **kwargs: response)

    result = Bridge().test_provider("https://example.com/v1", "test-key")

    assert result["ok"] is True
    assert result["models"] == ["model-a", "model-b"]
    assert result["models_count"] == 2


def test_test_provider_requires_api_key():
    result = Bridge().test_provider("https://example.com/v1", "")

    assert result == {"ok": False, "error": "Chave API não informada."}


def test_test_provider_reports_actionable_401_without_exposing_key(monkeypatch):
    response = Mock()
    response.status_code = 401
    response.json.return_value = {
        "error": {"message": "invalid key bad-key"}
    }
    monkeypatch.setattr("ai_bridge.bridge.httpx.get", lambda *args, **kwargs: response)

    result = Bridge().test_provider("https://example.com/v1", "bad-key", "DeepSeek")

    assert result["ok"] is False
    assert result["status_code"] == 401
    assert "HTTP 401" in result["error"]
    assert "autenticação recusada" in result["error"]
    assert "bad-key" not in result["error"]
    assert "[chave ocultada]" in result["error"]


def test_test_provider_reports_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        request = httpx.Request("GET", "https://example.com/v1/models")
        raise httpx.TimeoutException("timeout", request=request)

    monkeypatch.setattr("ai_bridge.bridge.httpx.get", timeout)

    result = Bridge().test_provider("https://example.com/v1", "test-key")

    assert result == {"ok": False, "error": "Timeout ao conectar."}


def test_activate_target_switches_in_one_action_and_restarts_running_client(monkeypatch):
    from ai_bridge import bridge as bridge_module
    app = bridge_module.Bridge()
    monkeypatch.setattr(bridge_module, "_target_state", lambda _target: {
        "id": "claude_desktop", "installed": True, "connected": False, "mode": "original"
    })
    monkeypatch.setattr(bridge_module.launchers, "is_running", lambda _target: True)
    monkeypatch.setattr(app, "_preflight_active_provider", lambda: {"ok": True})
    monkeypatch.setattr(app, "connect_target", lambda target: {"ok": True, "port": 38771})
    calls = []
    monkeypatch.setattr(bridge_module.launchers, "launch_target", lambda target, restart=False: (calls.append((target, restart)) or {"launched": True, "restarted": True}))

    result = app.activate_target("claude_desktop", "bridge")

    assert result["ok"] is True
    assert result["mode"] == "bridge"
    assert calls == [("claude_desktop", True)]
    assert "confirm_restart" not in result


def test_activate_target_configures_and_restarts_in_one_action(monkeypatch):
    from ai_bridge import bridge as bridge_module
    app = bridge_module.Bridge()
    calls = []
    monkeypatch.setattr(bridge_module, "_target_state", lambda _target: {
        "id": "claude_desktop", "installed": True, "connected": False, "mode": "original"
    })
    monkeypatch.setattr(bridge_module.launchers, "is_running", lambda _target: True)
    monkeypatch.setattr(app, "_preflight_active_provider", lambda: {"ok": True})
    monkeypatch.setattr(app, "connect_target", lambda _target: {"ok": True, "port": 38771})
    monkeypatch.setattr(bridge_module.launchers, "launch_target", lambda target, restart=False: (
        calls.append((target, restart)) or {"launched": True, "restarted": True}
    ))

    result = app.activate_target("claude_desktop", "bridge")

    assert result["ok"] is True
    assert result["mode"] == "bridge"
    assert calls == [("claude_desktop", True)]


def test_activate_original_mode_restores_and_opens_codex(monkeypatch):
    from ai_bridge import bridge as bridge_module
    app = bridge_module.Bridge()
    calls = []
    monkeypatch.setattr(bridge_module, "_target_state", lambda _target: {
        "id": "codex", "installed": True, "connected": True, "port": 38771
    })
    monkeypatch.setattr(bridge_module.launchers, "is_running", lambda _target: False)
    monkeypatch.setattr(app, "disconnect_target", lambda _target: {"ok": True})
    monkeypatch.setattr(bridge_module.launchers, "launch_target", lambda target, restart=False: (
        calls.append((target, restart)) or {"launched": True, "restarted": False}
    ))

    result = app.activate_target("codex", "original")

    assert result["ok"] is True
    assert result["mode"] == "original"
    assert calls == [("codex", False)]


def test_activate_bridge_blocks_when_selected_provider_fails_preflight(monkeypatch):
    from ai_bridge import bridge as bridge_module
    app = bridge_module.Bridge()
    monkeypatch.setattr(bridge_module, "_target_state", lambda _target: {
        "id": "codex", "installed": True, "connected": False, "port": None
    })
    monkeypatch.setattr(app, "_preflight_active_provider", lambda: {
        "ok": False, "error": "O provedor DeepSeek não passou no teste: HTTP 401"
    })
    monkeypatch.setattr(app, "connect_target", lambda _target: (_ for _ in ()).throw(AssertionError("must not change config")))
    monkeypatch.setattr(bridge_module.launchers, "launch_target", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not launch")))

    result = app.activate_target("codex", "bridge")

    assert result["ok"] is False
    assert "HTTP 401" in result["error"]


def test_failed_restart_rolls_back_the_requested_mode(monkeypatch):
    from ai_bridge import bridge as bridge_module
    app = bridge_module.Bridge()
    calls = []
    monkeypatch.setattr(bridge_module, "_target_state", lambda _target: {
        "id": "codex", "installed": True, "connected": True, "port": 38771
    })
    monkeypatch.setattr(bridge_module.launchers, "is_running", lambda _target: True)
    monkeypatch.setattr(app, "disconnect_target", lambda target: calls.append(("disconnect", target)) or {"ok": True})
    monkeypatch.setattr(app, "connect_target", lambda target: calls.append(("connect", target)) or {"ok": True, "port": 38771})
    monkeypatch.setattr(bridge_module.launchers, "launch_target", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("app did not restart")))

    result = app.activate_target("codex", "original")

    assert result["ok"] is False
    assert "configuração anterior foi restaurada" in result["error"]
    assert calls == [("disconnect", "codex"), ("connect", "codex")]


def test_clicking_original_mode_again_restarts_stale_claude_window(monkeypatch):
    from ai_bridge import bridge as bridge_module
    app = bridge_module.Bridge()
    calls = []
    monkeypatch.setattr(bridge_module, "_target_state", lambda _target: {
        "id": "claude_desktop", "installed": True, "connected": False, "mode": "original"
    })
    monkeypatch.setattr(bridge_module.launchers, "is_running", lambda _target: True)
    monkeypatch.setattr(bridge_module.launchers, "launch_target", lambda target, restart=False: (
        calls.append((target, restart)) or {"launched": True, "restarted": True}
    ))

    result = app.activate_target("claude_desktop", "original")

    assert result["ok"] is True
    assert calls == [("claude_desktop", True)]
