"""Ingest step `captions`: caption every shot with Gemini and write `captions.json`.

One schema-constrained call gets the shot list (from `shots.json`) and the
transcript segments, and returns a caption, the on-screen text, the visible
entities and the action for each shot. Videos longer than `WINDOW_S` are split
into equal windows on shot boundaries; each window is one call restricted to its
range with `start` and `end`, and the results are merged. Gemini answers by shot
index, so merging never depends on the times it reports.

`captions.json`:

    {
      "model": "gemini-3.8-flash",
      "windows": [{"start_s": 0.0, "end_s": 367.0}],
      "shots": [{"index": 0, "start_s": 0.0, "end_s": 4.2, "time": "00:00",
                 "caption": "...", "on_screen_text": ["American Express"],
                 "entities": ["credit card"], "action": "..."}, ...]
    }

A shot Gemini skips keeps an empty caption, and the step's summary counts it.
"""

from __future__ import annotations

from pydantic import BaseModel

from server.ingest import Step, StepContext
from server.ingest.overview import ensure_active, format_clock
from server.ingest.shots import SHOTS
from server.ingest.transcribe import TRANSCRIPT
from server.providers import JSON, get_vision_model

CAPTIONS = "captions.json"
WINDOW_S = 20 * 60

PROMPT = """\
You are indexing a video so an assistant can answer questions about it later.
The video is cut into the shots listed below. This request covers {start} to {end};
all times are mm:ss from the start of the whole video. For every listed shot return:
- index: the shot's number from the list.
- caption: one or two sentences on what the shot shows.
- on_screen_text: every piece of text visible in the shot (titles, subtitles, logos,
  signs, charts, cards), copied exactly. Empty if there is none.
- entities: the people, objects, brands, organisations and places visible.
- action: what happens in the shot, in a few words.
Return exactly one entry per listed shot. Describe what is seen; use the transcript
only to tell who or what is on screen.

Shots:
{shots}

Transcript:
{transcript}"""

SCHEMA: JSON = {
    "type": "object",
    "properties": {
        "shots": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "caption": {"type": "string"},
                    "on_screen_text": {"type": "array", "items": {"type": "string"}},
                    "entities": {"type": "array", "items": {"type": "string"}},
                    "action": {"type": "string"},
                },
                "required": ["index", "caption", "on_screen_text", "entities", "action"],
            },
        }
    },
    "required": ["shots"],
}


class ShotCaption(BaseModel):
    index: int
    caption: str
    on_screen_text: list[str]
    entities: list[str]
    action: str


class Response(BaseModel):
    shots: list[ShotCaption]


def windows(shots: list[JSON], duration: float) -> list[list[JSON]]:
    """Split shots into equal-length windows of at most `WINDOW_S`, by each shot's start."""
    n = max(1, -int(-duration // WINDOW_S))  # ceil
    size = duration / n
    groups: list[list[JSON]] = [[] for _ in range(n)]
    for shot in shots:
        groups[min(n - 1, int(shot["start_s"] // size))].append(shot)
    return [g for g in groups if g]


def build_prompt(shots: list[JSON], segments: list[JSON]) -> str:
    lo, hi = shots[0]["start_s"], shots[-1]["end_s"]
    shot_lines = [
        f"{s['index']}: {format_clock(s['start_s'])}-{format_clock(s['end_s'])}" for s in shots
    ]
    lines = [
        f"[{format_clock(seg['start'])}] {seg['speaker'] or 'speaker'}: {seg['text'].strip()}"
        for seg in segments
        if seg["start"] < hi and seg["end"] > lo
    ]
    return PROMPT.format(
        start=format_clock(lo),
        end=format_clock(hi),
        shots="\n".join(shot_lines),
        transcript="\n".join(lines) or "(no speech)",
    )


def merge(shots: list[JSON], captions: list[ShotCaption]) -> list[JSON]:
    """One entry per shot, in order; shots without a caption get empty fields."""
    by_index = {c.index: c for c in captions}
    empty = ShotCaption(index=-1, caption="", on_screen_text=[], entities=[], action="")
    return [
        {
            "index": s["index"],
            "start_s": s["start_s"],
            "end_s": s["end_s"],
            "time": format_clock(s["start_s"]),
            **by_index.get(s["index"], empty).model_dump(exclude={"index"}),
        }
        for s in shots
    ]


def run(ctx: StepContext) -> str:
    shots_data = ctx.workspace.read_json(SHOTS)
    shots, duration = shots_data["shots"], shots_data["duration_s"]
    segments = ctx.workspace.read_json(TRANSCRIPT)["segments"]
    vision = get_vision_model(ctx.settings, ctx.release)
    file, _ = ensure_active(vision, ctx.workspace)

    groups = windows(shots, duration)
    windowed = len(groups) > 1
    captions: list[ShotCaption] = []
    for group in groups:
        wanted = {s["index"] for s in group}
        raw = vision.generate_json(
            file=file,
            prompt=build_prompt(group, segments),
            schema=SCHEMA,
            purpose="captions",
            start=group[0]["start_s"] if windowed else None,
            end=group[-1]["end_s"] if windowed else None,
        )
        captions += [c for c in Response.model_validate(raw).shots if c.index in wanted]

    merged = merge(shots, captions)
    ctx.workspace.write_json(
        CAPTIONS,
        {
            "model": ctx.release.gemini_model,
            "windows": [{"start_s": g[0]["start_s"], "end_s": g[-1]["end_s"]} for g in groups],
            "shots": merged,
        },
    )
    missing = sum(not s["caption"] for s in merged)
    calls = f"{len(groups)} call{'s' if windowed else ''}"
    note = f", {missing} without a caption" if missing else ""
    return f"{len(merged)} shots in {calls}{note}"


STEP = Step(
    name="captions",
    outputs=[CAPTIONS],
    run=run,
    requires=["probe", "transcribe", "shots"],
)
