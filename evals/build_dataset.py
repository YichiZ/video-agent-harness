"""Sample Video-MME into `evals/dataset.jsonl` (plan step 13).

    uv run python -m evals.build_dataset

20 questions each from short, medium and long videos, all from videos that are
on this machine (`VIDEO_MME_DIR`) and have English subtitles (Video-MME's are
English, so this also keeps the set English), with long videos capped at
`max_video_minutes`. Videos are drawn in a fixed shuffled order (seed `SEED`)
and all of a video's questions are taken, so the set is the same on every run,
spans Video-MME's task types, and needs few ingests (about 7 videos a length).
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
import zipfile
from collections import Counter
from pathlib import Path

from server.config import Settings, load_release

SEED = 13
PER_LENGTH = 20
LENGTHS = ("short", "medium", "long")
OUT = Path(__file__).resolve().parent / "dataset.jsonl"
FIELDS = ("question_id", "videoID", "duration", "task_type", "question", "options", "answer")


def duration_s(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(video)],
        capture_output=True, text=True, check=True, timeout=60,
    )  # fmt: skip
    return float(out.stdout.strip())


def subtitled(root: Path) -> set[str]:
    folder = root / "subtitle"
    names = [p.name for p in folder.glob("*.srt")] if folder.is_dir() else []
    if (root / "subtitle.zip").is_file():
        names += zipfile.ZipFile(root / "subtitle.zip").namelist()
    return {Path(n).stem for n in names if n.endswith(".srt")}


def build(root: Path, cap_s: float) -> list[dict]:
    questions = json.loads((root / "annotations_all.json").read_text())
    videos = root / "videos" / "data"
    have_subs = subtitled(root)
    by_video: dict[tuple[str, str], list[dict]] = {}
    for q in questions:
        if (videos / f"{q['videoID']}.mp4").is_file() and q["videoID"] in have_subs:
            by_video.setdefault((q["duration"], q["videoID"]), []).append(q)

    rng = random.Random(SEED)
    picked = []
    for length in LENGTHS:
        keys = sorted(k for k in by_video if k[0] == length)
        rng.shuffle(keys)
        taken: list[dict] = []
        for key in keys:
            if len(taken) >= PER_LENGTH:
                break
            if length == "long" and duration_s(videos / f"{key[1]}.mp4") > cap_s:
                continue
            taken += sorted(by_video[key], key=lambda q: q["question_id"])
        if len(taken) < PER_LENGTH:
            raise SystemExit(f"only {len(taken)} {length} questions available")
        picked += [{f: q[f] for f in FIELDS} for q in taken[:PER_LENGTH]]
    return picked


def main() -> int:
    settings, release = Settings.load(), load_release()
    picked = build(settings.video_mme_dir, release.max_video_seconds)
    OUT.write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in picked))
    videos = len({q["videoID"] for q in picked})
    print(f"wrote {len(picked)} questions from {videos} videos to {OUT.name}")
    print(dict(Counter(q["task_type"] for q in picked)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
