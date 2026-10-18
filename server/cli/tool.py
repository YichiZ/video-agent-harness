"""`tool <name> '<json args>' [--video PATH]`: call one agent tool through the real MCP server.

A debugging aid for plan step 4. Without `--video` it uses the most recently
ingested workspace. It prints the tool's text result, and for image blocks one
line with the image's type and size. Exit codes: 0 ok, 2 bad input or the tool
returned an error, 3 the video's ingest failed.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

from mcp import Client
from rich.console import Console

from server import cli, ingest
from server.agent.tools import TOOL_NAMES, VideoTools, build_server
from server.config import ConfigError, Settings, load_release
from server.workspace import Workspace


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("tool", help="call one agent tool (debugging)")
    p.add_argument("name", choices=TOOL_NAMES, metavar="name", help=", ".join(TOOL_NAMES))
    p.add_argument("args", nargs="?", default="{}", help="the tool's arguments as JSON")
    p.add_argument("--video", type=Path, help="video to use (default: latest workspace)")
    p.set_defaults(func=run)


def latest_workspace(root: Path) -> Workspace | None:
    metas = list(root.glob("*/meta.json"))
    return Workspace(max(metas, key=lambda p: p.stat().st_mtime).parent) if metas else None


async def call(server, name: str, args: dict) -> tuple[list, bool]:
    async with Client(server["instance"]) as client:
        result = await client.call_tool(name, args)
    return result.content, bool(result.is_error)


def run(args: argparse.Namespace) -> int:
    out = Console(soft_wrap=True, highlight=False, markup=False)
    err = Console(stderr=True, soft_wrap=True, highlight=False, markup=False)
    try:
        tool_args = json.loads(args.args)
    except json.JSONDecodeError as e:
        err.print(f"error: tool arguments are not valid JSON: {e}")
        return cli.EXIT_BAD_INPUT
    if not isinstance(tool_args, dict):
        err.print("error: tool arguments must be a JSON object")
        return cli.EXIT_BAD_INPUT
    try:
        release = load_release()
    except ConfigError as e:
        err.print(f"error: {e}")
        return cli.EXIT_CONFIG
    settings = Settings.load()

    if args.video:
        video = args.video.expanduser()
        if not video.is_file():
            err.print(f"error: {video}: no such file")
            return cli.EXIT_BAD_INPUT
        workspace, _ = Workspace.for_video(settings.workspace_dir, video)
        ctx = ingest.StepContext(workspace=workspace, settings=settings, release=release)
        try:
            ingest.run_steps(ctx, ["transcribe", "overview"])
        except Exception as e:
            err.print(f"error: ingest failed: {type(e).__name__}: {e}")
            return cli.EXIT_STEP_FAILED
    else:
        workspace = latest_workspace(settings.workspace_dir)
        if workspace is None:
            err.print("error: no ingested video; pass --video")
            return cli.EXIT_BAD_INPUT

    server = build_server(VideoTools(workspace, settings, release))
    content, is_error = asyncio.run(call(server, args.name, tool_args))
    for block in content:
        if block.type == "text":
            (err if is_error else out).print(block.text)
        elif block.type == "image":
            out.print(f"[image {block.mime_type} {len(base64.b64decode(block.data))} bytes]")
    return cli.EXIT_BAD_INPUT if is_error else cli.EXIT_OK


if __name__ == "__main__":
    sys.exit(cli.main(["tool", *sys.argv[1:]]))
