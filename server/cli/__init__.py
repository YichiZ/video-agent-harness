"""`python -m server.cli <command>`: the harness's command line.

Each subcommand lives in its own module in this package and exposes
`register(subparsers)`, which adds its parser and sets `func` to a function
that takes the parsed args and returns the exit code. To add a command, write
the module and add its name to `COMMANDS`; nothing else here changes.

Exit codes shared by all commands:

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Environment or config problem (for example a broken `release.json`) |
| 2 | Bad input (for example a video longer than the cap) |
| 3 | An ingest step failed |
"""

from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Sequence

# Subcommand modules, in help order. Later steps append "chat" and "tool".
COMMANDS = ["doctor", "ingest", "tool", "chat", "chats", "serve"]

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_BAD_INPUT = 2
EXIT_STEP_FAILED = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m server.cli",
        description="Chat with an agent about one video.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="<command>")
    for name in COMMANDS:
        importlib.import_module(f"server.cli.{name}").register(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
