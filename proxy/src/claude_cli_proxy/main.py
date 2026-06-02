"""CLI entry point: `claude-cli-proxy --port 8765`."""
from __future__ import annotations

import argparse
import logging

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(description="Anthropic API → claude CLI proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    uvicorn.run(
        "claude_cli_proxy.server:app",
        host=args.host,
        port=args.port,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
