"""
pywebview JS bridge — all public methods are callable from the frontend.
"""
import threading
import webbrowser
from urllib.parse import urlparse

import httpx

from . import config as cfg
from . import proxy
from . import claude_code
from . import claude_desktop
from . import codex as codex_mod

_PRESETS = [
    {
        'name': 'DeepSeek',
        'base_url': 'https://api.deepseek.com/v1',
        'models': 'deepseek-chat|DeepSeek Chat\ndeepseek-reasoner|DeepSeek Reasoner',
    },
    {
        'name': 'OpenAI',
        'base_url': 'https://api.openai.com/v1',
        'models': 'gpt-4o|GPT-4o\ngpt-4o-mini|GPT-4o Mini\no1|o1\no3-mini|o3 Mini',
    },
    {
        'name': 'Groq',
        'base_url': 'https://api.groq.com/openai/v1',
        'models': 'llama-3.3-70b-versatile|Llama 3.3 70B\nllama-3.1-8b-instant|Llama 3.1 8B\ngemma2-9b-it|Gemma 2 9B',
    },
    {
        'name': 'Gemini',
        'base_url': 'https://generativelanguage.googleapis.com/v1beta/openai',
        'models': 'gemini-2.0-flash|Gemini 2.0 Flash\ngemini-2.5-pro|Gemini 2.5 Pro\ngemini-2.5-flash|Gemini 2.5 Flash',
    },
    {
        'name': 'Mistral',
        'base_url': 'https://api.mistral.ai/v1',
        'models': 'mistral-large-latest|Mistral Large\nmistral-small-latest|Mistral Small\ncodestral-latest|Codestral',
    },
    {
        'name': 'OpenRouter',
        'base_url': 'https://openrouter.ai/api/v1',
        'models': 'openai/gpt-4o|GPT-4o (OpenRouter)\nanthropic/claude-opus-4-5|Claude Opus 4.5 (OpenRouter)',
    },
    {
        'name': 'Together',
        'base_url': 'https://api.together.xyz/v1',
        'models': 'meta-llama/Llama-3.3-70B-Instruct-Turbo|Llama 3.3 70B Turbo\nQwen/Qwen2.5-Coder-32B-Instruct|Qwen 2.5 Coder 32B',
    },
    {
        'name': 'Ollama',
        'base_url': 'http://localhost:11434/v1',
        'models': 'llama3.2|Llama 3.2\nqwen2.5-coder:7b|Qwen 2.5 Coder 7B\ndeepseek-r1:8b|DeepSeek R1 8B',
    },
    {
        'name': 'Claude Gateway',
        'base_url': 'https://claude-ss.ia.br/v1',
        'models': (
            'claude-sonnet-5|Claude Sonnet 5\n'
            'claude-opus-5-5|Claude Opus 5.5\n'
            'claude-fable-5-1|Claude Fable 5.1\n'
            'claude-haiku-4-5|Claude Haiku 4.5\n'
            'claude-opus-5|Claude Opus 5\n'
            'gpt-6-astra|GPT-6 Astra'
        ),
    },
]

# Target metadata — order determines display order
_TARGETS_META = [
    {
        'id': 'claude_code',
        'name': 'Claude Code',
        'desc': 'Terminal e IDEs',
        'icon': '🤖',
        'protocol': 'anthropic',
    },
    {
        'id': 'claude_desktop',
        'name': 'Claude Desktop',
        'desc': 'App para computador',
        'icon': '🔷',
        'protocol': 'anthropic',
    },
    {
        'id': 'codex',
        'name': 'Codex',
        'desc': 'App, terminal e IDEs',
        'icon': '⚡',
        'protocol': 'openai',
    },
]


def _target_state(target_id: str) -> dict:
    if target_id == 'claude_code':
        s = claude_code.state()
        meta = _TARGETS_META[0]
        return {**meta, 'installed': True, 'connected': s.connected, 'port': s.port}
    if target_id == 'claude_desktop':
        s = claude_desktop.state()
        meta = _TARGETS_META[1]
        return {**meta, 'installed': s.installed, 'connected': s.connected, 'port': s.port}
    if target_id == 'codex':
        s = codex_mod.state()
        meta = _TARGETS_META[2]
        return {**meta, 'installed': s.installed, 'connected': s.connected, 'port': s.port}
    return {}


def _is_valid_url(url: str) -> bool:
    try:
        p = urlparse(url)
        return p.scheme in ('http', 'https') and bool(p.netloc)
    except Exception:
        return False


class Bridge:
    def __init__(self):
        self._lock = threading.Lock()
        self._window = None
        self._tray = None

    def set_window(self, window):
        self._window = window

    def set_tray(self, tray):
        self._tray = tray

    # ── Window controls ──────────────────────────────────────────────────────

    def minimize(self):
        if self._window:
            self._window.minimize()

    def close(self):
        """Hide the window to the system tray (if available) instead of destroying it."""
        if self._window:
            if self._tray:
                self._window.hide()
            else:
                self._window.destroy()

    def hide_window(self):
        self.close()

    def quit(self):
        """Fully exit the application (called from tray menu)."""
        if self._tray:
            self._tray.stop()
        if self._window:
            self._window.destroy()

    def open_url(self, url: str):
        if url and (url.startswith('https://') or url.startswith('http://localhost')):
            webbrowser.open(url)

    # ── State ────────────────────────────────────────────────────────────────

    def state(self) -> dict:
        try:
            providers = cfg.get_providers()
            summaries = [
                {
                    'name': p['name'],
                    'base_url': p['base_url'],
                    'models': [
                        {'id': m['id'], 'label': m.get('label', m['id'])}
                        for m in p.get('models', [])
                    ],
                }
                for p in providers
            ]

            targets = [_target_state(t['id']) for t in _TARGETS_META]
            any_connected = any(t['connected'] for t in targets)
            connected_port = next((t['port'] for t in targets if t['connected']), proxy.get_port())

            return {
                'ok': True,
                'providers': summaries,
                'targets': targets,
                'model_count': len(cfg.all_picker_models()),
                # legacy fields for any callers that still check them
                'connected': any_connected,
                'port': connected_port,
                'proxy_running': proxy.is_running(),
            }
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ── Provider management ──────────────────────────────────────────────────

    def get_provider_detail(self, name: str) -> dict:
        """Return provider data for editing — API key is never returned."""
        try:
            for p in cfg.get_providers():
                if p['name'] == name:
                    models_raw = '\n'.join(
                        f"{m['id']}|{m['label']}" if m.get('label') and m['label'] != m['id'] else m['id']
                        for m in p.get('models', [])
                    )
                    stored_key = cfg.get_api_key(name)
                    return {
                        'ok': True,
                        'name': p['name'],
                        'base_url': p['base_url'],
                        'models_raw': models_raw,
                        'has_key': bool(stored_key),
                        # api_key is intentionally NEVER returned to the frontend
                    }
            return {'ok': False, 'error': f'Provedor "{name}" não encontrado'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    def add_provider(self, name: str, base_url: str, api_key: str, models_raw: str) -> dict:
        with self._lock:
            try:
                name = name.strip()
                base_url = base_url.strip().rstrip('/')
                api_key = api_key.strip()

                if not name:
                    return {'ok': False, 'error': 'Nome do provedor é obrigatório'}
                if not base_url:
                    return {'ok': False, 'error': 'URL base é obrigatória'}
                if not _is_valid_url(base_url):
                    return {'ok': False, 'error': 'URL inválida. Use o formato https://api.example.com/v1'}

                # When editing, a blank key means "keep the existing key"
                if not api_key:
                    existing = next((p for p in cfg.get_providers() if p['name'] == name), None)
                    if existing:
                        api_key = existing.get('api_key', '')
                if not api_key:
                    return {'ok': False, 'error': 'Chave de API é obrigatória'}

                models = []
                for line in models_raw.strip().splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    if '|' in line:
                        mid, label = line.split('|', 1)
                        models.append({'id': mid.strip(), 'label': label.strip()})
                    else:
                        models.append({'id': line, 'label': line})

                if not models:
                    return {'ok': False, 'error': 'Adicione pelo menos um modelo'}

                cfg.add_provider(name, base_url, api_key, models)
                self._sync_connected_targets()
                return {'ok': True}
            except Exception as e:
                return {'ok': False, 'error': str(e)}

    def remove_provider(self, name: str) -> dict:
        with self._lock:
            try:
                cfg.remove_provider(name)
                models = cfg.all_picker_models()
                if models:
                    self._sync_connected_targets()
                else:
                    # No providers left — disconnect everything
                    self._disconnect_all()
                return {'ok': True}
            except Exception as e:
                return {'ok': False, 'error': str(e)}

    def _sync_connected_targets(self):
        """Push updated model list to all currently-connected targets."""
        if not proxy.is_running():
            return
        port = proxy.get_port()
        models = cfg.all_picker_models()
        if claude_code.is_connected():
            claude_code.connect(port, models)
        if codex_mod.is_connected():
            codex_mod.update_models()

    def _disconnect_all(self):
        if claude_code.is_connected():
            claude_code.disconnect()
        if claude_desktop.is_connected():
            claude_desktop.disconnect()
        if codex_mod.is_connected():
            codex_mod.disconnect()

    def test_provider(self, base_url: str, api_key: str, provider_name: str = None) -> dict:
        """Test connectivity. If api_key is blank and provider_name is given, uses the stored key."""
        if not api_key and provider_name:
            api_key = cfg.get_api_key(provider_name) or ''
        if not api_key:
            return {'ok': False, 'error': 'Chave API não informada.'}
        try:
            url = base_url.rstrip('/') + '/models'
            resp = httpx.get(
                url,
                headers={'Authorization': f'Bearer {api_key}', 'User-Agent': 'ai-bridge/1.0.0'},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                ids = [m.get('id', '') for m in data.get('data', [])]
                return {'ok': True, 'models': ids[:30]}
            return {'ok': False, 'error': f'HTTP {resp.status_code}'}
        except httpx.ConnectError:
            return {'ok': False, 'error': 'Não foi possível conectar. Verifique a URL.'}
        except httpx.TimeoutException:
            return {'ok': False, 'error': 'Timeout ao conectar.'}
        except Exception as e:
            return {'ok': False, 'error': str(e)}

    # ── Multi-target connect ─────────────────────────────────────────────────

    def connect_target(self, target_id: str) -> dict:
        with self._lock:
            try:
                if not cfg.get_providers():
                    return {'ok': False, 'error': 'Adicione pelo menos um provedor antes de conectar'}

                port = proxy.start()
                models = cfg.all_picker_models()

                if target_id == 'claude_code':
                    claude_code.connect(port, models)
                elif target_id == 'claude_desktop':
                    claude_desktop.connect(port)
                elif target_id == 'codex':
                    codex_mod.connect(port)
                else:
                    return {'ok': False, 'error': f'Target desconhecido: {target_id}'}

                return {'ok': True, 'port': port}
            except Exception as e:
                return {'ok': False, 'error': str(e)}

    def disconnect_target(self, target_id: str) -> dict:
        with self._lock:
            try:
                if target_id == 'claude_code':
                    claude_code.disconnect()
                elif target_id == 'claude_desktop':
                    claude_desktop.disconnect()
                elif target_id == 'codex':
                    codex_mod.disconnect()
                else:
                    return {'ok': False, 'error': f'Target desconhecido: {target_id}'}
                return {'ok': True}
            except Exception as e:
                return {'ok': False, 'error': str(e)}

    # ── Legacy single-target shims (backward compat) ─────────────────────────

    def connect_claude(self) -> dict:
        return self.connect_target('claude_code')

    def disconnect_claude(self) -> dict:
        return self.disconnect_target('claude_code')

    # ── Presets ──────────────────────────────────────────────────────────────

    def get_presets(self) -> dict:
        return {'ok': True, 'presets': _PRESETS}
