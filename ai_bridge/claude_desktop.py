"""
Claude Desktop integration for AI Bridge.
Writes ANTHROPIC_BASE_URL to the app config and Windows user env.
"""
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

if sys.platform == 'win32':
    _CFG_DIR = Path(os.environ.get('APPDATA', Path.home())) / 'Claude'
elif sys.platform == 'darwin':
    _CFG_DIR = Path.home() / 'Library' / 'Application Support' / 'Claude'
else:
    _CFG_DIR = Path.home() / '.config' / 'Claude'

_CFG_FILE = _CFG_DIR / 'claude_desktop_config.json'
_BRIDGE_KEY = '_ai_bridge'


@dataclass
class ClaudeDesktopState:
    installed: bool
    connected: bool
    port: Optional[int]


def _is_installed() -> bool:
    return _CFG_DIR.exists()


def _load() -> dict:
    if not _CFG_FILE.exists():
        return {}
    try:
        return json.loads(_CFG_FILE.read_text('utf-8'))
    except Exception:
        return {}


def _write(data: dict):
    _CFG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _CFG_DIR / f'.aib-{os.getpid()}.tmp'
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), 'utf-8')
    tmp.replace(_CFG_FILE)


def _set_win_env(key: str, value: str):
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0,
                            winreg.KEY_SET_VALUE) as reg:
            winreg.SetValueEx(reg, key, 0, winreg.REG_EXPAND_SZ, value)
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, 'Environment', 2, 5000, None)
    except Exception:
        pass


def _del_win_env(key: str):
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment', 0,
                            winreg.KEY_SET_VALUE) as reg:
            try:
                winreg.DeleteValue(reg, key)
            except FileNotFoundError:
                pass
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, 'Environment', 2, 5000, None)
    except Exception:
        pass


def _set_mac_env(key: str, value: str):
    if sys.platform != 'darwin':
        return
    try:
        import subprocess
        subprocess.run(['launchctl', 'setenv', key, value], timeout=5)
    except Exception:
        pass


def _del_mac_env(key: str):
    if sys.platform != 'darwin':
        return
    try:
        import subprocess
        subprocess.run(['launchctl', 'unsetenv', key], timeout=5)
    except Exception:
        pass


def connect(port: int):
    """Route Claude Desktop API calls through AI Bridge."""
    data = _load()
    data[_BRIDGE_KEY] = {'port': port}
    _write(data)

    url = f'http://127.0.0.1:{port}'
    _set_win_env('ANTHROPIC_BASE_URL', url)
    _set_mac_env('ANTHROPIC_BASE_URL', url)


def disconnect():
    """Remove AI Bridge config from Claude Desktop."""
    data = _load()
    data.pop(_BRIDGE_KEY, None)
    if data:
        _write(data)

    _del_win_env('ANTHROPIC_BASE_URL')
    _del_mac_env('ANTHROPIC_BASE_URL')


def state() -> ClaudeDesktopState:
    if not _is_installed():
        return ClaudeDesktopState(installed=False, connected=False, port=None)
    bridge = _load().get(_BRIDGE_KEY)
    if bridge:
        return ClaudeDesktopState(installed=True, connected=True, port=bridge.get('port'))
    return ClaudeDesktopState(installed=True, connected=False, port=None)


def is_connected() -> bool:
    return state().connected
