"""The agent's video tools, served as the in-process MCP server `video` (plan step 4).

Each tool is a plain method on `VideoTools`, which is bound to one ingested
workspace. `build_server()` wraps them with `@tool` for the Agent SDK, so Claude
sees them as `mcp__video__<name>`. Every tool returns JSON as text, except
`get_frames`, which also returns the frames as JPEG image blocks.

| Tool | Reads |
|---|---|
| `get_overview()` | `overview.json`, `meta.json` |
| `search(query, k)` | `transcript.json` (case-insensitive; step 8 replaces it) |
| `get_transcript(start, end)` | `transcript.json` |
| `inspect(question, start, end, fps)` | Gemini, restricted to the range; cached in `inspect/` |
| `get_frames(timestamps)` | the source video, through ffmpeg |

Times are seconds in arguments and both seconds and `mm:ss` in results. Every
range is clamped to the video's duration. `ToolError` messages go back to the
agent as error results.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from server.config import Release, Settings
from server.ingest.overview import OVERVIEW, ensure_active, format_clock
from server.ingest.probe import META
from server.ingest.transcribe import TRANSCRIPT
from server.providers import JSON, VisionModel, get_vision_model
from server.workspace import Workspace

SERVER_NAME = "video"
INSPECT_DIR = "inspect"
MAX_FRAMES = 8
DEFAULT_K = 5
FRAME_HEIGHT = 720

INSPECT_PROMPT = """\
Answer the question about this part of the video ({start} to {end}).
Look at the frames and listen to the audio. Answer only from what you see and
hear in this range; if the range does not show the answer, say so.
For every observation the answer relies on, give the time it happens as mm:ss
from the start of the whole video.

Question: {question}"""

INSPECT_SCHEMA: JSON = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "time": {"type": "string", "description": "mm:ss"},
                    "observation": {"type": "string"},
                },
                "required": ["time", "observation"],
            },
        },
    },
    "required": ["answer", "observations"],
}


class ToolError(Exception):
    """A tool call the agent should fix or stop making. The message is shown to it."""


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


@dataclass
class VideoTools:
    workspace: Workspace
    settings: Settings
    release: Release
    inspect_calls: int = 0
    """Provider calls made by `inspect` (cache hits excluded), capped by `inspect_limit`."""
    _vision: VisionModel | None = field(default=None, repr=False)

    def _read(self, name: str) -> Any:
        if not self.workspace.path(name).exists():
            raise ToolError(f"{name} is missing; ingest the video first")
        return self.workspace.read_json(name)

    @property
    def duration(self) -> float:
        return float(self._read(META)["duration_s"])

    def _range(self, start: float | None, end: float | None) -> tuple[float, float]:
        lo = max(0.0, min(float(start or 0.0), self.duration))
        hi = self.duration if end is None else max(0.0, min(float(end), self.duration))
        if hi <= lo:
            raise ToolError(f"empty time range {lo:g}-{hi:g} s (video is {self.duration:g} s)")
        return lo, hi

    @staticmethod
    def _segment(seg: JSON) -> JSON:
        return {
            "start": seg["start"],
            "end": seg["end"],
            "time": format_clock(seg["start"]),
            "speaker": seg["speaker"],
            "text": seg["text"].strip(),
        }

    # Tools

    def get_overview(self) -> JSON:
        return {
            "duration_s": self.duration,
            "duration": format_clock(self.duration),
            **self._read(OVERVIEW),
        }

    def search(self, query: str, k: int = DEFAULT_K) -> JSON:
        """Rank segments by the share of query words they contain; an exact phrase wins."""
        terms = set(_words(query))
        if not terms:
            raise ToolError("search needs a query with at least one word")
        phrase = " ".join(_words(query))
        hits = []
        for seg in self._read(TRANSCRIPT)["segments"]:
            words = _words(seg["text"])
            score = len(terms & set(words)) / len(terms) + (phrase in " ".join(words))
            if score > 0:
                hits.append((score, seg))
        hits.sort(key=lambda h: (-h[0], h[1]["start"]))
        return {
            "query": query,
            "hits": [{"score": round(s, 2), **self._segment(seg)} for s, seg in hits[: max(1, k)]],
        }

    def get_transcript(self, start: float | None = None, end: float | None = None) -> JSON:
        lo, hi = self._range(start, end)
        segments = [
            self._segment(seg)
            for seg in self._read(TRANSCRIPT)["segments"]
            if seg["start"] < hi and seg["end"] > lo
        ]
        return {"start": lo, "end": hi, "segments": segments}

    def inspect(
        self,
        question: str,
        start: float | None = None,
        end: float | None = None,
        fps: float | None = None,
    ) -> JSON:
        lo, hi = self._range(start, end)
        request = {"question": question, "start": lo, "end": hi, "fps": fps}
        prompt = INSPECT_PROMPT.format(
            start=format_clock(lo), end=format_clock(hi), question=question
        )
        cache_key = {**request, "model": self.release.gemini_model, "prompt": prompt}
        key = hashlib.sha256(json.dumps(cache_key, sort_keys=True).encode()).hexdigest()[:16]
        cache = self.workspace.path(INSPECT_DIR) / f"{key}.json"
        if cache.exists():
            return {**json.loads(cache.read_text()), "cached": True}
        if self.inspect_calls >= self.release.inspect_limit:
            raise ToolError(
                f"inspect limit reached ({self.release.inspect_limit} calls); "
                "answer from what you have, or use get_frames"
            )
        self.inspect_calls += 1
        if self._vision is None:
            self._vision = get_vision_model(self.settings, self.release)
        file, _ = ensure_active(self._vision, self.workspace)
        prompt = INSPECT_PROMPT.format(
            start=format_clock(lo), end=format_clock(hi), question=question
        )
        raw = self._vision.generate_json(
            file=file, prompt=prompt, schema=INSPECT_SCHEMA, purpose="inspect",
            start=lo, end=hi, fps=fps,
        )  # fmt: skip
        result = {**request, "answer": raw["answer"], "observations": raw["observations"]}
        cache.parent.mkdir(exist_ok=True)
        self.workspace.write_json(f"{INSPECT_DIR}/{key}.json", result)
        return {**result, "cached": False}

    def get_frames(self, timestamps: list[float]) -> tuple[JSON, list[bytes]]:
        if not timestamps:
            raise ToolError("get_frames needs at least one timestamp")
        if len(timestamps) > MAX_FRAMES:
            raise ToolError(
                f"get_frames takes at most {MAX_FRAMES} timestamps, got {len(timestamps)}"
            )
        last = max(0.0, self.duration - 0.05)  # the last decodable frame sits just before the end
        times = [max(0.0, min(float(t), last)) for t in timestamps]
        frames = [self._frame(t) for t in times]
        return {"frames": [{"t": t, "time": format_clock(t)} for t in times]}, frames

    def _frame(self, t: float) -> bytes:
        result = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(self.workspace.source),
                "-frames:v", "1", "-vf", f"scale=-2:'min({FRAME_HEIGHT},ih)'",
                "-q:v", "3", "-f", "image2pipe", "-c:v", "mjpeg", "-",
            ],
            capture_output=True,
            timeout=60,
        )  # fmt: skip
        if result.returncode != 0 or not result.stdout:
            raise ToolError(f"could not read a frame at {t:g} s: {result.stderr.decode()[-200:]}")
        return result.stdout


# MCP wrappers


def _text(data: JSON) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}


def _error(e: ToolError) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"error: {e}"}], "is_error": True}


def _number(description: str) -> JSON:
    return {"type": "number", "description": description}


def build_server(tools: VideoTools) -> McpSdkServerConfig:
    """The in-process MCP server `video` serving `tools`."""

    def wrap(fn):
        async def handler(args: JSON) -> dict[str, Any]:
            try:
                # Tools block on ffmpeg and Gemini; keep the event loop free for streaming.
                return await asyncio.to_thread(fn, args)
            except ToolError as e:
                return _error(e)

        return handler

    def frames(args: JSON) -> dict[str, Any]:
        data, images = tools.get_frames(args["timestamps"])
        blocks = [
            {"type": "image", "data": base64.b64encode(img).decode(), "mimeType": "image/jpeg"}
            for img in images
        ]
        return {"content": [*_text(data)["content"], *blocks]}

    obj = lambda required=(), **props: {  # noqa: E731
        "type": "object", "properties": props, "required": list(required),
    }  # fmt: skip
    start, end = _number("range start in seconds"), _number("range end in seconds")
    defs = [
        tool(
            "get_overview",
            "The video's duration, summary, recurring subjects and chapters. Call it first.",
            obj(),
        )(wrap(lambda a: _text(tools.get_overview()))),
        tool(
            "search",
            "Find transcript segments that mention the query words. Returns the best k hits.",
            obj(["query"], query={"type": "string"}, k={"type": "integer", "minimum": 1}),
        )(wrap(lambda a: _text(tools.search(a["query"], a.get("k", DEFAULT_K))))),
        tool(
            "get_transcript",
            "The transcript segments between start and end seconds.",
            obj(start=start, end=end),
        )(wrap(lambda a: _text(tools.get_transcript(a.get("start"), a.get("end"))))),
        tool(
            "inspect",
            "Ask a vision model a question about what is seen or heard between start and end "
            "seconds. Keep the range tight; calls are limited.",
            obj(["question"], question={"type": "string"}, start=start, end=end,
                fps=_number("frames per second to sample (optional)")),
        )(wrap(lambda a: _text(tools.inspect(a["question"], a.get("start"), a.get("end"), a.get("fps"))))),  # noqa: E501
        tool(
            "get_frames",
            f"JPEG frames at the given times in seconds (at most {MAX_FRAMES}).",
            obj(["timestamps"], timestamps={"type": "array", "items": {"type": "number"}}),
        )(wrap(frames)),
    ]  # fmt: skip
    return create_sdk_mcp_server(name=SERVER_NAME, tools=defs)


TOOL_NAMES = ["get_overview", "search", "get_transcript", "inspect", "get_frames"]
ALLOWED_TOOLS = [f"mcp__{SERVER_NAME}__{name}" for name in TOOL_NAMES]
