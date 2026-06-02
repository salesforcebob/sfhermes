"""FastAPI server: Anthropic Messages API → claude CLI subprocess."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .flatten import flatten_messages
from .translate import collect_non_streaming, translate_stream

logger = logging.getLogger("claude_cli_proxy")

# Map Anthropic model names to claude CLI aliases. claude accepts aliases like
# "sonnet", "opus", "haiku" as shortcuts to the latest of each tier; full IDs
# pass through unchanged.
_MODEL_ALIASES = {
    "claude-sonnet-4-5": "sonnet",
    "claude-sonnet-4-6": "sonnet",
    "claude-opus-4-5": "opus",
    "claude-opus-4-6": "opus",
    "claude-opus-4-7": "opus",
    "claude-haiku-4-5": "haiku",
}


def _resolve_model(name: str) -> str:
    if not name:
        return "sonnet"
    base = name.split("/", 1)[-1]
    base = base.split(":", 1)[0]
    return _MODEL_ALIASES.get(base, base)


def _build_mcp_config(tools: list[dict[str, Any]] | None, shim_module: str) -> Path | None:
    """Write a temporary MCP config file that registers our shim as the sole MCP server."""
    if not tools:
        return None
    tmp = Path(tempfile.mkdtemp(prefix="claude_proxy_"))
    config_path = tmp / "mcp.json"
    config = {
        "mcpServers": {
            "hermes_tools": {
                "command": sys.executable,
                "args": ["-m", shim_module],
                "env": {"HERMES_PROXY_TOOLS": json.dumps(tools)},
            }
        }
    }
    config_path.write_text(json.dumps(config, indent=2))
    return config_path


def _build_claude_cmd(
    *,
    claude_bin: str,
    model: str,
    system: str | None,
    mcp_config: Path | None,
    max_turns: int,
) -> list[str]:
    cmd = [
        claude_bin,
        "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--input-format", "text",
        "--model", model,
        "--bare",
        "--no-session-persistence",
        "--permission-mode", "bypassPermissions",
        "--max-turns", str(max_turns),
    ]
    if system:
        cmd += ["--append-system-prompt", system]
    if mcp_config is not None:
        cmd += [
            "--strict-mcp-config",
            "--mcp-config", str(mcp_config),
            # Block all of claude's built-in tools so the model is forced
            # to use the host-owned MCP tools when it wants to act.
            "--disallowedTools", "Bash", "Edit", "Read", "Write", "Glob", "Grep",
        ]
    return cmd


async def _spawn_claude(
    *,
    cmd: list[str],
    prompt: str,
) -> AsyncIterator[dict[str, Any]]:
    """Spawn claude, write the prompt on stdin, yield parsed NDJSON events."""
    env = os.environ.copy()
    # Belt-and-suspenders: refuse to forward an Anthropic API key into the
    # subprocess. The CLI uses its own auth (Bedrock SSO / OAuth / etc.).
    env.pop("ANTHROPIC_API_KEY", None)

    logger.info("spawning: %s", " ".join(cmd[:8]))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    assert proc.stdin and proc.stdout and proc.stderr

    # Write the flattened prompt to stdin and close it so claude knows
    # the input is complete.
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()

    try:
        async for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("non-JSON line from claude: %r", line[:200])
                continue
            yield event
    finally:
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        # Drain stderr for diagnostics
        stderr = await proc.stderr.read()
        if stderr:
            logger.debug("claude stderr: %s", stderr.decode("utf-8", errors="replace")[:500])


def create_app() -> FastAPI:
    app = FastAPI(title="claude-cli-proxy")

    claude_bin = shutil.which("claude") or "/Users/robert.ullery/.local/bin/claude"
    shim_module = "claude_cli_proxy.mcp_shim"

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "claude": claude_bin}

    @app.post("/v1/messages")
    async def messages(request: Request) -> Any:
        body = await request.json()
        model = _resolve_model(body.get("model", "sonnet"))
        system = body.get("system")
        if isinstance(system, list):
            # Anthropic accepts system as a list of {type, text} blocks
            system = "\n\n".join(b.get("text", "") for b in system if isinstance(b, dict))
        msgs = body.get("messages", [])
        tools = body.get("tools") or []
        stream = bool(body.get("stream", False))

        prompt = flatten_messages(msgs)
        mcp_config = _build_mcp_config(tools, shim_module)
        cmd = _build_claude_cmd(
            claude_bin=claude_bin,
            model=model,
            system=system,
            mcp_config=mcp_config,
            max_turns=1,
        )

        # Use a wrapper that yields events from the async generator, since the
        # translator expects a sync iterator. We bridge via an async-to-sync
        # event collector for non-streaming, and a true async generator for SSE.
        if stream:
            async def sse_iter() -> AsyncIterator[bytes]:
                events: list[dict[str, Any]] = []
                async for event in _spawn_claude(cmd=cmd, prompt=prompt):
                    events.append(event)
                # NB: we collect first then translate. True real-time streaming
                # would translate inline; this keeps the translator sync-only
                # for simplicity. claude's events arrive in coarse blocks
                # (whole assistant messages), so the buffering is small.
                for sse in translate_stream(iter(events), model=model):
                    yield sse.encode("utf-8")

            return StreamingResponse(sse_iter(), media_type="text/event-stream")

        events: list[dict[str, Any]] = []
        async for event in _spawn_claude(cmd=cmd, prompt=prompt):
            events.append(event)
        response = collect_non_streaming(iter(events), model=model)
        return JSONResponse(response)

    return app


app = create_app()
