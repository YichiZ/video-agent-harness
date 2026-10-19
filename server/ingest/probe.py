"""Ingest step `probe`: ffprobe the source, validate it, write `meta.json`.

Rejects (exit code 2) files with no video stream and videos longer than
`release.max_video_minutes`.

`meta.json`:

    {
      "video_id": "3f1c...", "source_name": "TbaCxIJ_VP4.mp4", "source_path": "/abs/...",
      "size_bytes": 4194304, "container": "mov,mp4,m4a,3gp,3g2,mj2",
      "duration_s": 83.92, "fps": 29.97, "width": 1280, "height": 720, "rotation": 0,
      "video": {"index": 0, "codec": "h264", "width": 1280, "height": 720, "fps": 29.97,
                "frames": 2515, "pix_fmt": "yuv420p"},
      "audio": {"index": 1, "codec": "aac", "sample_rate": 44100, "channels": 2} | null,
      "streams": [{"index": 0, "type": "video", "codec": "h264"}, ...]
    }

`width` and `height` at the top level are the display size, after rotation.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

from server.ingest import Rejected, Step, StepContext

META = "meta.json"


class ProbeError(RuntimeError):
    """ffprobe could not read the file."""


def ffprobe(path: Path) -> dict[str, Any]:
    """Run ffprobe and return its JSON (`format` and `streams`)."""
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe not found on PATH (run `make doctor`)")
    result = subprocess.run(
        [exe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise ProbeError(result.stderr.strip() or f"ffprobe exited {result.returncode}")
    return json.loads(result.stdout)


def parse_rate(rate: str | None) -> float | None:
    """'30000/1001' -> 29.97. None for missing or zero rates ('0/0')."""
    if not rate:
        return None
    try:
        value = Fraction(rate)
    except (ValueError, ZeroDivisionError):
        return None
    return round(float(value), 3) if value > 0 else None


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rotation(stream: dict[str, Any]) -> int:
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            return int(round(float(side["rotation"]))) % 360
    tag = (stream.get("tags") or {}).get("rotate")
    return int(tag) % 360 if tag and tag.lstrip("-").isdigit() else 0


def format_clock(seconds: float) -> str:
    """83.9 -> '1:23', 3660 -> '1:01:00'."""
    total = int(seconds)
    h, rest = divmod(total, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def build_meta(probe: dict[str, Any], *, max_seconds: float) -> dict[str, Any]:
    """Validate ffprobe output and turn it into `meta.json` (without the source fields)."""
    streams = probe.get("streams") or []
    videos = [
        s
        for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    if not videos:
        raise Rejected("no video stream")
    video = videos[0]
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    fmt = probe.get("format") or {}
    duration = _float(fmt.get("duration")) or _float(video.get("duration"))
    if not duration or duration <= 0:
        raise Rejected("no video stream (the file has no duration, so it may be a still image)")
    if duration > max_seconds:
        raise Rejected(
            f"video is longer than {max_seconds / 60:g} minutes ({format_clock(duration)})"
        )

    fps = parse_rate(video.get("avg_frame_rate")) or parse_rate(video.get("r_frame_rate"))
    width, height = int(video.get("width") or 0), int(video.get("height") or 0)
    rotation = _rotation(video)
    display_w, display_h = (height, width) if rotation in (90, 270) else (width, height)
    frames = video.get("nb_frames")

    return {
        "size_bytes": int(fmt["size"]) if fmt.get("size") else None,
        "container": fmt.get("format_name"),
        "duration_s": round(duration, 3),
        "fps": fps,
        "width": display_w,
        "height": display_h,
        "rotation": rotation,
        "video": {
            "index": video.get("index"),
            "codec": video.get("codec_name"),
            "width": width,
            "height": height,
            "fps": fps,
            "frames": int(frames) if frames and str(frames).isdigit() else None,
            "pix_fmt": video.get("pix_fmt"),
        },
        "audio": (
            {
                "index": audio.get("index"),
                "codec": audio.get("codec_name"),
                "sample_rate": int(audio["sample_rate"]) if audio.get("sample_rate") else None,
                "channels": audio.get("channels"),
            }
            if audio
            else None
        ),
        "streams": [
            {"index": s.get("index"), "type": s.get("codec_type"), "codec": s.get("codec_name")}
            for s in streams
        ],
    }


def run(ctx: StepContext) -> str:
    source = ctx.workspace.source
    try:
        probe = ffprobe(source)
    except ProbeError:
        # ffprobe fails on anything that is not a media file, such as a .txt.
        raise Rejected("no video stream (ffprobe could not read the file)") from None
    meta = build_meta(probe, max_seconds=ctx.release.max_video_seconds)
    original = source.resolve()
    meta = {
        "video_id": ctx.workspace.id,
        "source_name": original.name,
        "source_path": str(original),
        **meta,
    }
    ctx.workspace.write_json(META, meta)
    size = f"{meta['width']}x{meta['height']}"
    fps = f", {meta['fps']:g} fps" if meta["fps"] else ""
    audio = "" if meta["audio"] else ", no audio"
    return f"{format_clock(meta['duration_s'])} ({meta['duration_s']:g} s), {size}{fps}{audio}"


STEP = Step(name="probe", outputs=[META], run=run)
