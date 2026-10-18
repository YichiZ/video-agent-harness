"""Ingest step `overview`: upload the video to Gemini and write `overview.json`.

`ensure_active()` keeps an `ACTIVE` Gemini file for the video, recorded in
`gemini_file.json`. It uploads the video again when there is no stored file or
the stored one is within `REFRESH_MARGIN` of its expiry (Gemini keeps files 48 h).
Step 4's `inspect` tool uses it too.

`overview.json` comes from one schema-constrained call:

    {
      "summary": "An animated explainer of how mRNA vaccines ...",
      "subjects": [{"name": "Spike protein", "kind": "object",
                    "description": "...", "first_seen": "00:05"}],
      "chapters": [{"start": "00:00", "end": "00:21", "title": "...", "summary": "..."}]
    }

Chapter times are clamped to the video's duration from `meta.json`.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from server.ingest import Step, StepContext
from server.providers import RemoteFile, VisionModel, get_vision_model
from server.workspace import Workspace

GEMINI_FILE = "gemini_file.json"
OVERVIEW = "overview.json"
REFRESH_MARGIN = timedelta(hours=1)
POLL_INTERVAL_S = 2.0
UPLOAD_TIMEOUT_S = 600.0

PROMPT = """\
You are indexing a video so an assistant can answer questions about it later.
Watch the whole video, including the audio and any on-screen text, and return:
- summary: 2 to 4 sentences on what the video is about and how it is presented.
- subjects: the recurring people, objects, places and organisations, each with
  a one-line description and the time it first appears.
- chapters: consecutive sections that cover the whole video in order, each
  with a short title and a one-sentence summary.
Write every time as mm:ss from the start of the video. The video is {duration} long,
so no time may be later than {duration}."""

CLOCK = r"^\d{1,2}:\d{2}(:\d{2})?$"


class Subject(BaseModel):
    name: str
    kind: Literal["person", "object", "place", "organization", "other"]
    description: str
    first_seen: str = Field(pattern=CLOCK)


class Chapter(BaseModel):
    start: str = Field(pattern=CLOCK)
    end: str = Field(pattern=CLOCK)
    title: str
    summary: str


class Overview(BaseModel):
    summary: str = Field(min_length=1)
    subjects: list[Subject]
    chapters: list[Chapter] = Field(min_length=1)


def _schema() -> dict:
    """Gemini's JSON schema for `Overview`, with every property required."""
    clock = {"type": "string", "description": "mm:ss"}
    text = {"type": "string"}

    def obj(**props: dict) -> dict:
        return {"type": "object", "properties": props, "required": list(props)}

    subject = obj(
        name=text,
        kind={"type": "string", "enum": ["person", "object", "place", "organization", "other"]},
        description=text,
        first_seen=clock,
    )
    chapter = obj(start=clock, end=clock, title=text, summary=text)
    return obj(
        summary=text,
        subjects={"type": "array", "items": subject},
        chapters={"type": "array", "items": chapter},
    )


SCHEMA = _schema()


def parse_clock(value: str) -> int:
    """'01:23' -> 83, '1:02:03' -> 3723."""
    seconds = 0
    for part in value.split(":"):
        seconds = seconds * 60 + int(part)
    return seconds


def format_clock(seconds: float) -> str:
    """83.9 -> '01:24' (rounded), 3723 -> '1:02:03'."""
    h, rest = divmod(round(seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def clamp_chapters(chapters: list[Chapter], duration_s: float) -> list[Chapter]:
    """Drop chapters that start after the end and clamp the rest to `duration_s`."""
    last = format_clock(duration_s)
    limit = parse_clock(last)
    kept = []
    for c in chapters:
        if parse_clock(c.start) > limit:
            continue
        end = c.end if parse_clock(c.end) <= limit else last
        kept.append(c.model_copy(update={"end": end}))
    return kept


# Gemini file


def _store(workspace: Workspace, file: RemoteFile) -> None:
    workspace.write_json(
        GEMINI_FILE,
        {
            "name": file.name,
            "display_name": file.display_name,
            "uri": file.uri,
            "mime_type": file.mime_type,
            "state": file.state,
            "expires_at": file.expires_at.isoformat(),
            "uploaded_at": datetime.now(UTC).isoformat(),
        },
    )


def stored_file(workspace: Workspace) -> RemoteFile | None:
    if not workspace.path(GEMINI_FILE).exists():
        return None
    data = workspace.read_json(GEMINI_FILE)
    return RemoteFile(
        name=data["name"],
        display_name=data["display_name"],
        uri=data["uri"],
        mime_type=data["mime_type"],
        state=data["state"],
        expires_at=datetime.fromisoformat(data["expires_at"]),
    )


def wait_active(vision: VisionModel, file: RemoteFile) -> RemoteFile:
    deadline = time.monotonic() + UPLOAD_TIMEOUT_S
    while file.state != "ACTIVE":
        if file.state == "FAILED":
            raise RuntimeError(f"Gemini could not process the video ({file.name})")
        if time.monotonic() > deadline:
            raise TimeoutError(f"Gemini file {file.name} not ACTIVE after {UPLOAD_TIMEOUT_S:g} s")
        time.sleep(POLL_INTERVAL_S)
        file = vision.get_file(file.name)
    return file


def ensure_active(
    vision: VisionModel, workspace: Workspace, now: datetime | None = None
) -> tuple[RemoteFile, bool]:
    """Return an ACTIVE Gemini file for the workspace video, and whether it was uploaded now."""
    now = now or datetime.now(UTC)
    stored = stored_file(workspace)
    # ponytail: trusts the stored expiry; check get_file() too if files vanish early.
    if stored and stored.state == "ACTIVE" and stored.expires_at - now > REFRESH_MARGIN:
        return stored, False
    file = wait_active(vision, vision.upload(workspace.source))
    _store(workspace, file)
    return file, True


def run(ctx: StepContext) -> str:
    vision = get_vision_model(ctx.settings, ctx.release)
    file, uploaded = ensure_active(vision, ctx.workspace)
    duration = ctx.workspace.read_json("meta.json")["duration_s"]
    raw = vision.generate_json(
        file=file,
        prompt=PROMPT.format(duration=format_clock(duration)),
        schema=SCHEMA,
        purpose="overview",
    )
    overview = Overview.model_validate(raw)
    overview = overview.model_copy(update={"chapters": clamp_chapters(overview.chapters, duration)})
    ctx.workspace.write_json(OVERVIEW, overview.model_dump())
    how = "uploaded" if uploaded else "reused upload"
    return f"{how}, {len(overview.chapters)} chapters, {len(overview.subjects)} subjects"


STEP = Step(name="overview", outputs=[GEMINI_FILE, OVERVIEW], run=run, requires=["probe"])
