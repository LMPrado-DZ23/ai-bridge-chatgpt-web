"""Regression tests for launching the actual desktop clients, not their CLIs."""
from unittest.mock import Mock

from ai_bridge import launchers


def test_run_hidden_suppresses_windows_console(monkeypatch):
    class StartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = -1

    run = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(launchers.sys, "platform", "win32")
    monkeypatch.setattr(launchers.subprocess, "run", run)
    monkeypatch.setattr(launchers.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(launchers.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(launchers.subprocess, "STARTUPINFO", StartupInfo, raising=False)

    launchers._run_hidden(["powershell.exe", "-Command", "exit 0"], capture_output=True)

    kwargs = run.call_args.kwargs
    assert kwargs["creationflags"] == 0x08000000
    assert kwargs["startupinfo"].dwFlags & 1
    assert kwargs["startupinfo"].wShowWindow == 0


def test_launch_codex_uses_installed_desktop_app(monkeypatch):
    popen = Mock()
    monkeypatch.setattr(launchers.sys, "platform", "win32")
    monkeypatch.setattr(launchers.subprocess, "Popen", popen)
    monkeypatch.setattr(launchers, "_wait_for_desktop_main_process", Mock())

    launchers.launch_target("codex")

    args = popen.call_args.args[0]
    assert args[0] == "explorer.exe"
    assert args[1] == r"shell:AppsFolder\OpenAI.Codex_2p2nqsd0c76g0!App"
    kwargs = popen.call_args.kwargs
    assert kwargs["close_fds"] is True
    if getattr(launchers.subprocess, "CREATE_NO_WINDOW", 0):
        assert kwargs["creationflags"] == launchers.subprocess.CREATE_NO_WINDOW
    if "startupinfo" in kwargs:
        assert kwargs["startupinfo"].wShowWindow == 0


def test_launch_claude_desktop_uses_installed_msix_app(monkeypatch):
    popen = Mock()
    monkeypatch.setattr(launchers.sys, "platform", "win32")
    monkeypatch.setattr(launchers.subprocess, "Popen", popen)
    monkeypatch.setattr(launchers, "_wait_for_desktop_main_process", Mock())

    launchers.launch_target("claude_desktop")

    args = popen.call_args.args[0]
    assert args[0] == "explorer.exe"
    assert args[1] == r"shell:AppsFolder\Claude_pzs8sxrjxfjjc!Claude"


def test_codex_target_no_longer_launches_cli(monkeypatch):
    desktop = Mock()
    cli = Mock()
    monkeypatch.setattr(launchers, "_launch_codex_desktop", desktop)
    monkeypatch.setattr(launchers, "_launch_cli", cli)
    monkeypatch.setattr(launchers, "_wait_for_desktop_main_process", Mock())

    result = launchers.launch_target("codex")

    assert result["launched"] is True
    desktop.assert_called_once_with()
    cli.assert_not_called()


def test_restart_claude_desktop_waits_for_main_process_before_relaunch(monkeypatch):
    run = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
    launch = Mock()
    wait = Mock()
    monkeypatch.setattr(launchers, "_claude_desktop_processes", lambda: [{"ProcessId": 10}])
    monkeypatch.setattr(launchers.subprocess, "run", run)
    monkeypatch.setattr(launchers, "_launch_claude_desktop", launch)
    monkeypatch.setattr(launchers, "_wait_for_desktop_main_process", wait)

    result = launchers._restart_claude_desktop()

    assert result == {"launched": True, "restarted": True}
    script = run.call_args.args[0][-1]
    assert "CloseMainWindow()" in script
    assert "CommandLine -notmatch '--type='" in script
    assert "AddSeconds(6)" in script
    assert "Stop-Process -Id $root.ProcessId -Force" in script
    launch.assert_called_once_with()
    wait.assert_called_once_with("claude_desktop")


def test_restart_codex_desktop_refuses_to_launch_if_graceful_close_fails(monkeypatch):
    run = Mock(return_value=Mock(returncode=23, stdout="", stderr="still running"))
    launch = Mock()
    monkeypatch.setattr(launchers, "_codex_desktop_processes", lambda: [{"ProcessId": 10}])
    monkeypatch.setattr(launchers.subprocess, "run", run)
    monkeypatch.setattr(launchers, "_launch_codex_desktop", launch)

    import pytest
    with pytest.raises(RuntimeError, match="não fechou de forma segura"):
        launchers._restart_codex_desktop()
    launch.assert_not_called()

def test_restart_waits_for_actual_windows_main_process(monkeypatch):
    run = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(launchers.sys, "platform", "win32")
    monkeypatch.setattr(launchers.subprocess, "run", run)

    launchers._wait_for_desktop_main_process("claude_desktop", timeout=4)

    script = run.call_args.args[0][-1]
    assert r"*\WindowsApps\Claude_*\app\Claude.exe" in script
    assert "MainWindowHandle -ne 0" in script
    assert "MainWindowTitle" in script
    assert "AddSeconds(4)" in script
