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
    data = request.get_json(force=True, silent=True) or {}
    model = data.get('model', '')

    provider = cfg.get_provider_for_model(model)
    if provider is None:
        return jsonify({
            'type': 'error',
            'error': {'type': 'not_found_error', 'message': f'Model not found in AI Bridge: {model}'},
        }), 404

    actual_model = cfg.get_model_id(model) or (model.split('/', 1)[1] if '/' in model else model)
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
                        yield from translate.stream_to_anthropic(r.iter_lines(), model)
                except httpx.TimeoutException:
                    err = json.dumps({'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Request timed out'}})
                    yield f"event: error\ndata: {err}\n\n"
                except httpx.ConnectError as e:
                    err = json.dumps({'type': 'error', 'error': {'type': 'api_error', 'message': str(e)}})
                    yield f"event: error\ndata: {err}\n\n"

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


@app.route('/v1/models')
def models():
    """List all configured models in OpenAI format."""
    data = []
    for p in cfg.get_providers():
        for m in p.get('models', []):
            label = m.get('label', m['id'])
            data.append({
                'id': f"{label} · {p['name']}",
                'object': 'model',
                'created': 0,
                'owned_by': p['name'],
            })
    return jsonify({'object': 'list', 'data': data})


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
    req = {**data, 'model': actual_model}

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
