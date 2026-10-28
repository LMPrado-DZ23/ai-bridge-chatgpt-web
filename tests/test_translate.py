"""
Unit tests for ai_bridge/translate.py — Anthropic ↔ OpenAI format translation.
Run with: python -m pytest tests/test_translate.py -v
"""
import json
import pytest

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ai_bridge.translate import (
    to_openai_request,
    to_anthropic_response,
    stream_to_anthropic,
    _sse,
)


# ── to_openai_request ───────────────────────────────────────────────────────

class TestToOpenaiRequest:
    def test_simple_message(self):
        req = {'messages': [{'role': 'user', 'content': 'hello'}]}
        result = to_openai_request(req, 'gpt-4o')
        assert result['model'] == 'gpt-4o'
        assert result['messages'] == [{'role': 'user', 'content': 'hello'}]
        assert result['max_tokens'] == 4096
        assert result['stream'] is False

    def test_system_string(self):
        req = {
            'system': 'You are helpful.',
            'messages': [{'role': 'user', 'content': 'hi'}],
        }
        result = to_openai_request(req, 'm')
        assert result['messages'][0] == {'role': 'system', 'content': 'You are helpful.'}
        assert result['messages'][1]['role'] == 'user'

    def test_system_list(self):
        req = {
            'system': [
                {'type': 'text', 'text': 'Part A.'},
                {'type': 'text', 'text': 'Part B.'},
            ],
            'messages': [{'role': 'user', 'content': 'hi'}],
        }
        result = to_openai_request(req, 'm')
        assert result['messages'][0]['content'] == 'Part A.\nPart B.'

    def test_optional_params_forwarded(self):
        req = {
            'messages': [{'role': 'user', 'content': 'x'}],
            'temperature': 0.7,
            'top_p': 0.9,
            'max_tokens': 100,
            'stream': True,
            'stop_sequences': ['END'],
        }
        result = to_openai_request(req, 'm')
        assert result['temperature'] == 0.7
        assert result['top_p'] == 0.9
        assert result['max_tokens'] == 100
        assert result['stream'] is True
        assert result['stop'] == ['END']

    def test_tool_choice_auto(self):
        req = {
            'messages': [{'role': 'user', 'content': 'x'}],
            'tools': [{'name': 'calc', 'description': '', 'input_schema': {'type': 'object', 'properties': {}}}],
            'tool_choice': {'type': 'auto'},
        }
        result = to_openai_request(req, 'm')
        assert result['tool_choice'] == 'auto'

    def test_tool_choice_any(self):
        req = {
            'messages': [{'role': 'user', 'content': 'x'}],
            'tools': [{'name': 'calc', 'description': '', 'input_schema': {}}],
            'tool_choice': {'type': 'any'},
        }
        result = to_openai_request(req, 'm')
        assert result['tool_choice'] == 'required'

    def test_tool_choice_specific(self):
        req = {
            'messages': [{'role': 'user', 'content': 'x'}],
            'tools': [{'name': 'calc', 'description': '', 'input_schema': {}}],
            'tool_choice': {'type': 'tool', 'name': 'calc'},
        }
        result = to_openai_request(req, 'm')
        assert result['tool_choice'] == {'type': 'function', 'function': {'name': 'calc'}}

    def test_assistant_text_block(self):
        req = {
            'messages': [
                {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Sure!'}]},
                {'role': 'user', 'content': 'ok'},
            ],
        }
        result = to_openai_request(req, 'm')
        assert result['messages'][0] == {'role': 'assistant', 'content': 'Sure!'}

    def test_assistant_tool_use_block(self):
        req = {
            'messages': [
                {
                    'role': 'assistant',
                    'content': [
                        {
                            'type': 'tool_use',
                            'id': 'call_abc',
                            'name': 'my_tool',
                            'input': {'key': 'val'},
                        }
                    ],
                },
            ],
        }
        result = to_openai_request(req, 'm')
        msg = result['messages'][0]
        assert msg['role'] == 'assistant'
        assert len(msg['tool_calls']) == 1
        tc = msg['tool_calls'][0]
        assert tc['id'] == 'call_abc'
        assert tc['function']['name'] == 'my_tool'
        assert json.loads(tc['function']['arguments']) == {'key': 'val'}

    def test_user_tool_result(self):
        req = {
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'tool_result',
                            'tool_use_id': 'call_abc',
                            'content': 'result text',
                        }
                    ],
                },
            ],
        }
        result = to_openai_request(req, 'm')
        msg = result['messages'][0]
        assert msg['role'] == 'tool'
        assert msg['tool_call_id'] == 'call_abc'
        assert msg['content'] == 'result text'

    def test_user_image_base64(self):
        req = {
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'image',
                            'source': {
                                'type': 'base64',
                                'media_type': 'image/png',
                                'data': 'abc123',
                            },
                        }
                    ],
                }
            ],
        }
        result = to_openai_request(req, 'm')
        msg = result['messages'][0]
        assert msg['role'] == 'user'
        parts = msg['content']
        assert parts[0]['type'] == 'image_url'
        assert parts[0]['image_url']['url'] == 'data:image/png;base64,abc123'

    def test_user_mixed_text_and_tool_result(self):
        req = {
            'messages': [
                {
                    'role': 'user',
                    'content': [
                        {'type': 'tool_result', 'tool_use_id': 'c1', 'content': 'done'},
                        {'type': 'text', 'text': 'Thanks!'},
                    ],
                }
            ],
        }
        result = to_openai_request(req, 'm')
        msgs = result['messages']
        # tool result comes first, then user text
        roles = [m['role'] for m in msgs]
        assert 'tool' in roles
        assert 'user' in roles


# ── to_anthropic_response ───────────────────────────────────────────────────

class TestToAnthropicResponse:
    def _make_oai(self, content=None, tool_calls=None, finish='stop', usage=None):
        msg = {'content': content, 'role': 'assistant'}
        if tool_calls:
            msg['tool_calls'] = tool_calls
        return {
            'choices': [{'message': msg, 'finish_reason': finish}],
            'usage': usage or {'prompt_tokens': 10, 'completion_tokens': 5},
        }

    def test_text_response(self):
        oai = self._make_oai(content='Hello')
        result = to_anthropic_response(oai, 'my-model')
        assert result['type'] == 'message'
        assert result['role'] == 'assistant'
        assert result['model'] == 'my-model'
        assert result['content'][0] == {'type': 'text', 'text': 'Hello'}
        assert result['stop_reason'] == 'end_turn'

    def test_stop_reason_mapping(self):
        for oai_reason, expected in [
            ('stop', 'end_turn'),
            ('length', 'max_tokens'),
            ('tool_calls', 'tool_use'),
            ('content_filter', 'stop_sequence'),
        ]:
            oai = self._make_oai(content='x', finish=oai_reason)
            result = to_anthropic_response(oai, 'm')
            assert result['stop_reason'] == expected

    def test_tool_call_response(self):
        tool_calls = [
            {
                'id': 'call_xyz',
                'type': 'function',
                'function': {'name': 'my_fn', 'arguments': '{"a": 1}'},
            }
        ]
        oai = self._make_oai(tool_calls=tool_calls, finish='tool_calls')
        result = to_anthropic_response(oai, 'm')
        block = result['content'][0]
        assert block['type'] == 'tool_use'
        assert block['id'] == 'call_xyz'
        assert block['name'] == 'my_fn'
        assert block['input'] == {'a': 1}

    def test_usage_mapping(self):
        oai = self._make_oai(content='hi', usage={'prompt_tokens': 7, 'completion_tokens': 3})
        result = to_anthropic_response(oai, 'm')
        assert result['usage']['input_tokens'] == 7
        assert result['usage']['output_tokens'] == 3

    def test_invalid_tool_args_fallback(self):
        tool_calls = [
            {
                'id': 'c1',
                'type': 'function',
                'function': {'name': 'fn', 'arguments': 'NOT JSON'},
            }
        ]
        oai = self._make_oai(tool_calls=tool_calls)
        result = to_anthropic_response(oai, 'm')
        # should not raise; input falls back to {}
        assert result['content'][0]['input'] == {}


# ── stream_to_anthropic ─────────────────────────────────────────────────────

def _make_lines(*chunks):
    """Produce SSE lines from dicts."""
    lines = []
    for chunk in chunks:
        lines.append(f'data: {json.dumps(chunk)}')
    lines.append('data: [DONE]')
    return lines


class TestStreamToAnthropic:
    def _collect(self, lines, model='m'):
        return list(stream_to_anthropic(iter(lines), model))

    def _parse_events(self, raw_lines):
        """Parse a list of SSE chunks (each may be multi-line) into (event, data) pairs."""
        result = []
        ev = None
        for item in raw_lines:
            for line in item.split('\n'):
                line = line.strip()
                if line.startswith('event: '):
                    ev = line[7:]
                elif line.startswith('data: ') and ev is not None:
                    try:
                        result.append((ev, json.loads(line[6:])))
                    except json.JSONDecodeError:
                        pass
        return result

    def test_empty_stream_emits_start_and_stop(self):
        lines = ['data: [DONE]']
        events = self._parse_events(self._collect(lines))
        types = [d['type'] for _, d in events]
        assert 'message_start' in types
        assert 'message_stop' in types

    def test_text_stream(self):
        chunk = {
            'choices': [{'delta': {'content': 'Hi!'}, 'finish_reason': None}],
        }
        lines = _make_lines(chunk)
        events = self._parse_events(self._collect(lines))
        types = [d['type'] for _, d in events]
        assert 'message_start' in types
        assert 'content_block_start' in types
        assert 'content_block_delta' in types
        assert 'content_block_stop' in types
        assert 'message_delta' in types
        assert 'message_stop' in types

    def test_text_delta_content(self):
        chunk = {
            'choices': [{'delta': {'content': 'Hello'}, 'finish_reason': None}],
        }
        lines = _make_lines(chunk)
        events = self._parse_events(self._collect(lines))
        deltas = [d for _, d in events if d['type'] == 'content_block_delta']
        assert len(deltas) == 1
        assert deltas[0]['delta']['text'] == 'Hello'

    def test_finish_reason_maps_correctly(self):
        chunk = {'choices': [{'delta': {}, 'finish_reason': 'length'}]}
        lines = _make_lines(chunk)
        events = self._parse_events(self._collect(lines))
        msg_delta = next(d for _, d in events if d['type'] == 'message_delta')
        assert msg_delta['delta']['stop_reason'] == 'max_tokens'

    def test_bytes_lines_decoded(self):
        chunk = {'choices': [{'delta': {'content': 'ok'}, 'finish_reason': None}]}
        raw = f'data: {json.dumps(chunk)}'.encode('utf-8')
        lines = [raw, b'data: [DONE]']
        events = self._parse_events(self._collect(lines))
        deltas = [d for _, d in events if d['type'] == 'content_block_delta']
        assert deltas[0]['delta']['text'] == 'ok'

    def test_tool_call_stream(self):
        chunk1 = {
            'choices': [
                {
                    'delta': {
                        'tool_calls': [
                            {
                                'index': 0,
                                'id': 'call_1',
                                'type': 'function',
                                'function': {'name': 'fn', 'arguments': ''},
                            }
                        ]
                    },
                    'finish_reason': None,
                }
            ]
        }
        chunk2 = {
            'choices': [
                {
                    'delta': {
                        'tool_calls': [
                            {
                                'index': 0,
                                'function': {'arguments': '{"x":1}'},
                            }
                        ]
                    },
                    'finish_reason': 'tool_calls',
                }
            ]
        }
        lines = _make_lines(chunk1, chunk2)
        events = self._parse_events(self._collect(lines))
        types = [d['type'] for _, d in events]
        assert 'content_block_start' in types
        assert 'content_block_delta' in types
        tool_starts = [d for _, d in events if d['type'] == 'content_block_start' and d.get('content_block', {}).get('type') == 'tool_use']
        assert len(tool_starts) == 1
        assert tool_starts[0]['content_block']['name'] == 'fn'


# ── _sse helper ─────────────────────────────────────────────────────────────

def test_sse_format():
    result = _sse('ping', {'type': 'ping'})
    assert result.startswith('event: ping\n')
    assert 'data: ' in result
    assert result.endswith('\n\n')
    data = json.loads(result.split('data: ')[1])
    assert data == {'type': 'ping'}
