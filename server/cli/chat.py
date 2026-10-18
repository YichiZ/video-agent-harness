"""`chat <video> [--resume]`: a streaming terminal chat with the agent about one video.

It first runs any ingest steps that are still missing, then prints a banner with
the Claude auth mode and the models, and opens a REPL:

- Answer text streams as it is generated; each tool call is one dim line.
- Each turn ends with its cost (API-key mode) or its turn count (subscription).
- Ctrl-C during an answer interrupts it, and at the prompt clears the line; `/quit` or Ctrl-D exit.

Exit codes: 0 ok, 1 config or auth problem, 2 bad input, 3 ingest failed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
from pathlib import Path

from rich.console import Console

from server import cli, ingest
from server.agent.session import ChatSession
from server.auth import AuthMode, detect_auth
from server.config import ConfigError, Settings, load_release
from server.workspace import Workspace

QUIT = {"/quit", "/exit"}


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("chat", help="chat with the agent about a video")
    p.add_argument("video", type=Path, help="path to the video file")
    p.add_argument("--resume", action="store_true", help="continue the last chat about it")
    p.set_defaults(func=run)


def _args(data: dict | None) -> str:
    text = json.dumps(data or {}, ensure_ascii=False)
    return text if len(text) <= 100 else text[:97] + "..."


async def turn(session: ChatSession, prompt: str, out: Console, mode: AuthMode) -> None:
    loop = asyncio.get_running_loop()
    pending: set[asyncio.Task] = set()

    def on_sigint() -> None:
        task = loop.create_task(session.interrupt())
        pending.add(task)
        task.add_done_callback(pending.discard)

    loop.add_signal_handler(signal.SIGINT, on_sigint)
    at_line_start = True
    try:
        async for event in session.ask(prompt):
            if event.kind == "text":
                out.print(event.text, end="", markup=False)
                at_line_start = event.text.endswith("\n")
            elif event.kind == "tool":
                if not at_line_start:
                    out.print()
                out.print(f"  · {event.text} {_args(event.input)}", style="dim", markup=False)
                at_line_start = True
            elif event.kind == "result" and event.result:
                r = event.result
                if not at_line_start:
                    out.print()
                if r.is_error and r.subtype != "error_during_execution":
                    out.print(f"[error: {r.result or r.subtype}]", style="red", markup=False)
                cost = (
                    f"${r.total_cost_usd:.4f}"
                    if mode is AuthMode.API_KEY and r.total_cost_usd is not None
                    else f"{r.num_turns} turn(s)"
                )
                out.print(f"[{cost}]", style="dim", markup=False)
    finally:
        loop.remove_signal_handler(signal.SIGINT)


def repl(session: ChatSession, out: Console, mode: AuthMode) -> None:
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(session.connect())
        while True:
            try:
                prompt = input("\n> ").strip()
            except KeyboardInterrupt:  # at the prompt: drop the line, like a shell
                out.print()
                continue
            except EOFError:
                out.print()
                break
            if prompt in QUIT:
                break
            if prompt:
                try:
                    loop.run_until_complete(turn(session, prompt, out, mode))
                except KeyboardInterrupt:  # landed just after the turn's own handler came off
                    out.print()
                except Exception as e:  # keep the chat alive; the next turn may work
                    out.print(f"\n[error: {type(e).__name__}: {e}]", style="red", markup=False)
    finally:
        loop.run_until_complete(session.disconnect())
        loop.close()


def run(args: argparse.Namespace) -> int:
    out = Console(soft_wrap=True, highlight=False)
    err = Console(stderr=True, soft_wrap=True, highlight=False, markup=False)
    video: Path = args.video.expanduser()
    if not video.is_file():
        err.print(f"error: {video}: no such file")
        return cli.EXIT_BAD_INPUT
    try:
        release = load_release()
    except ConfigError as e:
        err.print(f"error: {e}")
        return cli.EXIT_CONFIG
    settings = Settings.load()
    auth = detect_auth(settings)
    if auth.mode is AuthMode.NONE and not settings.fake_providers:
        err.print(f"error: no Claude auth: {auth.detail}")
        return cli.EXIT_CONFIG

    workspace, created = Workspace.for_video(settings.workspace_dir, video)
    ctx = ingest.StepContext(workspace=workspace, settings=settings, release=release)

    def report(result: ingest.StepResult) -> None:
        if result.state == "done":
            out.print(f"{result.name}: done", style="dim")

    try:
        ingest.run_steps(ctx, on_result=report)
    except ingest.Rejected as e:
        if created:
            workspace.remove()
        err.print(f"error: {video.name}: {e}")
        return cli.EXIT_BAD_INPUT
    except Exception as e:
        err.print(f"error: ingest failed: {type(e).__name__}: {e}")
        return cli.EXIT_STEP_FAILED

    try:
        session = ChatSession(workspace, settings, release, resume=args.resume)
    except LookupError as e:
        err.print(f"error: {e}")
        return cli.EXIT_BAD_INPUT
    out.print(
        f"[bold]{video.name}[/bold]  agent {release.agent_model} · vision {release.gemini_model}"
        f" · {auth.mode.value}" + (" · resumed" if args.resume else "")
    )
    out.print("Ask about the video. Ctrl-C interrupts an answer; /quit exits.", style="dim")
    repl(session, out, auth.mode)
    return cli.EXIT_OK


if __name__ == "__main__":
    sys.exit(cli.main(["chat", *sys.argv[1:]]))
