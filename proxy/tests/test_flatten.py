"""Unit tests for the message-history flattener."""
from __future__ import annotations

from claude_cli_proxy.flatten import flatten_messages


def test_empty_messages_returns_empty_string():
    assert flatten_messages([]) == ""


def test_single_user_text_message():
    out = flatten_messages([{"role": "user", "content": "hello"}])
    assert "<user>" in out
    assert "hello" in out
    assert "</user>" in out


def test_user_assistant_alternation():
    out = flatten_messages([
        {"role": "user", "content": "ping"},
        {"role": "assistant", "content": "pong"},
        {"role": "user", "content": "ping again"},
    ])
    # Order is preserved
    assert out.index("ping") < out.index("pong") < out.index("ping again")
    assert "<assistant>" in out and "</assistant>" in out


def test_assistant_tool_use_block():
    out = flatten_messages([
        {"role": "user", "content": "weather?"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Checking..."},
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Paris"}},
        ]},
    ])
    assert "Checking..." in out
    assert '<tool_use id="toolu_1" name="get_weather">' in out
    assert '"city": "Paris"' in out


def test_user_tool_result_block():
    out = flatten_messages([
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "22C, sunny"},
        ]},
    ])
    assert '<tool_result tool_use_id="toolu_1">22C, sunny</tool_result>' in out


def test_tool_result_error_flag():
    out = flatten_messages([
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": "boom", "is_error": True},
        ]},
    ])
    assert 'is_error="true"' in out


def test_thinking_block_preserved():
    out = flatten_messages([
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "Let me think..."},
            {"type": "text", "text": "ok"},
        ]},
    ])
    assert "<thinking>Let me think...</thinking>" in out
    assert "ok" in out


def test_unknown_block_type_silently_dropped():
    # Future-proof: don't crash on unknown types.
    out = flatten_messages([
        {"role": "user", "content": [
            {"type": "image", "source": {"data": "..."}},
            {"type": "text", "text": "describe"},
        ]},
    ])
    assert "describe" in out
    assert "image" not in out  # silently dropped


def test_nested_tool_result_content_list():
    out = flatten_messages([
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": [
                {"type": "text", "text": "stdout: ok"},
            ]},
        ]},
    ])
    assert "stdout: ok" in out
