"""Integration tests for the proxy Flask routes using the test client."""
import json
from unittest.mock import MagicMock, patch, call
import httpx
import pytest

from ai_bridge.proxy import app as proxy_app

# ── Shared fixtures ────────────────────────────────────────────────────────────

FAKE_PROVIDER = {
    'name': 'TestProvider',
    'base_url': 'https://api.test.example/v1',
    'api_key': 'test-key-xyz',
    'models': [{'id': 'test-model', 'label': 'Test Model'}],
}

OAI_TEXT_RESPONSE = {
    'id': 'chatcmpl-abc',
    'object': 'chat.completion',
    'choices': [{
        'index': 0,
        'message': {'role': 'assistant', 'content': 'Hello, world!', 'tool_calls': None},
        'finish_reason': 'stop',
    }],
    'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
}

OAI_TOOL_RESPONSE = {
    'id': 'chatcmpl-tool',
    'object': 'chat.completion',
    'choices': [{
        'index': 0,
        'message': {
            'role': 'assistant',
            'content': None,
            'tool_calls': [{
                'id': 'call_abc123',
                'type': 'function',
                'function': {'name': 'get_weather', 'arguments': '{"city":"London"}'},
            }],
        },
        'finish_reason': 'tool_calls',
    }],
    'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30},
}


class MockStreamCtx:
    """Context-manager mock for httpx.stream(...)."""
    def __init__(self, status_code: int = 200, lines: list | None = None):
        self.status_code = status_code
        self._lines = lines or []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_lines(self):
        return iter(self._lines)

    def read(self):
        return b'upstream error body'


@pytest.fixture
def client():
    proxy_app.config['TESTING'] = True
    with proxy_app.test_client() as c:
        yield c


def test_codex_model_catalog_uses_provider_model_ids(client, monkeypatch):
    monkeypatch.setattr('ai_bridge.proxy.cfg.get_providers', lambda: [{
        'name': 'DeepSeek',
        'models': [
            {'id': 'deepseek-flash', 'label': 'DeepSeek Flash'},
            {'id': 'deepseek-v4-pro', 'label': 'DeepSeek V4 Pro'},
        ],
    }])

    # Codex may fetch model_catalog_url without forwarding the provider token.
    # The route is loopback-only and exposes model metadata, never API keys.
    response = client.get('/v1/codex-models')

    assert response.status_code == 200
    assert client.get('/v1/codex-models', headers={
        'Authorization': 'Bearer ai-bridge-local'
    }).status_code == 200
    models = response.get_json()['models']
    assert [model['slug'] for model in models] == [
        'DeepSeek/deepseek-flash', 'DeepSeek/deepseek-v4-pro'
    ]
    assert models[0]['displayName'] == 'DeepSeek Flash · DeepSeek'
    assert models[0]['display_name'] == 'DeepSeek Flash · DeepSeek'
    assert models[0]['provider'] == 'ai_bridge'
    assert models[0]['hidden'] is False
    assert models[0]['supported_in_api'] is True
    assert models[0]['input_modalities'] == ['text']


# ── /health ────────────────────────────────────────────────────────────────────

def test_health(client):
    resp = client.get('/health')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['status'] == 'ok'


# ── /v1/messages ── non-streaming ─────────────────────────────────────────────

def test_messages_model_not_found(client):
    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=None):
        resp = client.post('/v1/messages',
                           data=json.dumps({'model': 'ghost-model', 'messages': []}),
                           content_type='application/json')
    assert resp.status_code == 404
    body = resp.get_json()
    assert body['type'] == 'error'
    assert body['error']['type'] == 'not_found_error'
    assert 'ghost-model' in body['error']['message']


def test_messages_text_response(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = OAI_TEXT_RESPONSE

    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.post', return_value=mock_resp) as mock_post:

        payload = {
            'model': 'TestProvider/test-model',
            'messages': [{'role': 'user', 'content': 'Hi'}],
            'max_tokens': 100,
        }
        resp = client.post('/v1/messages',
                           data=json.dumps(payload),
                           content_type='application/json')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['type'] == 'message'
    assert body['role'] == 'assistant'
    assert body['content'][0]['type'] == 'text'
    assert body['content'][0]['text'] == 'Hello, world!'
    assert body['stop_reason'] == 'end_turn'
    mock_post.assert_called_once()


def test_messages_tool_call_response(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = OAI_TOOL_RESPONSE

    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.post', return_value=mock_resp):

        payload = {
            'model': 'TestProvider/test-model',
            'messages': [{'role': 'user', 'content': "What's the weather?"}],
            'tools': [{'name': 'get_weather', 'description': 'Get weather',
                       'input_schema': {'type': 'object', 'properties': {'city': {'type': 'string'}}}}],
        }
        resp = client.post('/v1/messages',
                           data=json.dumps(payload),
                           content_type='application/json')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['stop_reason'] == 'tool_use'
    tool_block = next(b for b in body['content'] if b['type'] == 'tool_use')
    assert tool_block['name'] == 'get_weather'
    assert tool_block['input'] == {'city': 'London'}


def test_messages_provider_non_200(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.text = 'Bad request from upstream'

    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.post', return_value=mock_resp):

        resp = client.post('/v1/messages',
                           data=json.dumps({'model': 'TestProvider/test-model', 'messages': []}),
                           content_type='application/json')

    assert resp.status_code == 400
    body = resp.get_json()
    assert body['type'] == 'error'
    assert body['error']['type'] == 'api_error'


def test_messages_timeout(client):
    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.post', side_effect=httpx.TimeoutException('timed out')):

        resp = client.post('/v1/messages',
                           data=json.dumps({'model': 'TestProvider/test-model', 'messages': []}),
                           content_type='application/json')

    assert resp.status_code == 529
    body = resp.get_json()
    assert body['error']['type'] == 'overloaded_error'


def test_messages_connect_error(client):
    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.post',
               side_effect=httpx.ConnectError('connection refused')):

        resp = client.post('/v1/messages',
                           data=json.dumps({'model': 'TestProvider/test-model', 'messages': []}),
                           content_type='application/json')

    assert resp.status_code == 502
    body = resp.get_json()
    assert body['error']['type'] == 'api_error'


def test_messages_unexpected_exception(client):
    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.post', side_effect=RuntimeError('boom')):

        resp = client.post('/v1/messages',
                           data=json.dumps({'model': 'TestProvider/test-model', 'messages': []}),
                           content_type='application/json')

    assert resp.status_code == 500
    body = resp.get_json()
    assert body['error']['type'] == 'api_error'
    assert 'boom' in body['error']['message']


# ── /v1/messages ── streaming ──────────────────────────────────────────────────

def _sse_line(event: str, data: dict) -> str:
    return f"data: {json.dumps(data)}"


def _text_stream_lines():
    """Minimal valid OpenAI SSE stream for a simple text response."""
    chunk1 = {'choices': [{'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None, 'index': 0}]}
    chunk2 = {'choices': [{'delta': {'content': 'Hello'}, 'finish_reason': None, 'index': 0}]}
    chunk3 = {'choices': [{'delta': {'content': ' world'}, 'finish_reason': None, 'index': 0}]}
    chunk4 = {'choices': [{'delta': {}, 'finish_reason': 'stop', 'index': 0}]}
    return [
        f"data: {json.dumps(chunk1)}",
        f"data: {json.dumps(chunk2)}",
        f"data: {json.dumps(chunk3)}",
        f"data: {json.dumps(chunk4)}",
        'data: [DONE]',
    ]


def _parse_sse(text: str) -> list:
    """Parse SSE response body into list of (event, data) tuples."""
    events = []
    for block in text.strip().split('\n\n'):
        lines = block.strip().splitlines()
        event_type = None
        data_str = None
        for ln in lines:
            if ln.startswith('event: '):
                event_type = ln[7:]
            elif ln.startswith('data: '):
                data_str = ln[6:]
        if event_type and data_str:
            try:
                events.append((event_type, json.loads(data_str)))
            except json.JSONDecodeError:
                events.append((event_type, data_str))
    return events


def test_messages_stream_text(client):
    mock_stream = MockStreamCtx(status_code=200, lines=_text_stream_lines())

    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.stream', return_value=mock_stream):

        payload = {
            'model': 'TestProvider/test-model',
            'messages': [{'role': 'user', 'content': 'Hi'}],
            'stream': True,
        }
        resp = client.post('/v1/messages',
                           data=json.dumps(payload),
                           content_type='application/json')

    assert resp.status_code == 200
    assert 'text/event-stream' in resp.content_type

    events = _parse_sse(resp.data.decode('utf-8'))
    event_types = [e for e, _ in events]

    assert 'message_start' in event_types
    assert 'content_block_start' in event_types
    assert 'content_block_delta' in event_types
    assert 'content_block_stop' in event_types
    assert 'message_delta' in event_types
    assert 'message_stop' in event_types

    # Verify text content was assembled
    deltas = [d for e, d in events if e == 'content_block_delta']
    text_pieces = [d['delta']['text'] for d in deltas if d.get('delta', {}).get('type') == 'text_delta']
    assert ''.join(text_pieces) == 'Hello world'


def _tool_stream_lines():
    """OpenAI SSE stream with a tool call (no text)."""
    c0 = {'choices': [{'delta': {'role': 'assistant'}, 'finish_reason': None, 'index': 0}]}
    c1 = {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'call_t1', 'type': 'function',
                                                  'function': {'name': 'get_weather', 'arguments': ''}}]},
                        'finish_reason': None, 'index': 0}]}
    c2 = {'choices': [{'delta': {'tool_calls': [{'index': 0,
                                                  'function': {'arguments': '{"city":'}}]},
                        'finish_reason': None, 'index': 0}]}
    c3 = {'choices': [{'delta': {'tool_calls': [{'index': 0,
                                                  'function': {'arguments': '"Paris"}'}}]},
                        'finish_reason': None, 'index': 0}]}
    c4 = {'choices': [{'delta': {}, 'finish_reason': 'tool_calls', 'index': 0}]}
    return [
        f"data: {json.dumps(c0)}",
        f"data: {json.dumps(c1)}",
        f"data: {json.dumps(c2)}",
        f"data: {json.dumps(c3)}",
        f"data: {json.dumps(c4)}",
        'data: [DONE]',
    ]


def test_messages_stream_tool_call(client):
    mock_stream = MockStreamCtx(status_code=200, lines=_tool_stream_lines())

    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.stream', return_value=mock_stream):

        payload = {
            'model': 'TestProvider/test-model',
            'messages': [{'role': 'user', 'content': 'Weather?'}],
            'stream': True,
        }
        resp = client.post('/v1/messages',
                           data=json.dumps(payload),
                           content_type='application/json')

    assert resp.status_code == 200
    events = _parse_sse(resp.data.decode('utf-8'))
    event_types = [e for e, _ in events]

    # Tool call block must open
    block_starts = [d for e, d in events if e == 'content_block_start']
    tool_starts = [b for b in block_starts if b.get('content_block', {}).get('type') == 'tool_use']
    assert len(tool_starts) == 1
    assert tool_starts[0]['content_block']['name'] == 'get_weather'

    # Arguments arrive as input_json_delta
    deltas = [d for e, d in events if e == 'content_block_delta']
    arg_chunks = [d['delta']['partial_json'] for d in deltas
                  if d.get('delta', {}).get('type') == 'input_json_delta']
    assert ''.join(arg_chunks) == '{"city":"Paris"}'

    # stop_reason should be tool_use
    msg_deltas = [d for e, d in events if e == 'message_delta']
    assert msg_deltas[-1]['delta']['stop_reason'] == 'tool_use'


def test_messages_stream_provider_error(client):
    """Upstream 500 during streaming should yield an SSE error event."""
    mock_stream = MockStreamCtx(status_code=500, lines=[])

    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.stream', return_value=mock_stream):

        resp = client.post('/v1/messages',
                           data=json.dumps({'model': 'TestProvider/test-model',
                                            'messages': [], 'stream': True}),
                           content_type='application/json')

    assert resp.status_code == 200   # HTTP layer is 200; error is in stream
    body_text = resp.data.decode('utf-8')
    assert 'event: error' in body_text


# ── /v1/models ─────────────────────────────────────────────────────────────────

def test_models_list(client):
    providers = [
        {**FAKE_PROVIDER, 'models': [
            {'id': 'model-a', 'label': 'Model A'},
            {'id': 'model-b', 'label': 'Model B'},
        ]},
    ]
    with patch('ai_bridge.proxy.cfg.get_providers', return_value=providers):
        resp = client.get('/v1/models')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['object'] == 'list'
    ids = [m['id'] for m in body['data']]
    assert ids == ['TestProvider/model-a', 'TestProvider/model-b']
    assert body['data'][0]['display_name'] == 'Model A · TestProvider'
    assert body['data'][1]['display_name'] == 'Model B · TestProvider'


def test_models_empty(client):
    with patch('ai_bridge.proxy.cfg.get_providers', return_value=[]):
        resp = client.get('/v1/models')
    assert resp.status_code == 200
    assert resp.get_json()['data'] == []


# ── /v1/chat/completions ───────────────────────────────────────────────────────

def test_chat_completions_model_not_found(client):
    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=None):
        resp = client.post('/v1/chat/completions',
                           data=json.dumps({'model': 'unknown', 'messages': []}),
                           content_type='application/json')

    assert resp.status_code == 404
    body = resp.get_json()
    assert 'error' in body
    assert body['error']['type'] == 'invalid_request_error'


def test_chat_completions_passthrough(client):
    oai_resp_body = {
        'id': 'chatcmpl-passthrough',
        'object': 'chat.completion',
        'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': 'OK'},
                     'finish_reason': 'stop'}],
        'usage': {'prompt_tokens': 5, 'completion_tokens': 2, 'total_tokens': 7},
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = oai_resp_body

    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.post', return_value=mock_resp):

        resp = client.post('/v1/chat/completions',
                           data=json.dumps({'model': 'TestProvider/test-model',
                                            'messages': [{'role': 'user', 'content': 'Hey'}]}),
                           content_type='application/json')

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['id'] == 'chatcmpl-passthrough'
    assert body['choices'][0]['message']['content'] == 'OK'


def test_chat_completions_timeout(client):
    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.post', side_effect=httpx.TimeoutException('timeout')):

        resp = client.post('/v1/chat/completions',
                           data=json.dumps({'model': 'TestProvider/test-model', 'messages': []}),
                           content_type='application/json')

    assert resp.status_code == 529
    assert 'error' in resp.get_json()


def test_chat_completions_connect_error(client):
    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.post',
               side_effect=httpx.ConnectError('refused')):

        resp = client.post('/v1/chat/completions',
                           data=json.dumps({'model': 'TestProvider/test-model', 'messages': []}),
                           content_type='application/json')

    assert resp.status_code == 502


def test_chat_completions_stream(client):
    lines = [
        'data: {"choices":[{"delta":{"role":"assistant"},"finish_reason":null,"index":0}]}',
        'data: {"choices":[{"delta":{"content":"Hi"},"finish_reason":null,"index":0}]}',
        'data: {"choices":[{"delta":{},"finish_reason":"stop","index":0}]}',
        'data: [DONE]',
    ]
    mock_stream = MockStreamCtx(status_code=200, lines=lines)

    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.stream', return_value=mock_stream):

        resp = client.post('/v1/chat/completions',
                           data=json.dumps({'model': 'TestProvider/test-model',
                                            'messages': [], 'stream': True}),
                           content_type='application/json')

    assert resp.status_code == 200
    assert 'text/event-stream' in resp.content_type
    body_text = resp.data.decode('utf-8')
    # OAI pass-through: each line is forwarded as-is
    assert 'Hi' in body_text


# ── /v1/responses ─────────────────────────────────────────────────────────────

def test_responses_api_non_streaming(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = OAI_TEXT_RESPONSE

    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.post', return_value=mock_resp) as mock_post:
        resp = client.post('/v1/responses', json={
            'model': 'TestProvider/test-model',
            'instructions': 'Be concise',
            'input': 'Hello',
        })

    assert resp.status_code == 200
    body = resp.get_json()
    assert body['object'] == 'response'
    assert body['output'][0]['content'][0]['text'] == 'Hello, world!'
    call_kwargs = mock_post.call_args.kwargs
    assert call_kwargs['json']['model'] == 'test-model'
    assert call_kwargs['json']['messages'][0]['role'] == 'system'
    assert call_kwargs['json']['messages'][1]['content'] == 'Hello'


def test_responses_api_stream_emits_response_events(client):
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = OAI_TEXT_RESPONSE

    with patch('ai_bridge.proxy.cfg.get_provider_for_model', return_value=FAKE_PROVIDER), \
         patch('ai_bridge.proxy.cfg.get_model_id', return_value='test-model'), \
         patch('ai_bridge.proxy.httpx.post', return_value=mock_resp):
        resp = client.post('/v1/responses', json={
            'model': 'TestProvider/test-model',
            'input': 'Hello',
            'stream': True,
        })

    assert resp.status_code == 200
    assert resp.mimetype == 'text/event-stream'
    assert 'response.output_text.delta' in resp.get_data(as_text=True)
    assert 'response.completed' in resp.get_data(as_text=True)
