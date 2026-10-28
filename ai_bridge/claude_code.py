"""
Integrates with Claude Code via ~/.claude/settings.json.
"""
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

CLAUDE_DIR = Path.home() / '.claude'
SETTINGS_FILE = CLAUDE_DIR / 'settings.json'
_BRIDGE_KEY = '_ai_bridge'


@dataclass
class ClaudeState:
    connected: bool
    port: Optional[int]
    model_count: int


def _load() -> dict:
    if not SETTINGS_FILE.exists():
        return {}
    try:
        return json.loads(SETTINGS_FILE.read_text('utf-8'))
    except Exception:
        return {}


def _write(data: dict):
    CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CLAUDE_DIR / f'.ai-bridge-{os.getpid()}.tmp'
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), 'utf-8')
    tmp.replace(SETTINGS_FILE)


def connect(port: int, models: list):
    """Configure Claude Code to route through the local AI Bridge proxy."""
    data = _load()

    env = data.setdefault('env', {})
    original_url = env.get('ANTHROPIC_BASE_URL')
    env['ANTHROPIC_BASE_URL'] = f'http://127.0.0.1:{port}'

    data['modelPicker'] = {'replaceBuiltInOptions': models}

    data[_BRIDGE_KEY] = {
        'port': port,
        'original_base_url': original_url,
    }

    _write(data)


def disconnect():
    """Restore Claude Code settings to their pre-bridge state."""
    data = _load()

    bridge = data.pop(_BRIDGE_KEY, {})
    original_url = bridge.get('original_base_url')

    env = data.get('env', {})
    env.pop('ANTHROPIC_BASE_URL', None)
    if original_url:
        env['ANTHROPIC_BASE_URL'] = original_url
    if not env:
        data.pop('env', None)
    else:
        data['env'] = env

    data.pop('modelPicker', None)

    _write(data)


def state() -> ClaudeState:
    data = _load()
    bridge = data.get(_BRIDGE_KEY)
    if bridge:
        count = len(data.get('modelPicker', {}).get('replaceBuiltInOptions', []))
        return ClaudeState(connected=True, port=bridge.get('port'), model_count=count)
    return ClaudeState(connected=False, port=None, model_count=0)


def is_connected() -> bool:
    return state().connected
