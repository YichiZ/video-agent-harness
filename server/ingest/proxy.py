"""Ingest step `proxy`: a 720p H.264 copy of the video for the browser player, `proxy.mp4`.

`-movflags +faststart` puts the index first so the player can seek before the file
has fully loaded; the server serves it with HTTP range support. Videos at most 720p
high keep their height. Setting `ctx.cancel` kills the encode.
"""

from __future__ import annotations

import subprocess
import tempfile
import time

from server.ingest import CANCEL_POLL_S, Cancelled, Step, StepContext

PROXY = "proxy.mp4"
HEIGHT = 720
TIMEOUT_S = 3600  # a 60-minute video at veryfast takes a few minutes


def _wait(ctx: StepContext, proc: subprocess.Popen) -> None:
    """Wait for ffmpeg; raise `Cancelled` on cancel, `RuntimeError` past `TIMEOUT_S`."""
    deadline = time.monotonic() + TIMEOUT_S
    while proc.poll() is None:
        if ctx.cancel.wait(CANCEL_POLL_S):
            raise Cancelled
        if time.monotonic() > deadline:
            raise RuntimeError(f"ffmpeg proxy timed out after {TIMEOUT_S} s")


def run(ctx: StepContext) -> str:
    out = ctx.workspace.path(PROXY)
    tmp = out.with_suffix(".tmp.mp4")
    with tempfile.TemporaryFile("w+") as err:
        proc = subprocess.Popen(
            [
                "ffmpeg", "-v", "error", "-y", "-i", str(ctx.workspace.source),
                "-vf", f"scale=-2:'min({HEIGHT},ih)'", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart", str(tmp),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=err,
        )  # fmt: skip
        try:
            _wait(ctx, proc)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.returncode != 0:
                tmp.unlink(missing_ok=True)
        err.seek(0)
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg proxy failed: {err.read().strip()[-300:]}")
    tmp.replace(out)
    return f"{out.stat().st_size / 1e6:.1f} MB"


STEP = Step(name="proxy", outputs=[PROXY], run=run, requires=["probe"])
