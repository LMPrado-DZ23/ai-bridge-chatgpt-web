"""Regression tests for Codex config safety and Responses API translation."""
import json
import tomllib

from ai_bridge import codex, config, responses_api


def test_codex_connect_and_disconnect_preserve_existing_provider(tmp_path, monkeypatch):
    codex_dir = tmp_path / ".codex"
    codex_dir.mkdir()
    config_file = codex_dir / "config.toml"
    original = '''notify = ["codex-notify", "turn-ended"]

[desktop]
conversationDetailMode = "STEPS_COMMANDS"

[model_providers.invision]
name = "Invision"
base_url = "https://invision.example/v1"
wire_api = "responses"
'''
    config_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(codex, "CODEX_DIR", codex_dir)
    monkeypatch.setattr(codex, "CODEX_CONFIG", config_file)
    monkeypatch.setattr(codex, "CODEX_CATALOG_FILE", codex_dir / "ai-bridge-models.json")
    monkeypatch.setattr(codex, "_STATE_FILE", codex_dir / ".ai-bridge-state.json")
    monkeypatch.setattr(config, "all_picker_models", lambda: [
        {"value": "DeepSeek/deepseek-chat", "label": "DeepSeek Chat · DeepSeek"}
    ])

    codex.connect(38771)
    connected = config_file.read_text(encoding="utf-8")
    assert 'model_provider = "ai_bridge"' in connected
    assert 'base_url = "http://127.0.0.1:38771/v1"' in connected
    assert '[model_providers.invision]' in connected
    parsed = tomllib.loads(connected)
    assert parsed['model_provider'] == 'ai_bridge'
    assert parsed['model_providers']['ai_bridge']['base_url'] == 'http://127.0.0.1:38771/v1'
    assert parsed['model_providers']['ai_bridge']['model_catalog_url'] == 'http://127.0.0.1:38771/v1/codex-models'
    assert parsed['model_catalog_json'] == (codex_dir / 'ai-bridge-models.json').resolve().as_posix()
    catalog = json.loads((codex_dir / 'ai-bridge-models.json').read_text(encoding='utf-8'))
    assert [model['slug'] for model in catalog['models']] == ['DeepSeek/deepseek-chat']
    assert catalog['models'][0]['displayName'] == 'DeepSeek Chat · DeepSeek'
    assert catalog['models'][0]['display_name'] == 'DeepSeek Chat · DeepSeek'
    assert catalog['models'][0]['provider'] == 'ai_bridge'
    assert catalog['models'][0]['hidden'] is False
    assert catalog['models'][0]['slug'] == 'DeepSeek/deepseek-chat'
    assert '_ai_bridge_port' not in parsed['model_providers']['ai_bridge']
    assert codex._read_port() == 38771
    assert codex.state().connected is True

    codex.disconnect()
    restored = config_file.read_text(encoding="utf-8")
    assert 'model_provider = "ai_bridge"' not in restored
    assert '[model_providers.ai_bridge]' not in restored
    assert '[model_providers.invision]' in restored
    assert 'base_url = "https://invision.example/v1"' in restored
    assert 'conversationDetailMode = "STEPS_COMMANDS"' in restored
    assert codex.state().connected is False
    assert not (codex_dir / 'ai-bridge-models.json').exists()


def test_responses_request_translates_text_and_function_tools():
    request = responses_api.to_chat_request({
        "instructions": "Be concise",
        "input": [
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hello"}]},
            {"type": "function_call_output", "call_id": "call_1", "output": "done"},
        ],
        "tools": [{
            "type": "function", "name": "read_file", "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }],
        "max_output_tokens": 128,
    }, "deepseek-chat")

    assert request["model"] == "deepseek-chat"
    assert request["messages"][0] == {"role": "system", "content": "Be concise"}
    assert request["messages"][1] == {"role": "user", "content": "hello"}
    assert request["messages"][2]["role"] == "tool"
    assert request["tools"][0]["function"]["name"] == "read_file"
    assert request["max_tokens"] == 128


def test_responses_result_translates_text_and_tool_calls():
    result = responses_api.from_chat_response({
        "choices": [{
            "message": {
                "content": "hello",
                "tool_calls": [{
                    "id": "call_1", "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'}
                }],
            }
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7},
    }, "DeepSeek/deepseek-chat")

    assert result["object"] == "response"
    assert result["output"][0]["content"][0]["text"] == "hello"
    assert result["output"][1]["type"] == "function_call"
    assert result["output"][1]["name"] == "read_file"
    assert result["usage"]["total_tokens"] == 7
    events = list(responses_api.as_sse(result))
    assert any("response.output_text.delta" in event for event in events)
    assert any("response.completed" in event for event in events)


def test_escaped_model_list_is_repaired_with_backup(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({
        "providers": [{
            "name": "DeepSeek",
            "base_url": "https://api.example/v1",
            "models": [{"id": r"deepseek-chat\ndeepseek-reasoner", "label": r"deepseek-chat\ndeepseek-reasoner"}],
        }],
        "active_model": r"DeepSeek/deepseek-chat\ndeepseek-reasoner",
    }), encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "_get_key", lambda _name: "test-key")

    providers = config.get_providers()

    assert [m["id"] for m in providers[0]["models"]] == ["deepseek-chat", "deepseek-reasoner"]
    assert config.get_active_model() == "DeepSeek/deepseek-chat"
    backup = tmp_path / "config.json.ai-bridge-models-backup.json"
    assert backup.exists()
    original = json.loads(backup.read_text(encoding="utf-8"))
    assert len(original["providers"][0]["models"]) == 1


def test_claude_desktop_model_list_uses_compatible_aliases_and_preserves_upstream_ids(monkeypatch):
    monkeypatch.setattr(config, "get_providers", lambda: [{
        "name": "DeepSeek",
        "models": [
            {"id": "deepseek-flash", "label": "DeepSeek Flash"},
            {"id": "deepseek-v4-pro", "label": "DeepSeek V4 Pro"},
        ],
    }])
    monkeypatch.setattr(config, "load", lambda: {"active_model": "DeepSeek/deepseek-flash"})
    models = config.claude_desktop_models()

    assert models == [
        {"name": "claude-sonnet-5", "labelOverride": "DeepSeek Flash · DeepSeek (AI Bridge)"},
        {"name": "claude-sonnet-5-2", "labelOverride": "DeepSeek V4 Pro · DeepSeek (AI Bridge)"},
    ]
    assert config.get_provider_for_model("claude-sonnet-5")["name"] == "DeepSeek"
    assert config.get_model_id("claude-sonnet-5") == "deepseek-flash"
    assert config.get_model_id("claude-sonnet-5-2") == "deepseek-v4-pro"


def test_responses_developer_role_is_mapped_to_supported_system_role():
    request = responses_api.to_chat_request({
        "input": [
            {"type": "message", "role": "developer", "content": [
                {"type": "input_text", "text": "Follow the user's instruction."}
            ]},
            {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "hello"}
            ]},
        ],
    }, "deepseek-flash")

    assert request["messages"] == [
        {"role": "system", "content": "Follow the user's instruction."},
        {"role": "user", "content": "hello"},
    ]
