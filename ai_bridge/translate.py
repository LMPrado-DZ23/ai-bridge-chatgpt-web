"""
Translates between Anthropic Messages API format and OpenAI Chat Completions format.
"""
import json
import uuid
from typing import Iterator


# ─── Request: Anthropic → OpenAI ───────────────────────────────────────────────

def to_openai_request(req: dict, model: str) -> dict:
    messages = []

    # System prompt
    system = req.get('system')
    if system:
        if isinstance(system, str):
            messages.append({'role': 'system', 'content': system})
        elif isinstance(system, list):
            text = '\n'.join(b['text'] for b in system if b.get('type') == 'text')
            if text:
                messages.append({'role': 'system', 'content': text})

    for msg in req.get('messages', []):
        messages.extend(_convert_message(msg['role'], msg['content']))

    oai: dict = {
        'model': model,
        'messages': messages,
        'max_tokens': req.get('max_tokens', 4096),
        'stream': req.get('stream', False),
    }

    for k in ('temperature', 'top_p'):
        if k in req:
            oai[k] = req[k]

    if req.get('stop_sequences'):
        oai['stop'] = req['stop_sequences']

    tools = req.get('tools')
    if tools:
        oai['tools'] = [_tool_to_oai(t) for t in tools]
        tc = req.get('tool_choice')
        if tc:
            ttype = tc.get('type', 'auto')
            if ttype == 'auto':
                oai['tool_choice'] = 'auto'
            elif ttype == 'any':
                oai['tool_choice'] = 'required'
            elif ttype == 'tool':
                oai['tool_choice'] = {'type': 'function', 'function': {'name': tc['name']}}

    return oai


def _convert_message(role: str, content) -> list:
    if isinstance(content, str):
        return [{'role': role, 'content': content}]

    if role == 'assistant':
        text_parts = []
        tool_calls = []
        for block in content:
            btype = block.get('type')
            if btype == 'text':
                text_parts.append(block.get('text', ''))
            elif btype == 'tool_use':
                tool_calls.append({
                    'id': block.get('id', f'call_{uuid.uuid4().hex[:8]}'),
                    'type': 'function',
                    'function': {
                        'name': block.get('name', ''),
                        'arguments': json.dumps(block.get('input', {})),
                    },
                })
        msg: dict = {'role': 'assistant'}
        joined = '\n'.join(text_parts)
        if joined:
            msg['content'] = joined
        if tool_calls:
            msg['tool_calls'] = tool_calls
        return [msg]

    if role == 'user':
        tool_results = []
        other_blocks = []
        for block in content:
            if block.get('type') == 'tool_result':
                tool_results.append(block)
            else:
                other_blocks.append(block)

        msgs = []

        for r in tool_results:
            rc = r.get('content', '')
            if isinstance(rc, list):
                rc = '\n'.join(b.get('text', '') for b in rc if b.get('type') == 'text')
            msgs.append({'role': 'tool', 'tool_call_id': r['tool_use_id'], 'content': rc})

        if other_blocks:
            parts = []
            for block in other_blocks:
                btype = block.get('type')
                if btype == 'text':
                    parts.append({'type': 'text', 'text': block.get('text', '')})
                elif btype == 'image':
                    src = block.get('source', {})
                    if src.get('type') == 'base64':
                        parts.append({
                            'type': 'image_url',
                            'image_url': {'url': f"data:{src['media_type']};base64,{src['data']}"},
                        })
                    elif src.get('type') == 'url':
                        parts.append({'type': 'image_url', 'image_url': {'url': src['url']}})
            if len(parts) == 1 and parts[0].get('type') == 'text':
                msgs.append({'role': 'user', 'content': parts[0]['text']})
            elif parts:
                msgs.append({'role': 'user', 'content': parts})

        return msgs

    return [{'role': role, 'content': str(content)}]


def _tool_to_oai(tool: dict) -> dict:
    return {
        'type': 'function',
        'function': {
            'name': tool['name'],
            'description': tool.get('description', ''),
            'parameters': tool.get('input_schema', {'type': 'object', 'properties': {}}),
        },
    }


# ─── Response: OpenAI → Anthropic ──────────────────────────────────────────────

_STOP_MAP = {
    'stop': 'end_turn',
    'length': 'max_tokens',
    'tool_calls': 'tool_use',
    'content_filter': 'stop_sequence',
    'function_call': 'tool_use',
}


def to_anthropic_response(oai: dict, model: str) -> dict:
    choice = oai.get('choices', [{}])[0]
    message = choice.get('message', {})
    finish = choice.get('finish_reason', 'stop')
    usage = oai.get('usage', {})

    content = []

    text = message.get('content')
    if text:
        content.append({'type': 'text', 'text': text})

    for tc in (message.get('tool_calls') or []):
        fn = tc.get('function', {})
        try:
            inp = json.loads(fn.get('arguments', '{}'))
        except json.JSONDecodeError:
            inp = {}
        content.append({
            'type': 'tool_use',
            'id': tc.get('id', f'call_{uuid.uuid4().hex[:8]}'),
            'name': fn.get('name', ''),
            'input': inp,
        })

    return {
        'id': f"msg_{uuid.uuid4().hex}",
        'type': 'message',
        'role': 'assistant',
        'model': model,
        'content': content,
        'stop_reason': _STOP_MAP.get(finish, 'end_turn'),
        'stop_sequence': None,
        'usage': {
            'input_tokens': usage.get('prompt_tokens', 0),
            'output_tokens': usage.get('completion_tokens', 0),
        },
    }


# ─── Streaming: OpenAI SSE → Anthropic SSE ─────────────────────────────────────

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def stream_to_anthropic(oai_lines: Iterator[str], model: str) -> Iterator[str]:
    msg_id = f"msg_{uuid.uuid4().hex}"

    sent_start = False
    next_idx = 0
    text_block_idx = None       # assigned on first text delta
    tool_block_map: dict = {}   # oai_idx → {'blk_idx', 'id', 'name', 'args'}
    input_tokens = 0
    output_tokens = 0
    finish_reason = 'stop'

    for line in oai_lines:
        if isinstance(line, bytes):
            line = line.decode('utf-8', errors='replace')
        line = line.strip()
        if not line.startswith('data: '):
            continue
        raw = line[6:]
        if raw == '[DONE]':
            break
        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError:
            continue

        if chunk.get('usage'):
            u = chunk['usage']
            input_tokens = u.get('prompt_tokens', input_tokens)
            output_tokens = u.get('completion_tokens', output_tokens)

        choices = chunk.get('choices')
        if not choices:
            continue

        choice = choices[0]
        delta = choice.get('delta', {})
        fr = choice.get('finish_reason')
        if fr:
            finish_reason = fr

        # First chunk → send message_start + ping
        if not sent_start:
            yield _sse('message_start', {
                'type': 'message_start',
                'message': {
                    'id': msg_id,
                    'type': 'message',
                    'role': 'assistant',
                    'model': model,
                    'content': [],
                    'stop_reason': None,
                    'stop_sequence': None,
                    'usage': {'input_tokens': input_tokens, 'output_tokens': 0},
                },
            })
            yield _sse('ping', {'type': 'ping'})
            sent_start = True

        # Text delta
        text = delta.get('content')
        if text:
            if text_block_idx is None:
                text_block_idx = next_idx
                next_idx += 1
                yield _sse('content_block_start', {
                    'type': 'content_block_start',
                    'index': text_block_idx,
                    'content_block': {'type': 'text', 'text': ''},
                })
            yield _sse('content_block_delta', {
                'type': 'content_block_delta',
                'index': text_block_idx,
                'delta': {'type': 'text_delta', 'text': text},
            })

        # Tool call deltas
        for tc in delta.get('tool_calls', []):
            oai_idx = tc.get('index', 0)
            if oai_idx not in tool_block_map:
                fn = tc.get('function', {})
                blk_idx = next_idx
                next_idx += 1
                tool_block_map[oai_idx] = {
                    'blk_idx': blk_idx,
                    'id': tc.get('id', f'call_{uuid.uuid4().hex[:8]}'),
                    'name': fn.get('name', ''),
                    'args': fn.get('arguments', ''),
                }
                yield _sse('content_block_start', {
                    'type': 'content_block_start',
                    'index': blk_idx,
                    'content_block': {
                        'type': 'tool_use',
                        'id': tool_block_map[oai_idx]['id'],
                        'name': tool_block_map[oai_idx]['name'],
                        'input': {},
                    },
                })
            else:
                fn = tc.get('function', {})
                args_delta = fn.get('arguments', '')
                if args_delta:
                    tool_block_map[oai_idx]['args'] += args_delta
                    yield _sse('content_block_delta', {
                        'type': 'content_block_delta',
                        'index': tool_block_map[oai_idx]['blk_idx'],
                        'delta': {'type': 'input_json_delta', 'partial_json': args_delta},
                    })

    # If provider returned nothing at all, open and close a minimal message
    if not sent_start:
        yield _sse('message_start', {
            'type': 'message_start',
            'message': {
                'id': msg_id, 'type': 'message', 'role': 'assistant',
                'model': model, 'content': [], 'stop_reason': None,
                'stop_sequence': None,
                'usage': {'input_tokens': 0, 'output_tokens': 0},
            },
        })
        yield _sse('ping', {'type': 'ping'})

    # Close blocks
    if text_block_idx is not None:
        yield _sse('content_block_stop', {'type': 'content_block_stop', 'index': text_block_idx})

    for oai_idx in sorted(tool_block_map):
        yield _sse('content_block_stop', {
            'type': 'content_block_stop',
            'index': tool_block_map[oai_idx]['blk_idx'],
        })

    yield _sse('message_delta', {
        'type': 'message_delta',
        'delta': {
            'stop_reason': _STOP_MAP.get(finish_reason, 'end_turn'),
            'stop_sequence': None,
        },
        'usage': {'output_tokens': output_tokens},
    })

    yield _sse('message_stop', {'type': 'message_stop'})
