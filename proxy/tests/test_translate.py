"""Unit tests for the claude-NDJSON → Anthropic translator."""
from __future__ import annotations

import json

from claude_cli_proxy.translate import collect_non_streaming, translate_stream


def _claude_text_turn(text: str = "hello") -> list[dict]:
    """A minimal claude event sequence for a plain text response."""
    return [
        {"type": "system", "subtype": "init", "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0"},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }},
        {"type": "result", "subtype": "success", "stop_reason": "end_turn",
         "usage": {"input_tokens": 5, "output_tokens": 3}},
    ]


def _claude_tool_use_turn(tool_name: str, tool_input: dict) -> list[dict]:
    return [
        {"type": "system", "subtype": "init", "model": "claude-sonnet"},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "calling tool"},
                {"type": "tool_use", "id": "toolu_x", "name": tool_name, "input": tool_input},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }},
        {"type": "result", "subtype": "success", "stop_reason": "tool_use",
         "usage": {"input_tokens": 10, "output_tokens": 5}},
    ]


def _parse_sse(sse_chunks: list[str]) -> list[tuple[str, dict]]:
    """Parse SSE strings into (event_name, data) tuples."""
    events = []
    for chunk in sse_chunks:
        lines = chunk.strip().split("\n")
        event = data = None
        for line in lines:
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        if event:
            events.append((event, data))
    return events


# ── collect_non_streaming ──────────────────────────────────────────────

def test_non_streaming_returns_anthropic_message_shape():
    out = collect_non_streaming(iter(_claude_text_turn("hi")), model="sonnet")
    assert out["type"] == "message"
    assert out["role"] == "assistant"
    assert out["model"] == "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    assert out["stop_reason"] == "end_turn"
    assert any(b.get("type") == "text" and b.get("text") == "hi" for b in out["content"])
    assert out["id"].startswith("msg_")


def test_non_streaming_strips_mcp_prefix_from_tool_name():
    events = _claude_tool_use_turn("mcp__hermes_tools__terminal", {"cmd": "echo hi"})
    out = collect_non_streaming(iter(events), model="sonnet")
    tool_blocks = [b for b in out["content"] if b.get("type") == "tool_use"]
    assert len(tool_blocks) == 1
    assert tool_blocks[0]["name"] == "terminal"
    assert tool_blocks[0]["input"] == {"cmd": "echo hi"}


def test_non_streaming_passes_through_unprefixed_tool_name():
    events = _claude_tool_use_turn("Bash", {"command": "ls"})
    out = collect_non_streaming(iter(events), model="sonnet")
    tool_blocks = [b for b in out["content"] if b.get("type") == "tool_use"]
    assert tool_blocks[0]["name"] == "Bash"


def test_non_streaming_suppress_thinking():
    events = [
        {"type": "system", "subtype": "init", "model": "sonnet"},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "internal"},
            {"type": "text", "text": "external"},
        ], "usage": {}}},
        {"type": "result", "stop_reason": "end_turn", "usage": {}},
    ]
    out = collect_non_streaming(iter(events), model="sonnet", suppress_thinking=True)
    types = [b.get("type") for b in out["content"]]
    assert "thinking" not in types
    assert "text" in types


def test_non_streaming_suppresses_user_tool_result_events():
    """claude emits synthetic `user`/`tool_result` events for its own tool runs;
    those must not leak into Hermes's response."""
    events = [
        {"type": "system", "subtype": "init", "model": "sonnet"},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "echo"}},
        ], "usage": {}}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
        ]}},
        {"type": "result", "stop_reason": "end_turn", "usage": {}},
    ]
    out = collect_non_streaming(iter(events), model="sonnet")
    # Only the assistant's tool_use should appear, not claude's synthetic tool_result
    assert all(b.get("type") != "tool_result" for b in out["content"])


# ── translate_stream ───────────────────────────────────────────────────

def test_stream_emits_anthropic_event_order():
    sse_chunks = list(translate_stream(iter(_claude_text_turn("hi")), model="sonnet"))
    events = _parse_sse(sse_chunks)
    names = [n for n, _ in events]
    # Required Anthropic event order
    assert names[0] == "message_start"
    assert "content_block_start" in names
    assert "content_block_delta" in names
    assert "content_block_stop" in names
    assert names[-2] == "message_delta"
    assert names[-1] == "message_stop"


def test_stream_text_delta_carries_text():
    sse_chunks = list(translate_stream(iter(_claude_text_turn("hello world")), model="sonnet"))
    events = _parse_sse(sse_chunks)
    deltas = [d for n, d in events if n == "content_block_delta"]
    assert any(d.get("delta", {}).get("text") == "hello world" for d in deltas)


def test_stream_tool_use_emits_input_json_delta():
    events = _claude_tool_use_turn("mcp__hermes_tools__terminal", {"cmd": "echo hi"})
    sse_chunks = list(translate_stream(iter(events), model="sonnet"))
    sse_events = _parse_sse(sse_chunks)
    starts = [d for n, d in sse_events if n == "content_block_start"]
    tool_starts = [s for s in starts if s.get("content_block", {}).get("type") == "tool_use"]
    assert len(tool_starts) == 1
    assert tool_starts[0]["content_block"]["name"] == "terminal"  # prefix stripped
    deltas = [d for n, d in sse_events if n == "content_block_delta"]
    json_deltas = [d for d in deltas if d.get("delta", {}).get("type") == "input_json_delta"]
    assert len(json_deltas) == 1
    assert json.loads(json_deltas[0]["delta"]["partial_json"]) == {"cmd": "echo hi"}


def test_stream_message_delta_carries_stop_reason_and_usage():
    sse_chunks = list(translate_stream(iter(_claude_text_turn()), model="sonnet"))
    events = _parse_sse(sse_chunks)
    msg_deltas = [d for n, d in events if n == "message_delta"]
    assert len(msg_deltas) == 1
    assert msg_deltas[0]["delta"]["stop_reason"] == "end_turn"
    assert msg_deltas[0]["usage"]["input_tokens"] == 5


def test_stream_handles_premature_end_without_result():
    """If the claude subprocess dies before emitting a result, the translator
    should still close the message cleanly so the SSE consumer doesn't hang."""
    events = [
        {"type": "system", "subtype": "init", "model": "sonnet"},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "partial"},
        ], "usage": {"input_tokens": 1, "output_tokens": 1}}},
        # no result event
    ]
    sse_chunks = list(translate_stream(iter(events), model="sonnet"))
    parsed = _parse_sse(sse_chunks)
    names = [n for n, _ in parsed]
    assert names[-1] == "message_stop"
    assert "message_delta" in names
