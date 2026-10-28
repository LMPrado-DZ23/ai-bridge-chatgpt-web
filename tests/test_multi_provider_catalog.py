"""Multi-provider catalog regression tests."""
import json

from ai_bridge import codex, config


def test_codex_catalog_includes_every_model_from_multiple_providers(tmp_path, monkeypatch):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    catalog_path = codex_dir / "ai-bridge-models.json"
    monkeypatch.setattr(codex, "CODEX_DIR", codex_dir)
    monkeypatch.setattr(codex, "CODEX_CATALOG_FILE", catalog_path)
    monkeypatch.setattr(config, "all_picker_models", lambda: [
        {"value": "DeepSeek/deepseek-flash", "label": "DeepSeek Flash · DeepSeek"},
        {"value": "DeepSeek/deepseek-v4-pro", "label": "DeepSeek V4 Pro · DeepSeek"},
        {"value": "OpenRouter/anthropic/claude-sonnet", "label": "Claude Sonnet · OpenRouter"},
        {"value": "Gemini/gemini-2.5-pro", "label": "Gemini 2.5 Pro · Gemini"},
    ])

    codex._write_catalog_file()
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

    assert [model["slug"] for model in catalog["models"]] == [
        "DeepSeek/deepseek-flash",
        "DeepSeek/deepseek-v4-pro",
        "OpenRouter/anthropic/claude-sonnet",
        "Gemini/gemini-2.5-pro",
    ]
    assert len({model["slug"] for model in catalog["models"]}) == 4
    assert all(model["visibility"] == "list" for model in catalog["models"])
    assert all(model["supported_in_api"] for model in catalog["models"])


def test_claude_gateway_catalog_includes_models_from_multiple_providers(monkeypatch):
    monkeypatch.setattr(config, "get_providers", lambda: [
        {"name": "DeepSeek", "models": [
            {"id": "deepseek-flash", "label": "DeepSeek Flash"},
            {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
        ]},
        {"name": "OpenRouter", "models": [
            {"id": "anthropic/claude-sonnet", "label": "Claude Sonnet"},
        ]},
    ])
    monkeypatch.setattr(config, "load", lambda: {
        "active_model": "DeepSeek/deepseek-v4-pro"
    })

    models = config.claude_desktop_models()

    assert models == [
        {"name": "claude-sonnet-5", "labelOverride": "DeepSeek V4 Pro · DeepSeek (AI Bridge)"},
        {"name": "claude-sonnet-5-2", "labelOverride": "DeepSeek Flash · DeepSeek (AI Bridge)"},
        {"name": "claude-sonnet-5-3", "labelOverride": "Claude Sonnet · OpenRouter (AI Bridge)"},
    ]
    assert config.get_model_id("claude-sonnet-5-3") == "anthropic/claude-sonnet"
