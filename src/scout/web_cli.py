"""Command-line entry point for Scout's web workbench."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn


def main(argv: list[str] | None = None) -> int:
    """Run the Scout web application."""
    parser = argparse.ArgumentParser(prog="scout-web")
    parser.add_argument("--workspace", "-w", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    os.environ["SCOUT_WORKSPACE"] = str(Path(args.workspace).resolve())
    uvicorn.run(
        "scout.web.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0
