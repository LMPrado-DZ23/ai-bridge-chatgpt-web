"""Minimal OpenAI Responses API compatibility layer for Codex clients."""
import json
import uuid
import time


def to_chat_request(data: dict, upstream_model: str) -> dict:
    messages = []
    instructions = data.get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})

    input_items = data.get("input", "")
    if isinstance(input_items, str):
        if input_items:
            messages.append({"role": "user", "content": input_items})
    elif isinstance(input_items, list):
        for item in input_items:
            if not isinstance(item, dict):
                continue
            kind = item.get("type", "message")
            if kind == "function_call_output":
                messages.append({
                    "role": "tool",
                    "tool_call_id": str(item.get("call_id", "")),
                    "content": str(item.get("output", "")),
                })
                continue
            if kind == "function_call":
                messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": str(item.get("call_id") or item.get("id") or uuid.uuid4().hex),
                        "type": "function",
                        "function": {
                            "name": str(item.get("name", "")),
                            "arguments": str(item.get("arguments", "{}")),
                        },
                    }],
                })
                continue
            role = item.get("role", "user")
            # Several OpenAI-compatible upstreams (including DeepSeek) reject
            # the Responses API's "developer" role. Preserve its priority by
            # mapping it to the nearest supported Chat Completions role.
            if role == "developer":
                role = "system"
            content = item.get("content", "")
            if isinstance(content, list):
                pieces = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") in ("input_text", "output_text", "text"):
                        pieces.append(str(part.get("text", "")))
                    elif part.get("type") == "input_image":
                        raise ValueError("O proxy ainda não traduz imagens do formato Responses API.")
                content = "\n".join(pieces)
            if role in ("system", "developer", "user", "assistant"):
                messages.append({"role": role, "content": content})

    if not messages:
        messages = [{"role": "user", "content": ""}]

    request = {"model": upstream_model, "messages": messages, "stream": False}
    for source, target in (
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("max_output_tokens", "max_tokens"),
        ("tools", "tools"),
        ("tool_choice", "tool_choice"),
        ("parallel_tool_calls", "parallel_tool_calls"),
    ):
        if source in data:
            request[target] = data[source]
    if isinstance(data.get("tools"), list):
        tools = []
        for tool in data["tools"]:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "function":
                tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters", {"type": "object", "properties": {}}),
                        **({"strict": tool["strict"]} if "strict" in tool else {}),
                    },
                })
        request["tools"] = tools
    reasoning = data.get("reasoning")
    if isinstance(reasoning, dict) and reasoning.get("effort"):
        request["reasoning_effort"] = reasoning["effort"]
    return request


def from_chat_response(data: dict, requested_model: str) -> dict:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    output = []
    text = message.get("content")
    if isinstance(text, list):
        text = "".join(str(part.get("text", "")) for part in text if isinstance(part, dict))
    if text:
        output.append({
            "id": "msg_" + uuid.uuid4().hex,
            "type": "message",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": str(text), "annotations": []}],
        })
    for call in (message.get("tool_calls") or []):
        function = call.get("function") or {}
        output.append({
            "id": str(call.get("id") or "fc_" + uuid.uuid4().hex),
            "type": "function_call",
            "status": "completed",
            "call_id": str(call.get("id") or uuid.uuid4().hex),
            "name": str(function.get("name", "")),
            "arguments": str(function.get("arguments", "{}")),
        })
    usage = data.get("usage") or {}
    return {
        "id": "resp_" + uuid.uuid4().hex,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "model": requested_model,
        "output": output,
        "output_text": str(text or ""),
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "output_tokens": int(usage.get("completion_tokens", 0) or 0),
            "total_tokens": int(usage.get("total_tokens", 0) or 0),
        },
    }


def as_sse(response: dict):
    """Emit a Responses-style SSE sequence after the upstream response completes."""
    yield "event: response.created\ndata: " + json.dumps({
        "type": "response.created",
        "response": {**response, "status": "in_progress", "output": []},
    }) + "\n\n"
    for output_index, item in enumerate(response.get("output", [])):
        item_id = item.get("id", "item_" + uuid.uuid4().hex)
        if item.get("type") == "message":
            initial = {**item, "status": "in_progress", "content": []}
            yield "event: response.output_item.added\ndata: " + json.dumps({
                "type": "response.output_item.added", "output_index": output_index, "item": initial,
            }) + "\n\n"
            for content_index, part in enumerate(item.get("content", [])):
                yield "event: response.content_part.added\ndata: " + json.dumps({
                    "type": "response.content_part.added", "item_id": item_id,
                    "output_index": output_index, "content_index": content_index,
                    "part": {"type": "output_text", "text": "", "annotations": []},
                }) + "\n\n"
                text = part.get("text", "")
                if text:
                    yield "event: response.output_text.delta\ndata: " + json.dumps({
                        "type": "response.output_text.delta", "item_id": item_id,
                        "output_index": output_index, "content_index": content_index, "delta": text,
                    }) + "\n\n"
                yield "event: response.output_text.done\ndata: " + json.dumps({
                    "type": "response.output_text.done", "item_id": item_id,
                    "output_index": output_index, "content_index": content_index, "text": text,
                }) + "\n\n"
                yield "event: response.content_part.done\ndata: " + json.dumps({
                    "type": "response.content_part.done", "item_id": item_id,
                    "output_index": output_index, "content_index": content_index,
                    "part": part,
                }) + "\n\n"
            yield "event: response.output_item.done\ndata: " + json.dumps({
                "type": "response.output_item.done", "output_index": output_index, "item": item,
            }) + "\n\n"
        else:
            yield "event: response.output_item.added\ndata: " + json.dumps({
                "type": "response.output_item.added", "output_index": output_index,
                "item": {**item, "status": "in_progress"},
            }) + "\n\n"
            yield "event: response.output_item.done\ndata: " + json.dumps({
                "type": "response.output_item.done", "output_index": output_index, "item": item,
            }) + "\n\n"
    yield "event: response.completed\ndata: " + json.dumps({
        "type": "response.completed", "response": response,
    }) + "\n\n"
