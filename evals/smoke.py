"""Smoke comparison of system prompts (plan step 12): `make eval SUBSET=smoke`.

Asks the six Video-MME questions in `smoke.jsonl` (the short and medium test
videos from `tests/videomme.json`) once per prompt version, each in a fresh
agent session, and scores the `Answer: <letter>` it ends with. Exits 1 if the
candidate scores lower than the baseline.

Offline (`HARNESS_FAKE_PROVIDERS=1`) each session replays
`tests/fixtures/agent/smoke/<version>-<question_id>.json`; a live run with
`HARNESS_RECORD_FIXTURES=1` records them. Videos come from `VIDEO_MME_DIR`.

ponytail: six questions and accuracy only; step 13's `make eval` replaces this
with the full dataset, citation checks, cost and the gate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from server import ingest
from server.agent.session import ChatSession
from server.config import Release, Settings, load_release
from server.workspace import Workspace

QUESTIONS = Path(__file__).resolve().parent / "smoke.jsonl"
ANSWER = re.compile(r"Answer:\W*([A-D])\b")
FORMAT = "End your reply with a final line `Answer: <letter>`."


def load_questions(path: Path = QUESTIONS) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def question_text(q: dict) -> str:
    return "\n".join([q["question"], "Options:", *q["options"], FORMAT])


def parse_answer(text: str) -> str | None:
    """The letter of the last `Answer: X` line, or None."""
    letters = ANSWER.findall(text)
    return letters[-1] if letters else None


async def ask_once(workspace: Workspace, settings: Settings, release: Release, q: dict) -> str:
    fixture = f"smoke/{release.prompt_version}-{q['question_id']}"
    session = ChatSession(workspace, settings, release, fixture=fixture)
    await session.connect()
    try:
        return "".join([e.text async for e in session.ask(question_text(q)) if e.kind == "text"])
    finally:
        await session.disconnect()


def score(settings: Settings, release: Release, questions: list[dict]) -> dict:
    correct = 0
    for q in questions:
        video = settings.video_mme_dir / "videos" / "data" / f"{q['videoID']}.mp4"
        workspace, _ = Workspace.for_video(settings.workspace_dir, video)
        ctx = ingest.StepContext(workspace=workspace, settings=settings, release=release)
        ingest.run_steps(ctx)
        answer = parse_answer(asyncio.run(ask_once(workspace, settings, release, q)))
        correct += answer == q["answer"]
        print(f"  {release.prompt_version} {q['question_id']}: {answer} (gold {q['answer']})")
    return {"correct": correct, "total": len(questions)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", default="v1")
    parser.add_argument("--candidate", default="v2")
    args = parser.parse_args(argv)
    settings, release = Settings.load(), load_release()
    questions = load_questions()
    missing = {
        q["videoID"]
        for q in questions
        if not (settings.video_mme_dir / "videos" / "data" / f"{q['videoID']}.mp4").is_file()
    }
    if missing:
        print(f"error: Video-MME videos missing under {settings.video_mme_dir}: {sorted(missing)}")
        return 1
    scores = {
        version: score(settings, release.model_copy(update={"prompt_version": version}), questions)
        for version in (args.baseline, args.candidate)
    }
    print(json.dumps(scores))
    base, cand = (scores[v]["correct"] for v in (args.baseline, args.candidate))
    if cand < base:
        print(f"FAIL: {args.candidate} scored {cand}, below {args.baseline}'s {base}")
        return 1
    n = len(questions)
    print(f"ok: {args.candidate} {cand}/{n} vs {args.baseline} {base}/{n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
