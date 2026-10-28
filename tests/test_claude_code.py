"""Claude Code integration must preserve existing Invision settings on mode changes."""
import json

import pytest

from ai_bridge import claude_code, config


def _setup(tmp_path, monkeypatch, initial=None):
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    settings = claude_dir / "settings.json"
    if initial is not None:
        settings.write_text(json.dumps(initial, indent=2), encoding="utf-8")
    state = tmp_path / ".ai-bridge" / "backups" / "claude-code-settings.json"
    monkeypatch.setattr(claude_code, "CLAUDE_DIR", claude_dir)
    monkeypatch.setattr(claude_code, "SETTINGS_FILE", settings)
    monkeypatch.setattr(claude_code, "STATE_FILE", state)
    return settings, state


def test_connect_uses_valid_model_picker_and_preserves_invision_settings(tmp_path, monkeypatch):
    original_picker = {
        "replaceBuiltInOptions": True,
        "options": [{"model": "invision-black", "label": "Invision Black"}],
    }
    original = {
        "enableWorkflows": True,
        "env": {
            "ANTHROPIC_BASE_URL": "https://invision.example",
            "ANTHROPIC_AUTH_TOKEN": "do-not-copy-or-change",
            "ANTHROPIC_MODEL": "anthropic/claude-sonnet-4.6-invision",
            "ANTHROPIC_DEFAULT_OPUS_MODEL": "invision-black",
            "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
            "UNRELATED_ENV": "keep",
        },
        "modelPicker": original_picker,
    }
    settings, state = _setup(tmp_path, monkeypatch, original)
    models = [{
        "model": "DeepSeek/deepseek-flash",
        "label": "DeepSeek Flash · DeepSeek",
        "behavesAs": "claude-sonnet-5",
    }]

    claude_code.connect(38771, models)

    current = json.loads(settings.read_text(encoding="utf-8"))
    assert current["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:38771"
    assert current["env"]["ANTHROPIC_MODEL"] == "DeepSeek/deepseek-flash"
    assert current["env"]["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "DeepSeek/deepseek-flash"
    assert current["env"]["ANTHROPIC_AUTH_TOKEN"] == "do-not-copy-or-change"
    assert current["env"]["UNRELATED_ENV"] == "keep"
    assert current["modelPicker"] == {
        "replaceBuiltInOptions": True,
        "options": models,
    }
    backup = json.loads(state.read_text(encoding="utf-8"))
    assert "ANTHROPIC_AUTH_TOKEN" not in json.dumps(backup)
    assert backup["original_model_picker"] == original_picker


def test_reconnect_keeps_first_snapshot_and_disconnect_restores_exact_picker(tmp_path, monkeypatch):
    original_picker = {
        "replaceBuiltInOptions": True,
        "options": [{"model": "invision-black", "label": "Invision Black"}],
    }
    original = {
        "env": {
            "ANTHROPIC_BASE_URL": "https://invision.example",
            "ANTHROPIC_MODEL": "invision-black",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "invision-black",
        },
        "modelPicker": original_picker,
        "enableWorkflows": True,
    }
    settings, state = _setup(tmp_path, monkeypatch, original)

    claude_code.connect(38771, [{
        "model": "DeepSeek/deepseek-flash", "label": "DeepSeek Flash"
    }])
    claude_code.connect(38772, [{
        "model": "DeepSeek/deepseek-v4-pro", "label": "DeepSeek V4 Pro"
    }])
    assert claude_code.state().connected is True
    assert claude_code.state().port == 38772

    claude_code.disconnect()

    restored = json.loads(settings.read_text(encoding="utf-8"))
    assert restored["env"] == original["env"]
    assert restored["modelPicker"] == original_picker
    assert restored["enableWorkflows"] is True
    assert not state.exists()
    assert claude_code.state().connected is False


def test_disconnect_without_snapshot_does_not_delete_user_model_picker(tmp_path, monkeypatch):
    picker = {"replaceBuiltInOptions": True, "options": [{"model": "invision-black"}]}
    settings, _ = _setup(tmp_path, monkeypatch, {"modelPicker": picker, "other": True})

    claude_code.disconnect()

    current = json.loads(settings.read_text(encoding="utf-8"))
    assert current["modelPicker"] == picker
    assert current["other"] is True


def test_invalid_settings_json_is_preserved(tmp_path, monkeypatch):
    settings, _ = _setup(tmp_path, monkeypatch)
    settings.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(RuntimeError, match="JSON válido"):
        claude_code.connect(38771, [{"model": "DeepSeek/deepseek-flash"}])

    assert settings.read_text(encoding="utf-8") == "{not valid json"


def test_claude_code_models_use_documented_picker_fields(monkeypatch):
    monkeypatch.setattr(config, "all_picker_models", lambda: [
        {"value": "DeepSeek/deepseek-flash", "label": "DeepSeek Flash · DeepSeek"},
        {"value": "Anthropic/claude-sonnet-4-6", "label": "Claude Sonnet 4.6"},
    ])

    models = config.claude_code_models()

    assert models[0] == {
        "model": "DeepSeek/deepseek-flash",
        "label": "DeepSeek Flash · DeepSeek",
        "behavesAs": "claude-sonnet-5",
    }
    assert models[1] == {
        "model": "Anthropic/claude-sonnet-4-6",
        "label": "Claude Sonnet 4.6",
    }
