# hermes-claude-cli-proxy

A local HTTP shim that exposes the `claude` CLI binary as an
Anthropic-compatible Messages API endpoint, so [Hermes
Agent](https://github.com/NousResearch/hermes-agent) (or any other tool
that speaks Anthropic's `/v1/messages`) can route LLM calls through the
authenticated `claude` binary instead of going to `api.anthropic.com`
directly.

Why: the Claude Code OAuth token only authorizes Claude Code itself, not
third-party clients. On a corporate-managed Mac, the local `claude` CLI
is the only sanctioned path to Claude.

## How it works

```
┌─────────────┐  POST /v1/messages   ┌─────────────────┐  stdin (prompt)  ┌─────────────┐
│   Hermes    │ ───────────────────▶ │  claude-cli-    │ ───────────────▶ │   claude    │
│             │ ◀──── SSE stream ─── │  proxy (8765)   │ ◀── NDJSON ───── │   --print   │
└─────────────┘                      └────────┬────────┘                  └─────────────┘
                                              │
                                              │ spawns (per request)
                                              ▼
                                     ┌─────────────────┐
                                     │  hermes_tools   │  ← MCP stdio server
                                     │  (mcp_shim.py)  │     exposes Hermes's tool
                                     └─────────────────┘     names to claude
```

Per `POST /v1/messages` the proxy:

1. Flattens Hermes's `messages: [...]` history into a single text prompt.
2. Spawns a transient MCP stdio server that advertises Hermes's tools.
3. Spawns `claude --print --output-format stream-json --mcp-config ...`.
4. Translates claude's NDJSON event stream back into Anthropic SSE events.
5. Tears down the subprocess after one turn (`--max-turns 1`).

State lives in Hermes — each request is a fresh claude invocation with the
full conversation re-serialized.

## Layout

```
proxy/
  src/claude_cli_proxy/
    server.py        # FastAPI app, claude subprocess orchestration
    flatten.py       # Anthropic messages → flat prompt
    translate.py     # claude NDJSON → Anthropic SSE / non-streaming JSON
    mcp_shim.py      # stdio MCP server that advertises Hermes's tools
    main.py          # `claude-cli-proxy --port 8765` entry point
  scripts/
    com.salesforcebob.sfhermes.proxy.plist  # launchd plist template
    install-launchd.sh    # install + load launchd agent (recommended)
    uninstall-launchd.sh  # unload + remove launchd agent
    sfhermes              # CLI shim adding `sfhermes doctor` / `sfhermes proxy ...`
    install-sfhermes.sh   # symlink ~/.local/bin/sfhermes → scripts/sfhermes
    hermes-launcher.sh    # legacy: on-demand wrapper for ~/.local/bin/hermes
    install-launcher.sh   # legacy: install hermes-launcher.sh (clobbers upstream)
```

## Setup

```bash
cd proxy
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e .
```

Point Hermes at the proxy in `~/.hermes/config.yaml`:

```yaml
model:
  default: claude-sonnet-4-5
  provider: anthropic
  base_url: http://127.0.0.1:8765
```

And set a dummy API key in `~/.hermes/.env` (the proxy doesn't validate
auth, but Hermes's anthropic transport requires the key to exist):

```
ANTHROPIC_API_KEY=sk-ant-proxy-dummy
```

Install the launchd agent so the proxy auto-starts at login and survives crashes:

```bash
./scripts/install-launchd.sh
```

Install the `sfhermes` CLI shim (adds `sfhermes doctor`, `sfhermes proxy
{status,start,stop,log}` on top of stock `hermes`):

```bash
./scripts/install-sfhermes.sh
```

After that, run `hermes` as usual — the proxy is already up via launchd.

### Diagnostics

```bash
sfhermes doctor              # check proxy + hermes config alignment
sfhermes proxy status        # is the proxy running?
sfhermes proxy log           # tail the proxy log
sfhermes proxy stop          # stop launchd-managed proxy
sfhermes proxy start         # restart launchd-managed proxy
```

### Alternative: on-demand launcher (no launchd)

If you don't want a long-running background service, the older launcher
wrapper at `scripts/hermes-launcher.sh` will start the proxy on the
first `hermes` invocation. Install with `./scripts/install-launcher.sh`
— but note this overwrites `~/.local/bin/hermes` and gets clobbered by
`hermes update`. The launchd path is recommended.

## Caveats

- **Single-turn per HTTP request.** The proxy spawns one `claude --print`
  per request and tears it down. Hermes is the source of truth for history.
- **Tool calls are emitted, not executed.** The MCP shim returns an error
  sentinel if claude actually tries to invoke a tool; `--max-turns 1`
  ensures claude exits after the first `tool_use` block, before the shim
  is hit.
- **launchd survives `hermes update`.** Only the legacy on-demand
  launcher wrapper is at risk — and it's not used by default.
- **No streaming-mid-block deltas.** claude emits whole content blocks at
  a time, so SSE deltas arrive in chunks rather than token-by-token.
