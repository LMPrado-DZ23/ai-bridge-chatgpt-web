import json
import logging
import os
from pathlib import Path
from typing import Optional

_keyring_error: str = ''

try:
    import keyring
    import keyring.errors
    _KEYRING_AVAILABLE = True
except ImportError as e:
    _KEYRING_AVAILABLE = False
    _keyring_error = str(e)
    logging.getLogger(__name__).warning(
        'keyring not available — API keys will NOT be persisted: %s', e
    )

log = logging.getLogger(__name__)

CONFIG_DIR = Path.home() / '.ai-bridge'
CONFIG_FILE = CONFIG_DIR / 'config.json'
DEFAULT_PORT = 38771
_KEYRING_SERVICE = 'ai-bridge'


def _default() -> dict:
    return {'providers': [], 'port': DEFAULT_PORT}


def load() -> dict:
    if not CONFIG_FILE.exists():
        return _default()
    try:
        return json.loads(CONFIG_FILE.read_text('utf-8'))
    except Exception:
        return _default()


def save(data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_DIR / f'.tmp-{os.getpid()}'
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), 'utf-8')
    tmp.replace(CONFIG_FILE)


# ─── Keyring helpers ────────────────────────────────────────────────────────

def _set_key(name: str, api_key: str):
    if not _KEYRING_AVAILABLE:
        return
    try:
        keyring.set_password(_KEYRING_SERVICE, name, api_key)
    except Exception as e:
        log.warning('keyring: could not save key for %r: %s', name, e)


def _get_key(name: str) -> str:
    if not _KEYRING_AVAILABLE:
        return ''
    try:
        return keyring.get_password(_KEYRING_SERVICE, name) or ''
    except Exception:
        return ''


def _delete_key(name: str):
    if not _KEYRING_AVAILABLE:
        return
    try:
        keyring.delete_password(_KEYRING_SERVICE, name)
    except Exception:
        pass


def _migrate_legacy_keys(data: dict) -> bool:
    """Move plaintext api_key fields from JSON into the system keyring.
    Returns True if any migration occurred (caller should re-save)."""
    migrated = False
    for p in data.get('providers', []):
        if 'api_key' in p:
            key = p.pop('api_key')
            if key:
                _set_key(p['name'], key)
            migrated = True
    return migrated


# ─── Public API ─────────────────────────────────────────────────────────────

def get_providers() -> list:
    data = load()
    providers = data.get('providers', [])

    if any('api_key' in p for p in providers):
        if _migrate_legacy_keys(data):
            save(data)
            providers = data.get('providers', [])

    return [{**p, 'api_key': _get_key(p['name'])} for p in providers]


def get_api_key(name: str) -> str:
    """Return the stored API key for a provider, or '' if not found."""
    return _get_key(name)


def get_port() -> int:
    return load().get('port', DEFAULT_PORT)


def add_provider(name: str, base_url: str, api_key: str, models: list):
    _set_key(name, api_key)
    data = load()
    providers = [p for p in data.get('providers', []) if p['name'] != name]
    providers.append({
        'name': name,
        'base_url': base_url.rstrip('/'),
        'models': models,
        # api_key stored in system keyring, not here
    })
    data['providers'] = providers
    save(data)


def remove_provider(name: str):
    _delete_key(name)
    data = load()
    data['providers'] = [p for p in data.get('providers', []) if p['name'] != name]
    save(data)


def get_provider_for_model(model: str) -> Optional[dict]:
    for p in get_providers():
        for m in p.get('models', []):
            label = m.get('label', m['id'])
            if model in (
                f"{p['name']}/{m['id']}",   # Claude Code: Provider/id
                m['id'],                      # plain id
                f"{label} · {p['name']}",    # Codex picker: Label · Provider
            ):
                return p
    return None


def get_model_id(model: str) -> Optional[str]:
    """Return the actual model ID (to send to the provider) for any supported format."""
    for p in get_providers():
        for m in p.get('models', []):
            label = m.get('label', m['id'])
            if model in (
                f"{p['name']}/{m['id']}",
                m['id'],
                f"{label} · {p['name']}",
            ):
                return m['id']
    return None


def all_picker_models() -> list:
    result = []
    for p in get_providers():
        for m in p.get('models', []):
            result.append({
                'value': f"{p['name']}/{m['id']}",
                'label': f"{m.get('label', m['id'])} · {p['name']}",
            })
    return result
