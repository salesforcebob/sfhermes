"""Flatten an Anthropic-shaped messages array into a single text prompt.

Hermes (and any client speaking Anthropic's Messages API) sends `messages: [...]`
as the full conversation. `claude --print` only accepts a single prompt. We
serialize the history with simple XML-ish markers so the model can follow
along, then append the latest user turn as the actual instruction.
"""
from __future__ import annotations

import json
from typing import Any


def _block_to_text(block: dict[str, Any]) -> str:
    """Render one content block as plain text for the flattened prompt."""
    btype = block.get("type")
    if btype == "text":
        return block.get("text", "")
    if btype == "thinking":
        return f"<thinking>{block.get('thinking', '')}</thinking>"
    if btype == "tool_use":
        tid = block.get("id", "")
        name = block.get("name", "")
        inp = json.dumps(block.get("input", {}), ensure_ascii=False)
        return f'<tool_use id="{tid}" name="{name}">{inp}</tool_use>'
    if btype == "tool_result":
        tid = block.get("tool_use_id", "")
        content = block.get("content", "")
        if isinstance(content, list):
            content = "".join(_block_to_text(c) if isinstance(c, dict) else str(c) for c in content)
        is_error = block.get("is_error", False)
        err_attr = ' is_error="true"' if is_error else ""
        return f'<tool_result tool_use_id="{tid}"{err_attr}>{content}</tool_result>'
    return ""


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_block_to_text(b) if isinstance(b, dict) else str(b) for b in content)
    return ""


def flatten_messages(messages: list[dict[str, Any]]) -> str:
    """Serialize a Messages-API history into a single prompt string.

    Each turn becomes a `<user>...</user>` or `<assistant>...</assistant>`
    block. Tool calls and results are nested as XML-ish markers. The result
    is fed to `claude --print` as the prompt; the model treats it as the
    latest user turn so the closing instruction matters.
    """
    if not messages:
        return ""
    lines: list[str] = []
    for msg in messages:
        role = msg.get("role", "user")
        text = _content_to_text(msg.get("content", ""))
        lines.append(f"<{role}>\n{text}\n</{role}>")
    history = "\n".join(lines)
    return (
        "You are continuing a conversation. The full prior history follows "
        "in chronological order, marked up with XML-ish tags. Respond to the "
        "final user turn as if you had spoken every prior assistant turn "
        "yourself. Do not repeat or summarize earlier turns.\n\n"
        f"{history}"
    )
