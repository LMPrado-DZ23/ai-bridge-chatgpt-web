"""Claude Desktop third-party inference integration through its local configLibrary.

This avoids Windows policy registry writes, which can fail with WinError 5.
Changes are reversible and do not modify Claude account/session data.
"""
import json
import os
import sys
import uuid
from pathlib import Path

_LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
_CONFIG_LIBRARY = _LOCAL / "Claude-3p" / "configLibrary"
_META_FILE = _CONFIG_LIBRARY / "_meta.json"
_APP_CONFIG = _LOCAL / "Claude-3p" / "claude_desktop_config.json"
_BACKUP_DIR = Path.home() / ".ai-bridge" / "backups"
_BACKUP_FILE = _BACKUP_DIR / "claude-desktop-config-library.json"
_LOCAL_API_KEY = "ai-bridge-local"


def _read_json(path: Path, label: str, default=None):
    if not path.is_file():
        return default
    try:
        value = json.loads(path.read_text("utf-8-sig"))
    except Exception as exc:
        raise RuntimeError(f"{label} não é JSON válido; o arquivo foi preservado.") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} tem formato inesperado; o arquivo foi preservado.")
    return value


def _write_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".ai-bridge-{os.getpid()}.tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def _active_id(meta: dict) -> str:
    # Current Claude Desktop uses appliedId. activeConfigId is kept as a
    # compatibility read for older builds referenced by earlier Invision versions.
    return str(meta.get("appliedId") or meta.get("activeConfigId") or "")


def _entry_path(config_id: str) -> Path:
    return _CONFIG_LIBRARY / f"{config_id}.json"


def _load_state():
    state = _read_json(_BACKUP_FILE, "O backup do Claude Desktop", default=None)
    if state is None:
        return None
    return state


def configure(port: int, models: list) -> dict:
    """Write a per-user third-party gateway config and select it for next launch."""
    if sys.platform != "win32":
        raise RuntimeError("A configuração automática do Claude Desktop por configLibrary está implementada para Windows.")
    if not models:
        raise RuntimeError("Adicione e salve ao menos um provedor/modelo antes de conectar o Claude Desktop.")

    normalized = []
    seen = set()
    for model in models:
        model_id = str(model.get("name") or model.get("id") or "").strip()
        if not model_id:
            raise RuntimeError("O catálogo do Claude Desktop contém um modelo sem identificador.")
        if model_id in seen:
            raise RuntimeError(f"O catálogo do Claude Desktop contém um ID duplicado: {model_id}")
        seen.add(model_id)
        # Claude Desktop expects name to be a Claude-compatible model route.
        # labelOverride is the display-only label for non-Anthropic upstreams.
        item = {"name": model_id}
        label = str(model.get("labelOverride") or "").strip()
        if label:
            item["labelOverride"] = label
        if model.get("supports1m") is True:
            item["supports1m"] = True
        normalized.append(item)

    _CONFIG_LIBRARY.mkdir(parents=True, exist_ok=True)
    meta_existed = _META_FILE.is_file()
    meta = _read_json(_META_FILE, "O índice configLibrary do Claude Desktop", default={})
    app_existed = _APP_CONFIG.is_file()
    app_config = _read_json(_APP_CONFIG, "A configuração local do Claude Desktop", default={})
    if not isinstance(meta.get("entries", []), list):
        raise RuntimeError("O índice configLibrary tem o campo entries em formato inesperado; arquivo preservado.")

    previous_state = _load_state()
    previous_id = _active_id(meta)
    if previous_state:
        config_id = str(previous_state.get("config_id") or "")
        if not config_id or not _entry_path(config_id).is_file():
            raise RuntimeError("O backup do AI Bridge aponta para uma configuração ausente; nenhuma alteração foi feita.")
        # Keep the first snapshot so reconnecting never overwrites the user's original settings.
        state = previous_state
    else:
        config_id = str(uuid.uuid4())
        state = {
            "config_id": config_id,
            "meta_existed": meta_existed,
            "original_meta": meta,
            "original_applied_id": previous_id,
            "app_config_existed": app_existed,
            "original_deployment_mode_existed": "deploymentMode" in app_config,
            "original_deployment_mode": app_config.get("deploymentMode"),
        }
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        _write_json(_BACKUP_FILE, state)

    gateway_entry = {
        "inferenceProvider": "gateway",
        "inferenceCredentialKind": "static",
        "inferenceGatewayBaseUrl": f"http://127.0.0.1:{port}",
        "inferenceGatewayApiKey": _LOCAL_API_KEY,
        "inferenceGatewayAuthScheme": "bearer",
        "inferenceModels": normalized,
        "modelDiscoveryEnabled": False,
        "disableDeploymentModeChooser": True,
    }

    # Keep the app's current metadata and any other saved entries. Only switch
    # the applied ID to the AI Bridge-owned config.
    new_meta = dict(meta)
    # Claude Desktop validates appliedId against entries. If the profile ID is
    # not also registered here, the app can discard it on startup and silently
    # fall back to first-party mode.
    entries = [str(item) for item in new_meta.get("entries", []) if item]
    if config_id not in entries:
        entries.append(config_id)
    new_meta["entries"] = entries
    new_meta["appliedId"] = config_id
    # Older Claude Desktop builds used activeConfigId; harmless for current builds.
    if "activeConfigId" in new_meta:
        new_meta["activeConfigId"] = config_id

    new_app_config = dict(app_config)
    new_app_config["deploymentMode"] = "3p"

    try:
        _write_json(_entry_path(config_id), gateway_entry)
        _write_json(_META_FILE, new_meta)
        _write_json(_APP_CONFIG, new_app_config)
    except Exception:
        # Restore the metadata/config files if any write fails halfway through.
        try:
            if state.get("meta_existed"):
                _write_json(_META_FILE, state["original_meta"])
            else:
                _META_FILE.unlink(missing_ok=True)
            if state.get("app_config_existed"):
                original_app = dict(app_config)
                if state.get("original_deployment_mode_existed"):
                    original_app["deploymentMode"] = state.get("original_deployment_mode")
                else:
                    original_app.pop("deploymentMode", None)
                _write_json(_APP_CONFIG, original_app)
            else:
                _APP_CONFIG.unlink(missing_ok=True)
            if not previous_state:
                _entry_path(config_id).unlink(missing_ok=True)
                _BACKUP_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    return {
        "ok": True,
        "port": port,
        "models": len(normalized),
        "restart_required": True,
        "config_id": config_id,
        "config_path": str(_entry_path(config_id)),
        "meta_path": str(_META_FILE),
    }


def disconnect() -> dict:
    """Remove only the AI Bridge-owned entry and restore the previous active mode."""
    state = _load_state()
    if not state:
        return {"ok": True, "restart_required": False, "already_disconnected": True}

    config_id = str(state.get("config_id") or "")
    entry_path = _entry_path(config_id) if config_id else None
    meta = _read_json(_META_FILE, "O índice configLibrary do Claude Desktop", default={})
    app_config = _read_json(_APP_CONFIG, "A configuração local do Claude Desktop", default={})
    owns_active = _active_id(meta) == config_id

    original_meta = dict(state.get("original_meta") or {})
    current_entries = [str(item) for item in meta.get("entries", []) if item]
    original_entries = [str(item) for item in original_meta.get("entries", []) if item]
    extra_entries = [
        item for item in current_entries
        if item != config_id and item not in original_entries
    ]

    if owns_active:
        # Restore the previous active profile, but keep unrelated profiles that
        # may have been added while AI Bridge was connected.
        if state.get("meta_existed"):
            restored_meta = dict(original_meta)
            restored_entries = original_entries + extra_entries
            if restored_entries or "entries" in original_meta:
                restored_meta["entries"] = restored_entries
            _write_json(_META_FILE, restored_meta)
        elif extra_entries:
            restored_meta = dict(meta)
            restored_meta["entries"] = extra_entries
            restored_meta.pop("appliedId", None)
            restored_meta.pop("activeConfigId", None)
            _write_json(_META_FILE, restored_meta)
        else:
            _META_FILE.unlink(missing_ok=True)
    else:
        # The user switched to another profile while connected; do not change
        # that active selection, only remove the AI Bridge-owned profile ID.
        meta["entries"] = [item for item in current_entries if item != config_id]
        _write_json(_META_FILE, meta)

    if entry_path and entry_path.is_file():
        # Delete only the entry created by this AI Bridge snapshot.
        entry = _read_json(entry_path, "A configuração do gateway AI Bridge", default={})
        if entry.get("inferenceGatewayBaseUrl", "").startswith("http://127.0.0.1:") and entry.get("inferenceGatewayApiKey") == _LOCAL_API_KEY:
            entry_path.unlink(missing_ok=True)

    # Do not override a mode changed by the user to another third-party gateway.
    if owns_active and app_config.get("deploymentMode") == "3p":
        if state.get("original_deployment_mode_existed"):
            app_config["deploymentMode"] = state.get("original_deployment_mode")
        else:
            app_config.pop("deploymentMode", None)
        if state.get("app_config_existed"):
            _write_json(_APP_CONFIG, app_config)
        else:
            _APP_CONFIG.unlink(missing_ok=True)

    _BACKUP_FILE.unlink(missing_ok=True)
    return {"ok": True, "restart_required": True, "restored": owns_active}


def is_configured(port=None) -> bool:
    try:
        meta = _read_json(_META_FILE, "O índice configLibrary", default={})
        config_id = _active_id(meta)
        if not config_id:
            return False
        entries = meta.get("entries", [])
        if not isinstance(entries, list) or config_id not in [str(item) for item in entries]:
            return False
        entry = _read_json(_entry_path(config_id), "A configuração ativa do gateway", default={})
        base_url = entry.get("inferenceGatewayBaseUrl", "")
        if entry.get("inferenceProvider") != "gateway":
            return False
        if not base_url.startswith("http://127.0.0.1:"):
            return False
        if entry.get("inferenceGatewayApiKey") != _LOCAL_API_KEY:
            return False
        return port is None or base_url == f"http://127.0.0.1:{port}"
    except Exception:
        return False


def deployment_mode() -> str:
    try:
        data = _read_json(_APP_CONFIG, "A configuração local do Claude Desktop", default={})
        return data.get("deploymentMode", "1p")
    except Exception:
        return "unknown"
