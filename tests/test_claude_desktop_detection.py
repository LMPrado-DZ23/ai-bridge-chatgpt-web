"""Tests for Claude Desktop's local configLibrary gateway integration."""
import json

import pytest

from ai_bridge import claude_desktop, claude_desktop_gateway, config


def test_resolve_uses_existing_msix_user_data_config(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    appdata = tmp_path / "Roaming"
    custom = local / "Claude-Data" / "claude_pzs8sxrjxfjjc"
    custom.mkdir(parents=True)
    (custom / "claude_desktop_config.json").write_text('{"preferences": {}}', encoding="utf-8")
    (appdata / "Claude").mkdir(parents=True)
    (appdata / "Claude" / "claude_desktop_config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(claude_desktop.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(appdata))

    assert claude_desktop._resolve_cfg_dir() == custom


def test_detects_msix_install_without_legacy_appdata_config(tmp_path, monkeypatch):
    local = tmp_path / "Local"
    package_data = local / "Packages" / "Claude_pzs8sxrjxfjjc"
    package_data.mkdir(parents=True)
    monkeypatch.setattr(claude_desktop.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(claude_desktop, "_CFG_DIR", tmp_path / "Roaming" / "Claude")

    assert claude_desktop._is_installed() is True


def _prepare_gateway_test(tmp_path, monkeypatch, meta=None):
    local = tmp_path / "Local"
    library = local / "Claude-3p" / "configLibrary"
    library.mkdir(parents=True)
    meta_file = library / "_meta.json"
    if meta is not None:
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    app_config = local / "Claude-3p" / "claude_desktop_config.json"
    app_config.write_text(json.dumps({
        "preferences": {"theme": "dark", "keep": True},
        "deploymentMode": "1p",
    }), encoding="utf-8")
    backup_dir = tmp_path / ".ai-bridge" / "backups"
    backup_file = backup_dir / "claude-desktop-config-library.json"
    monkeypatch.setattr(claude_desktop_gateway.sys, "platform", "win32")
    monkeypatch.setattr(claude_desktop_gateway, "_CONFIG_LIBRARY", library)
    monkeypatch.setattr(claude_desktop_gateway, "_META_FILE", meta_file)
    monkeypatch.setattr(claude_desktop_gateway, "_APP_CONFIG", app_config)
    monkeypatch.setattr(claude_desktop_gateway, "_BACKUP_DIR", backup_dir)
    monkeypatch.setattr(claude_desktop_gateway, "_BACKUP_FILE", backup_file)
    return library, meta_file, app_config, backup_dir, backup_file


def _models():
    return [
        {"name": "claude-sonnet-5", "labelOverride": "DeepSeek Flash · DeepSeek (AI Bridge)"},
        {"name": "claude-sonnet-5-2", "labelOverride": "DeepSeek V4 Pro · DeepSeek (AI Bridge)"},
    ]


def test_connect_writes_official_config_library_and_preserves_app_settings(tmp_path, monkeypatch):
    original_meta = {"entries": ["preexisting-entry"], "appliedId": "preexisting-entry"}
    library, meta_file, app_config, backup_dir, backup_file = _prepare_gateway_test(
        tmp_path, monkeypatch, original_meta
    )
    existing_entry = library / "preexisting-entry.json"
    existing_entry.write_text(json.dumps({"inferenceProvider": "gateway", "inferenceGatewayBaseUrl": "https://old.example"}), encoding="utf-8")

    result = claude_desktop_gateway.configure(38771, _models())

    assert result["ok"] is True
    assert result["restart_required"] is True
    assert result["models"] == 2
    assert result["config_path"] == str(library / f"{result['config_id']}.json")
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert meta["appliedId"] == result["config_id"]
    assert meta["entries"] == ["preexisting-entry", result["config_id"]]
    assert meta["appliedId"] in meta["entries"]
    entry = json.loads((library / f"{result['config_id']}.json").read_text(encoding="utf-8"))
    assert entry["inferenceProvider"] == "gateway"
    assert entry["inferenceCredentialKind"] == "static"
    assert entry["inferenceGatewayBaseUrl"] == "http://127.0.0.1:38771"
    assert entry["inferenceGatewayApiKey"] == "ai-bridge-local"
    assert entry["inferenceGatewayAuthScheme"] == "bearer"
    assert entry["modelDiscoveryEnabled"] is False
    assert entry["inferenceModels"] == _models()
    app = json.loads(app_config.read_text(encoding="utf-8"))
    assert app["preferences"] == {"theme": "dark", "keep": True}
    assert app["deploymentMode"] == "3p"
    assert backup_file.exists()
    assert existing_entry.exists()
    assert claude_desktop_gateway.is_configured(38771)


def test_connect_without_prior_meta_creates_applied_id_and_disconnect_restores_original_mode(tmp_path, monkeypatch):
    library, meta_file, app_config, backup_dir, backup_file = _prepare_gateway_test(tmp_path, monkeypatch)

    result = claude_desktop_gateway.configure(38771, _models())
    assert result["ok"] is True
    assert json.loads(meta_file.read_text(encoding="utf-8"))["appliedId"] == result["config_id"]

    claude_desktop_gateway.disconnect()

    assert not meta_file.exists()
    assert not (library / f"{result['config_id']}.json").exists()
    app = json.loads(app_config.read_text(encoding="utf-8"))
    assert app["preferences"] == {"theme": "dark", "keep": True}
    assert app["deploymentMode"] == "1p"
    assert not backup_file.exists()
    assert not claude_desktop_gateway.is_configured()


def test_disconnect_restores_previous_metadata_and_preserves_other_entry(tmp_path, monkeypatch):
    original_meta = {"entries": ["existing"], "appliedId": "existing"}
    library, meta_file, app_config, backup_dir, backup_file = _prepare_gateway_test(
        tmp_path, monkeypatch, original_meta
    )
    (library / "existing.json").write_text(json.dumps({"inferenceProvider": "gateway", "inferenceGatewayBaseUrl": "https://old.example"}), encoding="utf-8")
    result = claude_desktop_gateway.configure(38771, _models())

    claude_desktop_gateway.disconnect()

    assert json.loads(meta_file.read_text(encoding="utf-8")) == original_meta
    assert (library / "existing.json").exists()
    assert not (library / f"{result['config_id']}.json").exists()
    assert json.loads(app_config.read_text(encoding="utf-8"))["deploymentMode"] == "1p"
    assert not backup_file.exists()


def test_disconnect_does_not_override_a_profile_switched_by_the_user(tmp_path, monkeypatch):
    library, meta_file, app_config, _, backup_file = _prepare_gateway_test(tmp_path, monkeypatch)
    result = claude_desktop_gateway.configure(38771, _models())
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["entries"].append("another-profile")
    meta["appliedId"] = "another-profile"
    meta_file.write_text(json.dumps(meta), encoding="utf-8")
    (library / "another-profile.json").write_text(json.dumps({
        "inferenceProvider": "gateway",
        "inferenceGatewayBaseUrl": "https://other.example",
    }), encoding="utf-8")

    claude_desktop_gateway.disconnect()

    restored_meta = json.loads(meta_file.read_text(encoding="utf-8"))
    assert restored_meta["appliedId"] == "another-profile"
    assert restored_meta["entries"] == ["another-profile"]
    assert (library / "another-profile.json").exists()
    assert not (library / f"{result['config_id']}.json").exists()
    # The user selected another profile; keep the 3p mode rather than force 1p.
    assert json.loads(app_config.read_text(encoding="utf-8"))["deploymentMode"] == "3p"
    assert not backup_file.exists()


def test_invalid_meta_json_is_preserved(tmp_path, monkeypatch):
    library, meta_file, app_config, _, backup_file = _prepare_gateway_test(tmp_path, monkeypatch)
    meta_file.write_text("{broken", encoding="utf-8")

    with pytest.raises(RuntimeError, match="JSON válido"):
        claude_desktop_gateway.configure(38771, _models())

    assert meta_file.read_text(encoding="utf-8") == "{broken"
    assert not backup_file.exists()


def test_claude_desktop_models_use_claude_compatible_ids_and_route_to_upstream(monkeypatch):
    providers = [{
        "name": "DeepSeek",
        "api_key": "test-key",
        "base_url": "https://api.example/v1",
        "models": [
            {"id": "deepseek-flash", "label": "DeepSeek Flash"},
            {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
        ],
    }]
    monkeypatch.setattr(config, "get_providers", lambda: providers)
    monkeypatch.setattr(config, "load", lambda: {"active_model": "DeepSeek/deepseek-v4-pro"})

    models = config.claude_desktop_models()

    assert models[0] == {"name": "claude-sonnet-5", "labelOverride": "DeepSeek V4 Pro · DeepSeek (AI Bridge)"}
    assert models[1] == {"name": "claude-sonnet-5-2", "labelOverride": "DeepSeek Flash · DeepSeek (AI Bridge)"}
    assert config.get_provider_for_model("claude-sonnet-5")["name"] == "DeepSeek"
    assert config.get_model_id("claude-sonnet-5") == "deepseek-v4-pro"
    assert config.get_model_id("claude-sonnet-5-2") == "deepseek-flash"
    # Compatibility for Claude Desktop's previously persisted Invision model.
    assert config.get_model_id("claude-opus-5-5") == "deepseek-v4-pro"
    assert all(model["name"] != "claude-opus-5-5" for model in models)


def test_gateway_detection_rejects_applied_id_not_registered_in_entries(tmp_path, monkeypatch):
    library, meta_file, app_config, _, backup_file = _prepare_gateway_test(tmp_path, monkeypatch)
    (library / "orphan.json").write_text(json.dumps({
        "inferenceProvider": "gateway",
        "inferenceGatewayBaseUrl": "http://127.0.0.1:38771",
        "inferenceGatewayApiKey": "ai-bridge-local",
    }), encoding="utf-8")
    meta_file.write_text(json.dumps({"appliedId": "orphan", "entries": []}), encoding="utf-8")

    assert not claude_desktop_gateway.is_configured(38771)
