"""Integration tests for the proxy HTTP server using a fake claude binary."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import httpx
import pytest

from claude_cli_proxy.server import create_app


@pytest.fixture
def fake_claude(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Install a fake `claude` script on PATH that emits canned NDJSON.

    The fake reads CLAUDE_FAKE_RESPONSE env var (path to a file) and
    cats it as stdout. Lets each test specify the exact NDJSON the
    proxy will see without spawning real Claude/Bedrock.
    """
    fake = tmp_path / "claude"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        "# Read and discard stdin so the parent doesn't block on broken pipe\n"
        "cat > /dev/null\n"
        'cat "$CLAUDE_FAKE_RESPONSE"\n'
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    return fake


def _write_ndjson(path: Path, events: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


@pytest.fixture
def app_with_fake_claude(fake_claude: Path):
    """Build a fresh FastAPI app instance that resolves to the fake claude."""
    return create_app()


@pytest.fixture
def client(app_with_fake_claude) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app_with_fake_claude)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_healthz(client: httpx.AsyncClient):
    async with client:
        r = await client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_non_streaming_text_response(
    tmp_path: Path,
    fake_claude: Path,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    response_file = _write_ndjson(tmp_path / "resp.ndjson", [
        {"type": "system", "subtype": "init", "model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0"},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "PROXY_OK"}],
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }},
        {"type": "result", "subtype": "success", "stop_reason": "end_turn",
         "usage": {"input_tokens": 5, "output_tokens": 3}},
    ])
    monkeypatch.setenv("CLAUDE_FAKE_RESPONSE", str(response_file))

    async with client:
        r = await client.post(
            "/v1/messages",
            json={"model": "sonnet", "max_tokens": 1024,
                  "messages": [{"role": "user", "content": "ping"}]},
            timeout=10,
        )
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "message"
    assert body["stop_reason"] == "end_turn"
    text_blocks = [b for b in body["content"] if b.get("type") == "text"]
    assert any(b["text"] == "PROXY_OK" for b in text_blocks)


@pytest.mark.asyncio
async def test_tool_use_response(
    tmp_path: Path,
    fake_claude: Path,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    response_file = _write_ndjson(tmp_path / "resp.ndjson", [
        {"type": "system", "subtype": "init", "model": "sonnet"},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "toolu_1",
                 "name": "mcp__hermes_tools__terminal",
                 "input": {"cmd": "ls"}},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }},
        {"type": "result", "subtype": "success", "stop_reason": "tool_use",
         "usage": {"input_tokens": 10, "output_tokens": 5}},
    ])
    monkeypatch.setenv("CLAUDE_FAKE_RESPONSE", str(response_file))

    async with client:
        r = await client.post(
            "/v1/messages",
            json={
                "model": "sonnet", "max_tokens": 1024,
                "messages": [{"role": "user", "content": "ls please"}],
                "tools": [{
                    "name": "terminal",
                    "description": "run a shell command",
                    "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}},
                }],
            },
            timeout=10,
        )
    assert r.status_code == 200
    body = r.json()
    assert body["stop_reason"] == "tool_use"
    tool_blocks = [b for b in body["content"] if b.get("type") == "tool_use"]
    assert len(tool_blocks) == 1
    # MCP prefix must be stripped on the way back to the client
    assert tool_blocks[0]["name"] == "terminal"
    assert tool_blocks[0]["input"] == {"cmd": "ls"}


@pytest.mark.asyncio
async def test_streaming_response_emits_sse_events(
    tmp_path: Path,
    fake_claude: Path,
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    response_file = _write_ndjson(tmp_path / "resp.ndjson", [
        {"type": "system", "subtype": "init", "model": "sonnet"},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "STREAMED"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }},
        {"type": "result", "stop_reason": "end_turn",
         "usage": {"input_tokens": 1, "output_tokens": 1}},
    ])
    monkeypatch.setenv("CLAUDE_FAKE_RESPONSE", str(response_file))

    async with client:
        async with client.stream(
            "POST", "/v1/messages",
            json={"model": "sonnet", "stream": True,
                  "messages": [{"role": "user", "content": "ping"}]},
            timeout=10,
        ) as r:
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/event-stream")
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
    text = body.decode()
    assert "event: message_start" in text
    assert "event: content_block_start" in text
    assert '"text": "STREAMED"' in text
    assert "event: message_stop" in text
