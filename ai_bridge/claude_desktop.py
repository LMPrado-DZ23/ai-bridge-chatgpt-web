"""
Claude Desktop integration for AI Bridge.
Uses Claude Desktop's supported third-party inference gateway configuration on Windows.
The app's account/session data is left untouched when switching modes.
"""
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

def _resolve_cfg_dir() -> Path:
    """Resolve the config directory used by the installed Claude Desktop build.

    The Microsoft Store/MSIX build can use a custom Electron user-data directory
    under %LOCALAPPDATA%\\Claude-Data\\claude_<package-family-suffix>, rather
    than the legacy %APPDATA%\\Claude path.
    """
    if sys.platform == 'win32':
        appdata = Path(os.environ.get('APPDATA', Path.home() / 'AppData' / 'Roaming'))
        local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
        legacy = appdata / 'Claude'
        data_root = local / 'Claude-Data'
        data_dirs = sorted(
            (p for p in data_root.glob('claude_*') if p.is_dir()),
            key=lambda p: (p / 'claude_desktop_config.json').exists(),
            reverse=True,
        )
        # Prefer an existing config, never create a second empty config beside it.
        for candidate in data_dirs:
            if (candidate / 'claude_desktop_config.json').is_file():
                return candidate
        if legacy.joinpath('claude_desktop_config.json').is_file():
            return legacy
        # If the Store app is installed but has not created its config yet,
        # derive its user-data folder from the MSIX package family suffix.
        package_root = local / 'Packages'
        for package_dir in sorted(package_root.glob('Claude_*')):
            suffix = package_dir.name.removeprefix('Claude_')
            candidate = data_root / f'claude_{suffix}'
            if candidate.exists() or package_dir.is_dir():
                return candidate
        if data_dirs:
            return data_dirs[0]
        return legacy
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Claude'
    return Path.home() / '.config' / 'Claude'


_CFG_DIR = _resolve_cfg_dir()


@dataclass
class ClaudeDesktopState:
    installed: bool
    connected: bool
    port: Optional[int]
    mode: str = 'original'


def _is_installed() -> bool:
    if _CFG_DIR.exists():
        return True
    if sys.platform == 'win32':
        local = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
        # MSIX installs create a package data folder even when the old
        # %APPDATA%\\Claude directory is absent.
        if any((local / 'Packages').glob('Claude_*')):
            return True
        if any((local / 'Claude-Data').glob('claude_*')):
            return True
        for executable in (
            local / 'Programs' / 'Claude' / 'Claude.exe',
            local / 'Claude' / 'Claude.exe',
            Path(os.environ.get('ProgramFiles', 'C:\\\\Program Files')) / 'Claude' / 'Claude.exe',
        ):
            if executable.is_file():
                return True
        windows_apps = Path(os.environ.get('ProgramFiles', 'C:\\\\Program Files')) / 'WindowsApps'
        if windows_apps.exists():
            try:
                if any(windows_apps.glob('Claude_*/app/Claude.exe')):
                    return True
            except (OSError, PermissionError):
                pass
    return False


def connect(port: int):
    """Configure the supported Claude Desktop third-party inference gateway."""
    from . import config as cfg
    from . import claude_desktop_gateway as gateway
    return gateway.configure(port, cfg.claude_desktop_models())


def disconnect():
    """Restore the user's prior gateway values and return to signed-in mode."""
    from . import claude_desktop_gateway as gateway
    return gateway.disconnect()


def state() -> ClaudeDesktopState:
    if not _is_installed():
        return ClaudeDesktopState(installed=False, connected=False, port=None, mode='unavailable')
    from . import claude_desktop_gateway as gateway
    if gateway.is_configured():
        from . import config as cfg
        return ClaudeDesktopState(installed=True, connected=True, port=cfg.get_port(), mode='bridge')
    mode = gateway.deployment_mode()
    if mode == '3p':
        return ClaudeDesktopState(installed=True, connected=False, port=None, mode='external')
    return ClaudeDesktopState(installed=True, connected=False, port=None, mode='original')


def is_connected() -> bool:
    return state().connected
