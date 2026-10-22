"""`chats <video>`: list the chats about a video, most recent first.

One line per chat: its id (for `chat <video> --chat <id>`), when it was last
updated, and its title.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from server import chats, cli
from server.config import Settings
from server.workspace import Workspace, video_id


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("chats", help="list the chats about a video")
    p.add_argument("video", type=Path, help="path to the video file")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    video: Path = args.video.expanduser()
    if not video.is_file():
        print(f"error: {video}: no such file", file=sys.stderr)
        return cli.EXIT_BAD_INPUT
    workspace = Workspace(Settings.load().workspace_dir / video_id(video))
    found = chats.chats(workspace)
    if not found:
        print("no chats yet")
    for chat in found:
        summary = chat.summary()
        updated = datetime.fromtimestamp(summary["updated"]).strftime("%Y-%m-%d %H:%M")
        print(f"{chat.id}  {updated}  {summary['title']}")
    return cli.EXIT_OK


if __name__ == "__main__":
    sys.exit(cli.main(["chats", *sys.argv[1:]]))
