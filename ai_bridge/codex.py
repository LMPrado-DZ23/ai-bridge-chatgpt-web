"""
Codex CLI integration for AI Bridge.
Modifies ~/.codex/config.toml and, on Windows, user-level env vars.
"""
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CODEX_DIR = Path.home() / '.codex'
CODEX_CONFIG = CODEX_DIR / 'config.toml'
_BRIDGE_TAG = '# ai-bridge-managed'


@dataclass
class CodexState:
    installed: bool
    connected: bool
    port: Optional[int]


def _is_installed() -> bool:
    import shutil
    return shutil.which('codex') is not None or CODEX_DIR.exists()


def _read_port() -> Optional[int]:
    """Scan config for the _ai_bridge_port marker we write."""
    if not CODEX_CONFIG.exists():
        return None
    for line in CODEX_CONFIG.read_text('utf-8', errors='replace').splitlines():
        line = line.strip()
        if line.startswith('_ai_bridge_port'):
            _, _, val = line.partition('=')
            try:
                return int(val.strip())
            except ValueError:
                pass
    return None


def _write_config(port: Optional[int]):
    """Write/update config.toml preserving non-bridge lines."""
    CODEX_DIR.mkdir(parents=True, exist_ok=True)
    kept = []
    bridge_keys = {'api_base', 'base_url', 'api_key', '_ai_bridge_port'}

    if CODEX_CONFIG.exists():
        skip_next = False
        for line in CODEX_CONFIG.read_text('utf-8', errors='replace').splitlines():
            stripped = line.strip()
            if stripped == _BRIDGE_TAG:
                skip_next = True
                continue
            if skip_next and '=' in stripped:
                key = stripped.partition('=')[0].strip()
                if key in bridge_keys:
                    continue
                skip_next = False
            kept.append(line)

    if port is not None:
        # First available model becomes the Codex default
        from . import config as cfg
        models = cfg.all_picker_models()
        default_model = models[0]['value'] if models else 'gpt-4o'

        kept.append('')
        kept.append(_BRIDGE_TAG)
        kept.append(f'model = "{default_model}"')
        kept.append(f'api_base = "http://127.0.0.1:{port}/v1"')
        kept.append(f'base_url = "http://127.0.0.1:{port}/v1"')
        kept.append('api_key = "ai-bridge-local"')
        kept.append(f'_ai_bridge_port = {port}')

    tmp = CODEX_CONFIG.with_suffix('.tmp')
    content = '\n'.join(kept).strip()
    tmp.write_text(content + '\n' if content else '', encoding='utf-8')
    tmp.replace(CODEX_CONFIG)


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


def connect(port: int):
    _write_config(port)
    _set_win_env('OPENAI_BASE_URL', f'http://127.0.0.1:{port}/v1')
    _set_win_env('OPENAI_API_KEY', 'ai-bridge-local')


def disconnect():
    _write_config(None)
    _del_win_env('OPENAI_BASE_URL')
    _del_win_env('OPENAI_API_KEY')


def update_models():
    """Refresh the default model in the config after providers change."""
    port = _read_port()
    if port is not None:
        _write_config(port)


def state() -> CodexState:
    if not _is_installed():
        return CodexState(installed=False, connected=False, port=None)
    port = _read_port()
    return CodexState(installed=True, connected=port is not None, port=port)


def is_connected() -> bool:
    return state().connected
