"""
Local HTTP proxy that implements the Anthropic Messages API and routes to
OpenAI-compatible providers based on the model prefix.
"""
import json
import socket
import threading
import logging

from flask import Flask, request, Response, jsonify
import httpx

from . import config as cfg
from . import translate
from . import auth
from . import turns as _turns
from . import model_cache as _cache
from . import skills as _skills

app = Flask(__name__)
log = logging.getLogger(__name__)

_thread: threading.Thread | None = None
_port: int | None = None

# ─── Routes ────────────────────────────────────────────────────────────────────

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'port': _port})


@app.route('/v1/messages', methods=['POST'])
def messages():
    if _turns.tracker.is_draining():
        return jsonify({
            'type': 'error',
            'error': {'type': 'overloaded_error', 'message': 'Server is draining — try again shortly'},
        }), 503

    turn_id = _turns.tracker.begin()
    if turn_id is None:
        return jsonify({
            'type': 'error',
            'error': {'type': 'overloaded_error', 'message': 'Server is draining — try again shortly'},
        }), 503

    # Streaming responses are consumed after this route returns. Keep the turn
    # registered until the generator finishes; the outer finally must not end it
    # before Flask iterates the response body.
    streaming_response = False
    try:
        data = request.get_json(force=True, silent=True) or {}
        model = data.get('model', '')

        provider = cfg.get_provider_for_model(model)
        if provider is None:
            return jsonify({
                'type': 'error',
                'error': {'type': 'not_found_error', 'message': f'Model not found in AI Bridge: {model}'},
            }), 404

        actual_model = cfg.get_model_id(model) or (model.split('/', 1)[1] if '/' in model else model)
        data = _skills.inject_into_anthropic_request(data)
        oai_req = translate.to_openai_request(data, actual_model)

        headers = {
            'Authorization': f"Bearer {provider['api_key']}",
            'Content-Type': 'application/json',
            'User-Agent': 'ai-bridge/1.0.0',
        }
        url = provider['base_url'].rstrip('/') + '/chat/completions'

        stream = data.get('stream', False)

        try:
            if stream:
                def generate():
                    try:
                        with httpx.stream(
                            'POST', url,
                            json=oai_req,
                            headers=headers,
                            timeout=httpx.Timeout(30.0, read=300.0),
                        ) as r:
                            if r.status_code != 200:
                                body = r.read()
                                err = json.dumps({
                                    'type': 'error',
                                    'error': {'type': 'api_error', 'message': body.decode('utf-8', errors='replace')},
                                })
                                yield f"event: error\ndata: {err}\n\n"
                                return
                            for chunk in translate.stream_to_anthropic(r.iter_lines(), model):
                                if _turns.tracker.is_cancelled(turn_id):
                                    err = json.dumps({'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Turn cancelled'}})
                                    yield f"event: error\ndata: {err}\n\n"
                                    return
                                yield chunk
                    except httpx.TimeoutException:
                        err = json.dumps({'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Request timed out'}})
                        yield f"event: error\ndata: {err}\n\n"
                    except httpx.ConnectError as e:
                        err = json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': str(e)}})
                        yield f"event: error\ndata: {err}\n\n"
                    finally:
                        _turns.tracker.end(turn_id)

                streaming_response = True
                return Response(
                    generate(),
                    content_type='text/event-stream',
                    headers={
                        'Cache-Control': 'no-cache',
                        'X-Accel-Buffering': 'no',
                        'Connection': 'keep-alive',
                    },
                )

            else:
                if _turns.tracker.is_cancelled(turn_id):
                    return jsonify({
                        'type': 'error',
                        'error': {'type': 'overloaded_error', 'message': 'Turn cancelled'},
                    }), 499
                resp = httpx.post(url, json=oai_req, headers=headers,
                                  timeout=httpx.Timeout(30.0, read=300.0))
                if resp.status_code != 200:
                    return jsonify({
                        'type': 'error',
                        'error': {'type': 'api_error', 'message': resp.text[:500]},
                    }), resp.status_code
                return jsonify(translate.to_anthropic_response(resp.json(), model))

        except httpx.TimeoutException:
            return jsonify({
                'type': 'error',
                'error': {'type': 'overloaded_error', 'message': 'Request timed out'},
            }), 529
        except httpx.ConnectError as e:
            return jsonify({
                'type': 'error',
                'error': {'type': 'api_error', 'message': f'Connection failed: {e}'},
            }), 502
        except Exception as e:
            log.exception('Unexpected proxy error')
            return jsonify({
                'type': 'error',
                'error': {'type': 'api_error', 'message': str(e)},
            }), 500
    finally:
        if not streaming_response:
            _turns.tracker.end(turn_id)


@app.route('/v1/models')
def models():
    """List all configured models in OpenAI format."""
    data = []
    for p in cfg.get_providers():
        for m in p.get('models', []):
            label = m.get('label', m['id'])
            data.append({
                'id': f"{p['name']}/{m['id']}",
                'object': 'model',
                'created': 0,
                'owned_by': p['name'],
                'display_name': f"{label} · {p['name']}",
            })
    return jsonify({'object': 'list', 'data': data})


@app.route('/v1/codex-models')
def codex_models():
    """Return non-secret Codex model metadata for the loopback-only model picker.

    Codex may fetch model_catalog_url without forwarding provider authorization.
    The route exposes only configured model IDs/labels, never provider credentials.
    """
    catalog = []
    for provider in cfg.get_providers():
        for model in provider.get('models', []):
            model_id = f"{provider['name']}/{model['id']}"
            label = model.get('label', model['id'])
            catalog.append({
                'slug': model_id,
                # Match Codex Desktop's local model_catalog_json schema.
                'displayName': f"{label} · {provider['name']}",
                'display_name': f"{label} · {provider['name']}",
                'provider': 'ai_bridge',
                'hidden': False,
                'description': f"{label} via AI Bridge ({provider['name']}).",
                'default_reasoning_level': 'medium',
                'supported_reasoning_levels': [
                    {'effort': 'low', 'description': 'Respostas mais rápidas.'},
                    {'effort': 'medium', 'description': 'Equilíbrio entre velocidade e profundidade.'},
                    {'effort': 'high', 'description': 'Maior profundidade para tarefas complexas.'},
                ],
                'shell_type': 'shell_command',
                'visibility': 'list',
                'supported_in_api': True,
                'priority': len(catalog) + 1,
                'additional_speed_tiers': [],
                'service_tiers': [],
                'default_service_tier': None,
                'availability_nux': None,
                'upgrade': None,
                'support_verbosity': False,
                'default_verbosity': None,
                'apply_patch_tool_type': 'freeform',
                'web_search_tool_type': 'text',
                'truncation_policy': {'mode': 'tokens', 'limit': 10000},
                'effective_context_window_percent': 95,
                'experimental_supported_tools': [],
                'input_modalities': ['text'],
                'base_instructions': 'Modelo externo conectado pelo AI Bridge. Siga as instruções do usuário.',
            })
    return jsonify({'models': catalog})


@app.route('/v1/responses', methods=['POST'])
def responses():
    """Responses API adapter used by Codex; upstream remains OpenAI Chat Completions."""
    from . import responses_api
    data = request.get_json(force=True, silent=True) or {}
    model = str(data.get('model', ''))
    provider = cfg.get_provider_for_model(model)
    if provider is None:
        return jsonify({
            'error': {'message': f'Model not found in AI Bridge: {model}', 'type': 'invalid_request_error'}
        }), 404
    actual_model = cfg.get_model_id(model) or model
    try:
        chat_request = responses_api.to_chat_request(data, actual_model)
    except ValueError as exc:
        return jsonify({'error': {'message': str(exc), 'type': 'invalid_request_error'}}), 400

    headers = {
        'Authorization': f"Bearer {provider['api_key']}",
        'Content-Type': 'application/json',
        'User-Agent': 'ai-bridge/1.0.0',
    }
    url = provider['base_url'].rstrip('/') + '/chat/completions'
    try:
        # The adapter returns a valid Responses event sequence for streaming clients.
        # Upstream inference completes first because the current translator is non-streaming.
        resp = httpx.post(
            url, json=chat_request, headers=headers,
            timeout=httpx.Timeout(30.0, read=300.0),
        )
        if resp.status_code != 200:
            return jsonify({
                'error': {'message': resp.text[:500], 'type': 'upstream_error'}
            }), resp.status_code
        response_data = responses_api.from_chat_response(resp.json(), model)
        if data.get('stream'):
            return Response(
                responses_api.as_sse(response_data),
                content_type='text/event-stream',
                headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
            )
        return jsonify(response_data)
    except httpx.TimeoutException:
        return jsonify({'error': {'message': 'Request timed out', 'type': 'server_error'}}), 529
    except httpx.ConnectError:
        return jsonify({'error': {'message': 'Connection failed to provider', 'type': 'server_error'}}), 502
    except Exception as exc:
        log.exception('Unexpected error (Responses API)')
        return jsonify({'error': {'message': str(exc), 'type': 'server_error'}}), 500


@app.route('/v1/chat/completions', methods=['POST'])
def chat_completions():
    """OpenAI-compatible endpoint — for Codex, Cursor and other OpenAI clients."""
    data = request.get_json(force=True, silent=True) or {}
    model = data.get('model', '')

    provider = cfg.get_provider_for_model(model)
    if provider is None:
        return jsonify({
            'error': {
                'message': f'Model not found in AI Bridge: {model}',
                'type': 'invalid_request_error',
                'code': 'model_not_found',
            }
        }), 404

    actual_model = cfg.get_model_id(model) or (model.split('/', 1)[1] if '/' in model else model)
    messages = _skills.inject_into_messages(data.get('messages', []))
    req = {**data, 'model': actual_model, 'messages': messages}

    headers = {
        'Authorization': f"Bearer {provider['api_key']}",
        'Content-Type': 'application/json',
        'User-Agent': 'ai-bridge/1.0.0',
    }
    url = provider['base_url'].rstrip('/') + '/chat/completions'
    stream = data.get('stream', False)

    try:
        if stream:
            def gen_oai():
                try:
                    with httpx.stream(
                        'POST', url, json=req, headers=headers,
                        timeout=httpx.Timeout(30.0, read=300.0),
                    ) as r:
                        if r.status_code != 200:
                            body = r.read()
                            yield f"data: {json.dumps({'error': {'message': body.decode('utf-8', errors='replace')}})}\n\n"
                            yield 'data: [DONE]\n\n'
                            return
                        for line in r.iter_lines():
                            if line:
                                yield line + '\n\n'
                except Exception as exc:
                    yield f"data: {json.dumps({'error': {'message': str(exc)}})}\n\n"
                    yield 'data: [DONE]\n\n'

            return Response(
                gen_oai(),
                content_type='text/event-stream',
                headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'},
            )

        resp = httpx.post(url, json=req, headers=headers,
                          timeout=httpx.Timeout(30.0, read=300.0))
        if resp.status_code != 200:
            return jsonify({'error': {'message': resp.text[:500]}}), resp.status_code
        return jsonify(resp.json())

    except httpx.TimeoutException:
        return jsonify({'error': {'message': 'Request timed out', 'type': 'server_error'}}), 529
    except httpx.ConnectError as e:
        return jsonify({'error': {'message': f'Connection failed: {e}', 'type': 'server_error'}}), 502
    except Exception as e:
        log.exception('Unexpected proxy error (chat/completions)')
        return jsonify({'error': {'message': str(e), 'type': 'server_error'}}), 500


# ─── Admin endpoints ────────────────────────────────────────────────────────────

@app.route('/admin/status')
@auth.require_admin
def admin_status():
    return jsonify({
        'active_turns': _turns.tracker.active_count(),
        'turn_ids': _turns.tracker.active_ids(),
        'draining': _turns.tracker.is_draining(),
        'port': _port,
    })


@app.route('/admin/token')
@auth.require_admin
def admin_token():
    return jsonify({'token': auth.get_token()})


@app.route('/admin/drain', methods=['POST'])
@auth.require_admin
def admin_drain():
    _turns.tracker.start_drain()
    return jsonify({'draining': True, 'active_turns': _turns.tracker.active_count()})


@app.route('/admin/undrain', methods=['POST'])
@auth.require_admin
def admin_undrain():
    _turns.tracker.stop_drain()
    return jsonify({'draining': False})


@app.route('/admin/cancel-turns', methods=['POST'])
@auth.require_admin
def admin_cancel_turns():
    count = _turns.tracker.cancel_all()
    return jsonify({'cancelled': count})


@app.route('/admin/cancel/<turn_id>', methods=['POST'])
@auth.require_admin
def admin_cancel_turn(turn_id: str):
    ok = _turns.tracker.cancel(turn_id)
    return jsonify({'cancelled': ok}), (200 if ok else 404)


# ─── Server lifecycle ───────────────────────────────────────────────────────────

def _find_port(preferred: int) -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(('127.0.0.1', preferred))
        s.close()
        return preferred
    except OSError:
        s.close()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def start() -> int:
    global _thread, _port
    if _thread and _thread.is_alive():
        return _port  # type: ignore[return-value]

    preferred = cfg.get_port()
    _port = _find_port(preferred)

    def run():
        wz_log = logging.getLogger('werkzeug')
        wz_log.setLevel(logging.ERROR)
        app.run(host='127.0.0.1', port=_port, threaded=True, use_reloader=False)

    _thread = threading.Thread(target=run, daemon=True, name='ai-bridge-proxy')
    _thread.start()
    return _port  # type: ignore[return-value]


def get_port() -> int | None:
    return _port


def is_running() -> bool:
    return _thread is not None and _thread.is_alive()
