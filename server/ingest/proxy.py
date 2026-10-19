"""Ingest step `proxy`: a 720p H.264 copy of the video for the browser player, `proxy.mp4`.

`-movflags +faststart` puts the index first so the player can seek before the file
has fully loaded; the server serves it with HTTP range support. Videos at most 720p
high keep their height.
"""

from __future__ import annotations

import subprocess

from server.ingest import Step, StepContext

PROXY = "proxy.mp4"
HEIGHT = 720
TIMEOUT_S = 3600  # a 60-minute video at veryfast takes a few minutes


def run(ctx: StepContext) -> str:
    out = ctx.workspace.path(PROXY)
    tmp = out.with_suffix(".tmp.mp4")
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-i", str(ctx.workspace.source),
            "-vf", f"scale=-2:'min({HEIGHT},ih)'", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(tmp),
        ],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_S,
    )  # fmt: skip
    if result.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg proxy failed: {result.stderr.strip()[-300:]}")
    tmp.replace(out)
    return f"{out.stat().st_size / 1e6:.1f} MB"


STEP = Step(name="proxy", outputs=[PROXY], run=run, requires=["probe"])
