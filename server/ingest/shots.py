"""Ingest step `shots`: find the cuts with PySceneDetect and write `shots.json`.

`ContentDetector` finds the cuts. Shots shorter than `MIN_SHOT_S` are merged into
their shorter neighbour, then shots longer than `MAX_SHOT_S` are split into equal
parts, so every shot is a usable unit for captioning (step 7).

`shots.json`:

    {
      "detector": {"name": "ContentDetector", "threshold": 27.0},
      "fps": 25.0, "duration_s": 20.0,
      "shots": [{"index": 0, "start_s": 0.0, "end_s": 4.0}, ...]
    }

The shots cover 0 to `duration_s` with no gaps or overlaps.
"""

from __future__ import annotations

from scenedetect import ContentDetector, detect

from server.ingest import Step, StepContext
from server.ingest.probe import META

SHOTS = "shots.json"
MIN_SHOT_S = 1.0
MAX_SHOT_S = 30.0
THRESHOLD = 27.0  # ContentDetector's default


def detect_cuts(path: str) -> list[float]:
    """Cut times in seconds (the start of every scene after the first)."""
    scenes = detect(path, ContentDetector(threshold=THRESHOLD))
    return [start.seconds for start, _ in scenes[1:]]


def build_shots(cuts: list[float], duration: float) -> list[tuple[float, float]]:
    """Turn cut times into shots covering 0..duration, merging short and splitting long ones."""
    bounds = [0.0, *sorted(c for c in set(cuts) if 0 < c < duration), duration]
    # Merge: drop the boundary between a short shot and its shorter neighbour.
    while len(bounds) > 2:
        lengths = [b - a for a, b in zip(bounds, bounds[1:], strict=False)]
        i = min(range(len(lengths)), key=lengths.__getitem__)
        if lengths[i] >= MIN_SHOT_S:
            break
        if i == 0:
            drop = 1
        elif i == len(lengths) - 1:
            drop = i
        else:
            drop = i if lengths[i - 1] <= lengths[i + 1] else i + 1
        bounds = bounds[:drop] + bounds[drop + 1 :]

    shots = []
    for start, end in zip(bounds, bounds[1:], strict=False):
        parts = max(1, -int(-(end - start) // MAX_SHOT_S))  # ceil
        step = (end - start) / parts
        edges = [start + step * k for k in range(parts)] + [end]
        shots += [(round(a, 3), round(b, 3)) for a, b in zip(edges, edges[1:], strict=False)]
    return shots


def run(ctx: StepContext) -> str:
    meta = ctx.workspace.read_json(META)
    duration = meta["duration_s"]
    cuts = detect_cuts(str(ctx.workspace.source))
    shots = build_shots(cuts, duration)
    ctx.workspace.write_json(
        SHOTS,
        {
            "detector": {"name": "ContentDetector", "threshold": THRESHOLD},
            "fps": meta["fps"],
            "duration_s": duration,
            "shots": [{"index": i, "start_s": a, "end_s": b} for i, (a, b) in enumerate(shots)],
        },
    )
    return f"{len(shots)} shots from {len(cuts)} cuts"


STEP = Step(name="shots", outputs=[SHOTS], run=run, requires=["probe"])
