"""
Integrates Claude Code through its supported ~/.claude/settings.json model picker.
Original user settings are snapshotted separately and restored on disconnect.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path.home() / ".claude"
SETTINGS_FILE = CLAUDE_DIR / "settings.json"
STATE_FILE = Path.home() / ".ai-bridge" / "backups" / "claude-code-settings.json"
_MANAGED_ENV_KEYS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY",
    "CLAUDE_CODE_GATEWAY_MODEL_DISCOVERY_TIMEOUT_MS",
)


@dataclass
class ClaudeState:
    connected: bool
    port: Optional[int]
    model_count: int


def _load() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        data = json.loads(SETTINGS_FILE.read_text("utf-8"))
    except Exception as exc:
        raise RuntimeError("O settings.json do Claude Code não é JSON válido; o arquivo foi preservado.") from exc
    if not isinstance(data, dict):
        raise RuntimeError("O settings.json do Claude Code tem formato inesperado; o arquivo foi preservado.")
    return data


def _load_state() -> dict:
    if not STATE_FILE.is_file():
        return {}
    try:
        state = json.loads(STATE_FILE.read_text("utf-8"))
    except Exception as exc:
        raise RuntimeError("O backup do Claude Code está inválido; nenhuma configuração foi alterada.") from exc
    if not isinstance(state, dict):
        raise RuntimeError("O backup do Claude Code tem formato inesperado; nenhuma configuração foi alterada.")
    return state


def _write_settings(data: dict):
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CLAUDE_DIR / f".ai-bridge-{os.getpid()}.tmp"
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(SETTINGS_FILE)


def _write_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_name(STATE_FILE.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(STATE_FILE)


def connect(port: int, models: list):
    """Route Claude Code to AI Bridge while preserving Invision's model picker."""
    if not models:
        raise RuntimeError("Adicione e salve ao menos um provedor/modelo antes de conectar o Claude Code.")

    data = _load()
    state = _load_state()
    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
        data["env"] = env

    if not state:
        original_picker_exists = "modelPicker" in data
        state = {
            "original_env": {key: env.get(key) for key in _MANAGED_ENV_KEYS},
            "original_model_picker_exists": original_picker_exists,
            "original_model_picker": data.get("modelPicker"),
            "original_env_existed": "env" in data,
        }

    default_model = models[0].get("model") or models[0].get("value")
    if not default_model:
        raise RuntimeError("O catálogo do Claude Code não contém um identificador de modelo válido.")

    env["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{port}"
    env["ANTHROPIC_MODEL"] = default_model
    env["ANTHROPIC_DEFAULT_OPUS_MODEL"] = default_model
    env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = default_model
    env["CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY"] = "0"
    env.pop("CLAUDE_CODE_GATEWAY_MODEL_DISCOVERY_TIMEOUT_MS", None)

    data["modelPicker"] = {
        "replaceBuiltInOptions": True,
        "options": models,
    }
    state["port"] = port
    state["model_count"] = len(models)

    # Write the settings first; if the write fails, the old state file remains valid.
    _write_settings(data)
    _write_state(state)


def disconnect():
    """Restore the original Claude Code environment and model picker exactly."""
    state = _load_state()
    if not state:
        return

    data = _load()
    env = data.get("env")
    if not isinstance(env, dict):
        env = {}
    original_env = state.get("original_env", {})
    for key in _MANAGED_ENV_KEYS:
        value = original_env.get(key)
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value

    if env or state.get("original_env_existed"):
        data["env"] = env
    else:
        data.pop("env", None)

    if state.get("original_model_picker_exists"):
        data["modelPicker"] = state.get("original_model_picker")
    else:
        data.pop("modelPicker", None)

    _write_settings(data)
    STATE_FILE.unlink(missing_ok=True)


def state() -> ClaudeState:
    try:
        saved = _load_state()
        if saved:
            port = saved.get("port")
            return ClaudeState(
                connected=bool(port),
                port=port,
                model_count=int(saved.get("model_count", 0)),
            )
    except Exception:
        pass
    return ClaudeState(connected=False, port=None, model_count=0)


def is_connected() -> bool:
    return state().connected
