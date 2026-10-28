"""Safe launch/restart helpers for installed AI clients."""
import json
import os
import shutil
import subprocess
import sys


def _run_hidden(command, **kwargs):
    """Run helper commands without spawning a visible console on Windows."""
    if sys.platform == "win32":
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if flags:
            kwargs["creationflags"] = flags
        startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
        if startupinfo_type is not None:
            startupinfo = startupinfo_type()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
            startupinfo.wShowWindow = 0
            kwargs["startupinfo"] = startupinfo
    return subprocess.run(command, **kwargs)


def _claude_desktop_processes():
    if sys.platform != "win32":
        return []
    script = r"""
$items = Get-CimInstance Win32_Process -Filter "name='Claude.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.ExecutablePath -like '*\WindowsApps\Claude_*\app\Claude.exe' } |
  Select-Object ProcessId, ExecutablePath
$items | ConvertTo-Json -Compress
"""
    try:
        result = _run_hidden(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        parsed = json.loads(result.stdout)
        return parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        return []


def _codex_desktop_processes():
    """Return only processes belonging to the installed Codex MSIX package."""
    if sys.platform != "win32":
        return []
    script = r"""
$items = Get-CimInstance Win32_Process -Filter "name='ChatGPT.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.ExecutablePath -like '*\WindowsApps\OpenAI.Codex_*\app\ChatGPT.exe' } |
  Select-Object ProcessId, ExecutablePath
$items | ConvertTo-Json -Compress
"""
    try:
        result = _run_hidden(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        parsed = json.loads(result.stdout)
        return parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        return []


def is_running(target_id: str) -> bool:
    if target_id == "claude_desktop":
        return bool(_claude_desktop_processes())
    if target_id == "codex":
        return bool(_codex_desktop_processes())
    # Claude Code sessions run in terminals; do not kill or replace them.
    return False


def _launch_msix_app(app_id: str):
    """Launch a packaged desktop application by its registered AppsFolder identity."""
    if sys.platform != "win32":
        raise RuntimeError("A abertura automática do aplicativo MSIX requer Windows.")
    # Using AppsFolder avoids relying on CLI PATH or a protocol handler that may
    # be missing. It starts the installed desktop app without touching its profile.
    command = ["explorer.exe", f"shell:AppsFolder\\{app_id}"]
    kwargs = {"close_fds": True}
    # The normal desktop-app path must never create a visible console window.
    # Keep this guarded so unit tests and non-Windows platforms can import it.
    no_console = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if no_console:
        kwargs["creationflags"] = no_console
    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
    subprocess.Popen(command, **kwargs)


def _launch_claude_desktop():
    if sys.platform == "win32":
        _launch_msix_app("Claude_pzs8sxrjxfjjc!Claude")
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", "claude://claude.ai/new"])
        return
    raise RuntimeError("A abertura automática do Claude Desktop não é suportada neste sistema.")


def _launch_codex_desktop():
    if sys.platform == "win32":
        _launch_msix_app("OpenAI.Codex_2p2nqsd0c76g0!App")
        return
    raise RuntimeError("A abertura automática do Codex Desktop não é suportada neste sistema.")


def _wait_for_desktop_main_process(target_id: str, timeout: int = 12):
    """Do not report a successful launch until the real desktop process exists."""
    if sys.platform != "win32":
        return
    if target_id == "claude_desktop":
        process_name = "Claude.exe"
        path_pattern = r"*\WindowsApps\Claude_*\app\Claude.exe"
        app_name = "Claude Desktop"
    elif target_id == "codex":
        process_name = "ChatGPT.exe"
        path_pattern = r"*\WindowsApps\OpenAI.Codex_*\app\ChatGPT.exe"
        app_name = "Codex Desktop"
    else:
        return

    script = f"""
$deadline = (Get-Date).AddSeconds({int(timeout)})
do {{
  $app = Get-Process -Name '{process_name.removesuffix(".exe")}' -ErrorAction SilentlyContinue |
    Where-Object {{ $_.Path -like '{path_pattern}' -and $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle }}
  if ($app) {{ exit 0 }}
  Start-Sleep -Milliseconds 250
}} while ((Get-Date) -lt $deadline)
exit 24
"""
    result = _run_hidden(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout + 3, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"{app_name} não iniciou corretamente após a troca de modo; a configuração não será considerada validada.")


def _restart_codex_desktop():
    processes = _codex_desktop_processes()
    if not processes:
        _launch_codex_desktop()
        _wait_for_desktop_main_process("codex")
        return {"launched": True, "restarted": False}

    script = r"""
$apps = Get-Process -Name ChatGPT -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -like '*\WindowsApps\OpenAI.Codex_*\app\ChatGPT.exe' -and $_.MainWindowHandle -ne 0 }
foreach ($app in $apps) { [void]$app.CloseMainWindow() }
$deadline = (Get-Date).AddSeconds(6)
do {
  Start-Sleep -Milliseconds 250
  $remaining = Get-CimInstance Win32_Process -Filter "name='ChatGPT.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -like '*\WindowsApps\OpenAI.Codex_*\app\ChatGPT.exe' -and $_.CommandLine -notmatch '--type=' }
} while ($remaining -and (Get-Date) -lt $deadline)
if ($remaining) {
  Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class AiBridgeWindowState { [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd); }'
  $visible = Get-Process -Name ChatGPT -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like '*\WindowsApps\OpenAI.Codex_*\app\ChatGPT.exe' -and $_.MainWindowHandle -ne 0 -and [AiBridgeWindowState]::IsWindowVisible($_.MainWindowHandle) }
  if ($visible) { exit 23 }
  foreach ($root in $remaining) { Stop-Process -Id $root.ProcessId -Force -ErrorAction SilentlyContinue }
  $deadline = (Get-Date).AddSeconds(6)
  do {
    Start-Sleep -Milliseconds 250
    $remaining = Get-CimInstance Win32_Process -Filter "name='ChatGPT.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.ExecutablePath -like '*\WindowsApps\OpenAI.Codex_*\app\ChatGPT.exe' -and $_.CommandLine -notmatch '--type=' }
  } while ($remaining -and (Get-Date) -lt $deadline)
}
if ($remaining) { exit 23 }
Start-Sleep -Milliseconds 750
exit 0
"""
    result = _run_hidden(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=18, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "O Codex Desktop não fechou de forma segura. Salve o trabalho e feche o aplicativo manualmente; o AI Bridge não encerrou processos à força."
        )
    _launch_codex_desktop()
    _wait_for_desktop_main_process("codex")
    return {"launched": True, "restarted": True}


def _restart_claude_desktop():
    processes = _claude_desktop_processes()
    if not processes:
        _launch_claude_desktop()
        _wait_for_desktop_main_process("claude_desktop")
        return {"launched": True, "restarted": False}

    script = r"""
$apps = Get-Process -Name Claude -ErrorAction SilentlyContinue |
  Where-Object { $_.Path -like '*\WindowsApps\Claude_*\app\Claude.exe' -and $_.MainWindowHandle -ne 0 }
foreach ($app in $apps) { [void]$app.CloseMainWindow() }
$deadline = (Get-Date).AddSeconds(6)
do {
  Start-Sleep -Milliseconds 250
  $remaining = Get-CimInstance Win32_Process -Filter "name='Claude.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.ExecutablePath -like '*\WindowsApps\Claude_*\app\Claude.exe' -and $_.CommandLine -notmatch '--type=' }
} while ($remaining -and (Get-Date) -lt $deadline)
if ($remaining) {
  Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class AiBridgeWindowState { [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd); }'
  $visible = Get-Process -Name Claude -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -like '*\WindowsApps\Claude_*\app\Claude.exe' -and $_.MainWindowHandle -ne 0 -and [AiBridgeWindowState]::IsWindowVisible($_.MainWindowHandle) }
  if ($visible) { exit 23 }
  foreach ($root in $remaining) { Stop-Process -Id $root.ProcessId -Force -ErrorAction SilentlyContinue }
  $deadline = (Get-Date).AddSeconds(6)
  do {
    Start-Sleep -Milliseconds 250
    $remaining = Get-CimInstance Win32_Process -Filter "name='Claude.exe'" -ErrorAction SilentlyContinue |
      Where-Object { $_.ExecutablePath -like '*\WindowsApps\Claude_*\app\Claude.exe' -and $_.CommandLine -notmatch '--type=' }
  } while ($remaining -and (Get-Date) -lt $deadline)
}
if ($remaining) { exit 23 }
Start-Sleep -Milliseconds 750
exit 0
"""
    result = _run_hidden(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=18, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "O Claude Desktop está aberto e não fechou de forma segura. Salve o trabalho e feche o Claude Desktop; depois clique novamente. O AI Bridge não encerrou processos à força."
        )
    _launch_claude_desktop()
    _wait_for_desktop_main_process("claude_desktop")
    return {"launched": True, "restarted": True}


def _resolve_cli(command: str):
    found = shutil.which(command)
    if found:
        return found
    if sys.platform == "win32":
        from pathlib import Path
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        if command == "codex":
            candidates = list((local / "OpenAI" / "Codex" / "bin").glob("*/codex.exe"))
        else:
            candidates = list((local / "Claude-Data").glob("claude_*/claude-code/*/*/claude.exe"))
        candidates = [candidate for candidate in candidates if candidate.is_file()]
        if candidates:
            return str(max(candidates, key=lambda item: item.stat().st_mtime))
    return None


def _launch_cli(command: str, title: str):
    executable = _resolve_cli(command)
    if not executable:
        raise RuntimeError(f"{command} não foi encontrado em um caminho conhecido. Nenhuma janela foi aberta.")
    if sys.platform == "win32":
        wt = shutil.which("wt.exe") or shutil.which("wt")
        command_line = f'"{executable}"'
        if wt:
            subprocess.Popen([wt, "new-tab", "--title", title, "cmd.exe", "/k", command_line])
        else:
            subprocess.Popen(["cmd.exe", "/k", command_line])
    else:
        raise RuntimeError(
            f"{command} está instalado, mas a abertura automática de terminal não está configurada neste sistema."
        )
    return {"launched": True, "restarted": False}


def launch_target(target_id: str, restart: bool = False) -> dict:
    if target_id == "claude_desktop":
        if restart:
            return _restart_claude_desktop()
        _launch_claude_desktop()
        _wait_for_desktop_main_process("claude_desktop")
        return {"launched": True, "restarted": False}
    if target_id == "codex":
        if restart:
            return _restart_codex_desktop()
        _launch_codex_desktop()
        _wait_for_desktop_main_process("codex")
        return {"launched": True, "restarted": False}
    if target_id == "claude_code":
        return _launch_cli("claude", "Claude Code via AI Bridge")
    raise RuntimeError(f"Aplicativo desconhecido: {target_id}")
