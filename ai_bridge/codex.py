"""Codex CLI provider integration with reversible, isolated config edits."""
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

CODEX_DIR = Path.home() / ".codex"
CODEX_CONFIG = CODEX_DIR / "config.toml"
CODEX_CATALOG_FILE = CODEX_DIR / "ai-bridge-models.json"
_BRIDGE_TAG = "# ai-bridge-managed"
_BRIDGE_PROVIDER = "ai_bridge"
_STATE_FILE = CODEX_DIR / ".ai-bridge-state.json"


@dataclass
class CodexState:
    installed: bool
    connected: bool
    port: Optional[int]


def _is_installed() -> bool:
    return shutil.which("codex") is not None or CODEX_DIR.exists()


def _read_port() -> Optional[int]:
    if not CODEX_CONFIG.exists():
        return None
    in_bridge_table = False
    for line in CODEX_CONFIG.read_text("utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_bridge_table = stripped == "[model_providers.ai_bridge]"
            continue
        if in_bridge_table and stripped.startswith("base_url"):
            match = re.search(r'http://127\.0\.0\.1:(\d+)/v1', stripped)
            if match:
                return int(match.group(1))
    return None


def _split_root(lines):
    """Return root-level lines and table-level lines, preserving original text."""
    root, rest = [], []
    in_table = False
    for line in lines:
        if line.lstrip().startswith("[") and line.rstrip().endswith("]"):
            in_table = True
        (rest if in_table else root).append(line)
    return root, rest


def _root_value(root, key):
    prefix = key + " ="
    return next((line for line in root if line.strip().startswith(prefix)), None)


def _remove_root_key(root, key):
    prefix = key + " ="
    return [line for line in root if not line.strip().startswith(prefix)]


def _write_catalog_file():
    """Write the local catalog consumed by Codex Desktop's model selector."""
    from . import config as cfg

    models = []
    for picker_model in cfg.all_picker_models():
        model_id = picker_model["value"]
        label = picker_model["label"]
        provider_name = model_id.split("/", 1)[0]
        models.append({
            "slug": model_id,
            # Keep both spellings: Codex Desktop and its bundled CLI have
            # used different serialized names for the same display field.
            "displayName": label,
            "display_name": label,
            "provider": _BRIDGE_PROVIDER,
            "hidden": False,
            "description": f"{label} via AI Bridge ({provider_name}).",
            "default_reasoning_level": "medium",
            "supported_reasoning_levels": [
                {"effort": "low", "description": "Respostas mais rápidas."},
                {"effort": "medium", "description": "Equilíbrio entre velocidade e profundidade."},
                {"effort": "high", "description": "Maior profundidade para tarefas complexas."},
            ],
            "shell_type": "shell_command",
            "visibility": "list",
            "supported_in_api": True,
            "priority": len(models) + 1,
            "additional_speed_tiers": [],
            "service_tiers": [],
            "default_service_tier": None,
            "availability_nux": None,
            "upgrade": None,
            "support_verbosity": False,
            "default_verbosity": None,
            "apply_patch_tool_type": "freeform",
            "web_search_tool_type": "text",
            "truncation_policy": {"mode": "tokens", "limit": 10000},
            "effective_context_window_percent": 95,
            "experimental_supported_tools": [],
            "input_modalities": ["text"],
            "base_instructions": "Modelo externo conectado pelo AI Bridge. Siga as instruções do usuário.",
        })
    if not models:
        raise RuntimeError("O catálogo do Codex está vazio; adicione um provedor/modelo antes de conectar.")
    CODEX_DIR.mkdir(parents=True, exist_ok=True)
    temp = CODEX_CATALOG_FILE.with_name(CODEX_CATALOG_FILE.name + ".tmp")
    temp.write_text(json.dumps({"models": models}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(CODEX_CATALOG_FILE)


def _write_config(port: Optional[int]):
    from . import config as cfg

    CODEX_DIR.mkdir(parents=True, exist_ok=True)
    existing = CODEX_CONFIG.read_text("utf-8", errors="replace") if CODEX_CONFIG.exists() else ""
    lines = existing.splitlines()
    root, rest = _split_root(lines)

    if port is None:
        if not _STATE_FILE.exists():
            # Never remove a provider block we cannot prove AI Bridge owns.
            return
        state = json.loads(_STATE_FILE.read_text("utf-8"))
        rest_lines = []
        skip_bridge_block = False
        for line in rest:
            stripped = line.strip()
            if stripped == _BRIDGE_TAG:
                skip_bridge_block = True
                continue
            if skip_bridge_block:
                if stripped == "[model_providers.ai_bridge]":
                    continue
                if stripped.startswith("[") and stripped.endswith("]"):
                    skip_bridge_block = False
                else:
                    continue
            rest_lines.append(line)
        root = _remove_root_key(root, "model_provider")
        root = _remove_root_key(root, "model")
        root = _remove_root_key(root, "model_catalog_json")
        if state.get("original_model_provider") is not None:
            root.append(state["original_model_provider"])
        if state.get("original_model") is not None:
            root.append(state["original_model"])
        if state.get("original_model_catalog_json") is not None:
            root.append(state["original_model_catalog_json"])
        new_lines = root + rest_lines
        content = "\n".join(new_lines).strip()
        tmp = CODEX_CONFIG.with_suffix(".tmp")
        tmp.write_text(content + "\n" if content else "", encoding="utf-8")
        tmp.replace(CODEX_CONFIG)
        if state.get("original_catalog_existed"):
            catalog_backup_path = Path(state.get("original_catalog_backup") or "")
            if catalog_backup_path.is_file():
                shutil.copy2(catalog_backup_path, CODEX_CATALOG_FILE)
                catalog_backup_path.unlink(missing_ok=True)
        else:
            CODEX_CATALOG_FILE.unlink(missing_ok=True)
        _STATE_FILE.unlink(missing_ok=True)
        return

    if not cfg.all_picker_models():
        raise RuntimeError("Adicione e salve um provedor/modelo antes de conectar o Codex.")
    if "[model_providers.ai_bridge]" in existing and not _STATE_FILE.exists():
        raise RuntimeError("Já existe um provedor model_providers.ai_bridge que o AI Bridge não criou; ele não será sobrescrito.")

    models = cfg.all_picker_models()
    default_model = cfg.get_active_model() or models[0]["value"]

    if not _STATE_FILE.exists():
        CODEX_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = CODEX_DIR / f"config.toml.ai-bridge-backup-{stamp}.bak"
        if CODEX_CONFIG.exists():
            shutil.copy2(CODEX_CONFIG, backup)
        catalog_backup = None
        if CODEX_CATALOG_FILE.exists():
            catalog_backup = CODEX_CATALOG_FILE.with_name(f"ai-bridge-models.json.ai-bridge-backup-{stamp}.bak")
            shutil.copy2(CODEX_CATALOG_FILE, catalog_backup)
        state = {
            "backup": str(backup) if CODEX_CONFIG.exists() else None,
            "original_model_provider": _root_value(root, "model_provider"),
            "original_model": _root_value(root, "model"),
            "original_model_catalog_json": _root_value(root, "model_catalog_json"),
            "original_catalog_existed": CODEX_CATALOG_FILE.exists(),
            "original_catalog_backup": str(catalog_backup) if catalog_backup else None,
        }
        _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    _write_catalog_file()

    root = _remove_root_key(root, "model_provider")
    root = _remove_root_key(root, "model")
    root = _remove_root_key(root, "model_catalog_json")
    root.extend([
        f'model_provider = "{_BRIDGE_PROVIDER}"',
        f'model = "{default_model}"',
        f'model_catalog_json = "{CODEX_CATALOG_FILE.resolve().as_posix()}"',
    ])

    # Remove an earlier managed block only; all unrelated provider blocks remain untouched.
    rest_lines = []
    skip_bridge_block = False
    for line in rest:
        stripped = line.strip()
        if stripped == _BRIDGE_TAG:
            skip_bridge_block = True
            continue
        if skip_bridge_block:
            if stripped == "[model_providers.ai_bridge]":
                continue
            if stripped.startswith("[") and stripped.endswith("]"):
                skip_bridge_block = False
            else:
                continue
        rest_lines.append(line)

    block = [
        "",
        _BRIDGE_TAG,
        "[model_providers.ai_bridge]",
        'name = "AI Bridge"',
        f'base_url = "http://127.0.0.1:{port}/v1"',
        'wire_api = "responses"',
        f'model_catalog_url = "http://127.0.0.1:{port}/v1/codex-models"',
        'experimental_bearer_token = "ai-bridge-local"',
    ]
    content = "\n".join(root + rest_lines + block).strip() + "\n"
    tmp = CODEX_CONFIG.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(CODEX_CONFIG)


def connect(port: int):
    # Keep global environment variables and Codex authentication untouched.
    _write_config(port)


def disconnect():
    _write_config(None)


def update_models():
    port = _read_port()
    if port is not None:
        _write_config(port)


def _set_win_env(key: str, value: str):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as reg:
            winreg.SetValueEx(reg, key, 0, winreg.REG_EXPAND_SZ, value)
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, None)
    except Exception:
        pass


def _del_win_env(key: str):
    if sys.platform != "win32":
        return
    try:
        import ctypes
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as reg:
            try:
                winreg.DeleteValue(reg, key)
            except FileNotFoundError:
                pass
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 2, 5000, None)
    except Exception:
        pass


def state() -> CodexState:
    if not _is_installed():
        return CodexState(installed=False, connected=False, port=None)
    port = _read_port()
    return CodexState(installed=True, connected=port is not None, port=port)


def is_connected() -> bool:
    return state().connected
