"""Translate claude-cli stream-json events to Anthropic SSE events.

Anthropic's streaming Messages API emits SSE events in this order:
    message_start            (with empty Message envelope)
    content_block_start      (one per content block)
    content_block_delta      (one+ per content block)
    content_block_stop
    message_delta            (final stop_reason + usage)
    message_stop

claude-cli emits a different NDJSON stream:
    {type:"system", subtype:"init", ...}                      → suppressed
    {type:"assistant", message:{content:[{type, ...}], ...}}  → one whole block per event
    {type:"user", message:{content:[{type:"tool_result", ...}]}}  → suppressed (claude's own tool runs)
    {type:"result", subtype, stop_reason, usage, ...}         → message_delta + message_stop

claude does NOT stream individual deltas — it emits whole content blocks per
event. We synthesize one *_start, one *_delta with the full content, and one
*_stop per block. This is suboptimal for token-by-token UX but correct.
"""
from __future__ import annotations

import json
import uuid
from typing import Any, Iterator


def _new_msg_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _usage_from_assistant(msg: dict[str, Any]) -> dict[str, int]:
    usage = msg.get("usage") or {}
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
    }


def _final_usage(result: dict[str, Any]) -> dict[str, int]:
    usage = result.get("usage") or {}
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
    }


_MCP_TOOL_PREFIX = "mcp__hermes_tools__"


def _strip_mcp_prefix(name: str) -> str:
    """claude prefixes MCP tools with `mcp__<server>__`. Strip ours back to the original Hermes name."""
    if name.startswith(_MCP_TOOL_PREFIX):
        return name[len(_MCP_TOOL_PREFIX):]
    return name


def translate_stream(
    claude_events: Iterator[dict[str, Any]],
    *,
    model: str,
    suppress_thinking: bool = False,
) -> Iterator[str]:
    """Convert a stream of claude NDJSON events into Anthropic SSE strings.

    Yields fully-formed SSE event strings (with trailing blank line) ready
    to be written to the response body.
    """
    msg_id = _new_msg_id()
    started = False
    block_index = 0
    last_assistant_msg: dict[str, Any] | None = None
    final_stop_reason: str | None = None

    for event in claude_events:
        etype = event.get("type")

        if etype == "system":
            # init handshake — capture the resolved model id if present
            actual_model = event.get("model") or model
            yield _sse("message_start", {
                "type": "message_start",
                "message": {
                    "id": msg_id,
                    "type": "message",
                    "role": "assistant",
                    "model": actual_model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                },
            })
            started = True
            continue

        if etype == "assistant":
            msg = event.get("message", {})
            last_assistant_msg = msg
            content = msg.get("content", [])
            for block in content:
                btype = block.get("type")
                if btype == "thinking" and suppress_thinking:
                    continue
                if btype == "thinking":
                    yield _sse("content_block_start", {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {"type": "thinking", "thinking": ""},
                    })
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "thinking_delta", "thinking": block.get("thinking", "")},
                    })
                    if block.get("signature"):
                        yield _sse("content_block_delta", {
                            "type": "content_block_delta",
                            "index": block_index,
                            "delta": {"type": "signature_delta", "signature": block["signature"]},
                        })
                    yield _sse("content_block_stop", {
                        "type": "content_block_stop",
                        "index": block_index,
                    })
                    block_index += 1
                elif btype == "text":
                    yield _sse("content_block_start", {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {"type": "text", "text": ""},
                    })
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {"type": "text_delta", "text": block.get("text", "")},
                    })
                    yield _sse("content_block_stop", {
                        "type": "content_block_stop",
                        "index": block_index,
                    })
                    block_index += 1
                elif btype == "tool_use":
                    yield _sse("content_block_start", {
                        "type": "content_block_start",
                        "index": block_index,
                        "content_block": {
                            "type": "tool_use",
                            "id": block.get("id", ""),
                            "name": _strip_mcp_prefix(block.get("name", "")),
                            "input": {},
                        },
                    })
                    yield _sse("content_block_delta", {
                        "type": "content_block_delta",
                        "index": block_index,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    })
                    yield _sse("content_block_stop", {
                        "type": "content_block_stop",
                        "index": block_index,
                    })
                    block_index += 1
            continue

        if etype == "user":
            # claude's synthetic tool_result for its own tool runs — suppress
            continue

        if etype == "result":
            final_stop_reason = event.get("stop_reason") or "end_turn"
            final_usage = _final_usage(event)
            yield _sse("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": final_stop_reason, "stop_sequence": None},
                "usage": final_usage,
            })
            yield _sse("message_stop", {"type": "message_stop"})
            return

    # Stream ended without a result event — emit a synthetic close
    if started:
        yield _sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": final_stop_reason or "end_turn", "stop_sequence": None},
            "usage": _usage_from_assistant(last_assistant_msg or {}),
        })
        yield _sse("message_stop", {"type": "message_stop"})


def collect_non_streaming(
    claude_events: Iterator[dict[str, Any]],
    *,
    model: str,
    suppress_thinking: bool = False,
) -> dict[str, Any]:
    """Consume the claude event stream and return one non-streaming Anthropic Message."""
    msg_id = _new_msg_id()
    actual_model = model
    content: list[dict[str, Any]] = []
    stop_reason = "end_turn"
    usage = {"input_tokens": 0, "output_tokens": 0}

    for event in claude_events:
        etype = event.get("type")
        if etype == "system":
            actual_model = event.get("model") or model
        elif etype == "assistant":
            for block in event.get("message", {}).get("content", []):
                btype = block.get("type")
                if btype == "thinking" and suppress_thinking:
                    continue
                if btype == "tool_use":
                    block = {**block, "name": _strip_mcp_prefix(block.get("name", ""))}
                if btype in ("text", "thinking", "tool_use"):
                    content.append(block)
        elif etype == "result":
            stop_reason = event.get("stop_reason") or "end_turn"
            usage = _final_usage(event)

    return {
        "id": msg_id,
        "type": "message",
        "role": "assistant",
        "model": actual_model,
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": usage,
    }
