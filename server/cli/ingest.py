"""`ingest <video> [--only STEP ...] [--force] [--retry STEP ...]`: build the video's workspace.

Steps run in parallel (see `server.ingest`). Prints one line per step as it
finishes (`probe: done (...)`, `probe: skipped`, `captions: failed (...)`) and
then the workspace path; `status.json` in the workspace records every step's
state. `--retry captions` re-runs a step (failed or not) and then whatever else
is missing. Exit codes: 0 ok, 2 the video was rejected, 3 a step failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

from server import cli, ingest
from server.config import ConfigError, Settings, load_release
from server.workspace import Workspace


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("ingest", help="probe and index a video into its workspace")
    p.add_argument("video", type=Path, help="path to the video file")
    p.add_argument(
        "--only",
        action="append",
        choices=list(ingest.steps()),
        metavar="STEP",
        help=f"run only this step and what it needs (repeatable): {', '.join(ingest.steps())}",
    )
    p.add_argument("--force", action="store_true", help="re-run the named steps even if done")
    p.add_argument(
        "--retry",
        action="append",
        default=[],
        choices=list(ingest.steps()),
        metavar="STEP",
        help="re-run this step, then finish the rest of the ingest (repeatable)",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    out = Console(soft_wrap=True, highlight=False)
    err = Console(stderr=True, soft_wrap=True, highlight=False)
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

    workspace, created = Workspace.for_video(settings.workspace_dir, video)
    ctx = ingest.StepContext(workspace=workspace, settings=settings, release=release)

    def report(result: ingest.StepResult) -> None:
        summary = f" ({result.summary})" if result.summary else ""
        out.print(f"{result.name}: {result.state}{summary}")

    names = None if args.retry else args.only
    force = [*((args.only or ingest.steps()) if args.force else ()), *args.retry]
    try:
        results = ingest.run_steps(ctx, names, force=force, on_result=report)
    except ingest.Rejected as e:
        if created:
            workspace.remove()
        err.print(f"error: {video.name}: {e}")
        return cli.EXIT_BAD_INPUT
    except Exception as e:  # any other failure belongs to the step that raised it
        err.print(f"error: ingest failed: {type(e).__name__}: {e}")
        return cli.EXIT_STEP_FAILED
    out.print(f"workspace: {workspace.dir}")
    failed = [r.name for r in ingest.failures(results) if r.state == "failed"]
    if failed:
        err.print(f"error: ingest failed: {', '.join(failed)}; rerun with --retry <step>")
        return cli.EXIT_STEP_FAILED
    return cli.EXIT_OK


if __name__ == "__main__":
    sys.exit(cli.main(["ingest", *sys.argv[1:]]))
