"""`serve [--host HOST] [--port PORT]`: run the HTTP server (`server.api.app`) with uvicorn."""

from __future__ import annotations

import argparse
import sys

import uvicorn
from rich.console import Console

from server import cli
from server.api.app import create_app
from server.config import ConfigError, load_release

DEFAULT_PORT = 8000


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("serve", help="run the HTTP server for the browser UI")
    p.add_argument("--host", default="127.0.0.1", help="interface to bind (default: localhost)")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    try:
        release = load_release()
    except ConfigError as e:
        Console(stderr=True).print(f"error: {e}", markup=False)
        return cli.EXIT_CONFIG
    uvicorn.run(create_app(release=release), host=args.host, port=args.port, log_level="info")
    return cli.EXIT_OK


if __name__ == "__main__":
    sys.exit(cli.main(["serve", *sys.argv[1:]]))
