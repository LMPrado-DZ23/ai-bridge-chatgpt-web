import json
import logging
import os
import shutil
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


def _migrate_escaped_model_lines(data: dict) -> bool:
    """Repair model lists pasted with literal \\n separators instead of actual newlines."""
    changed = False
    for provider in data.get('providers', []):
        repaired = []
        for model in provider.get('models', []):
            model_id = str(model.get('id', ''))
            label = str(model.get('label', model_id))
            if r'\n' in model_id and label == model_id:
                parts = [part.strip() for part in model_id.split(r'\n') if part.strip()]
                if len(parts) > 1:
                    repaired.extend({'id': part, 'label': part} for part in parts)
                    changed = True
                    continue
            repaired.append(model)
        provider['models'] = repaired
    if changed:
        selected = data.get('active_model', '')
        for provider in data.get('providers', []):
            prefix = provider.get('name', '') + '/'
            if selected.startswith(prefix) and r'\n' in selected:
                first = selected[len(prefix):].split(r'\n', 1)[0].strip()
                data['active_model'] = prefix + first
                break
    return changed


# ─── Public API ─────────────────────────────────────────────────────────────

def get_providers() -> list:
    data = load()
    providers = data.get('providers', [])

    if any('api_key' in p for p in providers) and _migrate_legacy_keys(data):
        # Complete the existing keyring migration before creating any backup,
        # so a backup never duplicates plaintext API credentials.
        save(data)
        providers = data.get('providers', [])

    if _migrate_escaped_model_lines(data):
        if CONFIG_FILE.is_file():
            backup = CONFIG_FILE.with_name('config.json.ai-bridge-models-backup.json')
            if not backup.exists():
                shutil.copy2(CONFIG_FILE, backup)
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


def get_active_model() -> str:
    """Return the selected model value, falling back safely to the first model."""
    models = all_picker_models()
    if not models:
        return ''
    selected = load().get('active_model')
    if selected and any(model['value'] == selected for model in models):
        return selected
    return models[0]['value']


def set_active_model(value: str):
    if not any(model['value'] == value for model in all_picker_models()):
        raise ValueError('O modelo selecionado não existe nos provedores salvos.')
    data = load()
    data['active_model'] = value
    save(data)


def _claude_desktop_alias_map() -> dict:
    """Map Claude-compatible picker IDs to real upstream provider/model IDs.

    Claude Desktop filters its gateway picker for Claude-shaped model IDs.
    The visible label still names the actual upstream model/provider; proxy
    requests are translated back through this map.
    """
    models = all_picker_models()
    active = get_active_model()
    models.sort(key=lambda model: (model['value'] != active, model['value'].casefold()))
    aliases = {}
    for index, model in enumerate(models, start=1):
        alias = 'claude-sonnet-5' if index == 1 else f'claude-sonnet-5-{index}'
        aliases[alias] = model['value']
    # Claude Desktop can retain the previously selected gateway model across
    # profile changes. Keep the known Invision preset ID routable during
    # migration, but do not advertise it as an extra picker option.
    if active:
        aliases['claude-opus-5-5'] = active
    return aliases


def claude_desktop_models() -> list:
    """Return Claude Desktop gateway model objects with valid IDs and honest labels."""
    aliases = _claude_desktop_alias_map()
    labels = {model['value']: model['label'] for model in all_picker_models()}
    return [
        {'name': alias, 'labelOverride': f"{labels[upstream]} (AI Bridge)"}
        for alias, upstream in aliases.items()
        if alias != 'claude-opus-5-5'
    ]


def claude_code_models() -> list:
    """Build the documented Claude Code modelPicker options format."""
    result = []
    for model in all_picker_models():
        option = {'model': model['value'], 'label': model['label']}
        if 'claude' not in model['value'].lower():
            option['behavesAs'] = 'claude-sonnet-5'
        result.append(option)
    return result


def get_provider_for_model(model: str) -> Optional[dict]:
    model = _claude_desktop_alias_map().get(model, model)
    for provider in get_providers():
        for configured_model in provider.get('models', []):
            label = configured_model.get('label', configured_model['id'])
            if model in (
                f"{provider['name']}/{configured_model['id']}",
                configured_model['id'],
                f"{label} · {provider['name']}",
            ):
                return provider
    return None


def get_model_id(model: str) -> Optional[str]:
    """Return the actual upstream model ID for any supported client model ID."""
    model = _claude_desktop_alias_map().get(model, model)
    for provider in get_providers():
        for configured_model in provider.get('models', []):
            label = configured_model.get('label', configured_model['id'])
            if model in (
                f"{provider['name']}/{configured_model['id']}",
                configured_model['id'],
                f"{label} · {provider['name']}",
            ):
                return configured_model['id']
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
