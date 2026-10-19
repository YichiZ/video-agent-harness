"""The eval (plan step 13): `make eval` (all of `dataset.jsonl`) or `make eval SUBSET=smoke`.

1. Ingest each video once (cached in its workspace), a few in parallel.
2. Ask each question in a fresh agent session, which must end with `Answer: <letter>`.
3. Score accuracy (overall, by task type and by length), citations
   (`evals.scoring`), cost and latency, and ask the same question straight to
   the Gemini model with the whole video and no agent, as a baseline.
   If any video fails to ingest, the eval stops (exit 3) before asking anything.
4. Write the scorecard to `evals/results/<release hash>-<prompt version>-<subset>.json`
   and exit 1 if the gate fails: accuracy below `ACCURACY_MIN` or fewer than
   `CITATION_MIN` of answers citing valid timestamps.

`--prompt-version broken` (`server/agent/prompt.broken.md`) checks that the gate fails.
Each turn is a Langfuse trace when tracing is on (`server.tracing`); the scorecard
keeps each question's trace id.

Offline (`HARNESS_FAKE_PROVIDERS=1`) sessions replay
`tests/fixtures/agent/eval-<subset>/<prompt version>-<question id>.json` and the
baseline replays `gemini/<video>/baseline-*.json`; a live run with
`HARNESS_RECORD_FIXTURES=1` records them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evals import scoring
from evals.smoke import load_questions, question_text
from server import ingest
from server.agent.session import ChatSession
from server.config import Release, Settings, load_release
from server.ingest.captions import CAPTIONS
from server.ingest.overview import ensure_active
from server.ingest.transcribe import TRANSCRIPT
from server.providers import get_vision_model
from server.tracing import release_hash
from server.workspace import Workspace

HERE = Path(__file__).resolve().parent
SUBSETS = {"full": HERE / "dataset.jsonl", "smoke": HERE / "smoke.jsonl"}
RESULTS = HERE / "results"
# ponytail: provisional thresholds from PLAN.md; calibrate after the first full run.
ACCURACY_MIN = 0.70
CITATION_MIN = 0.90
SESSIONS_IN_PARALLEL = 4
INGESTS_IN_PARALLEL = 3
BASELINE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string", "enum": ["A", "B", "C", "D"]}},
    "required": ["answer"],
}


def video_path(settings: Settings, video_id: str) -> Path:
    return settings.video_mme_dir / "videos" / "data" / f"{video_id}.mp4"


def ingest_all(
    settings: Settings, release: Release, video_ids: set[str]
) -> tuple[dict[str, Workspace], dict[str, str]]:
    """Ingest every video. Returns the workspaces and, per failed video, what failed."""

    def one(video_id: str) -> tuple[str, Workspace, str | None]:
        workspace, _ = Workspace.for_video(settings.workspace_dir, video_path(settings, video_id))
        ctx = ingest.StepContext(workspace=workspace, settings=settings, release=release)
        try:
            failed = ingest.failures(ingest.run_steps(ctx))
        except Exception as e:  # Rejected, say
            failed_text = f"{type(e).__name__}: {e}"
        else:
            failed_text = "; ".join(f"{r.name} {r.state}: {r.summary}" for r in failed) or None
        print(f"  ingest {video_id}: {failed_text or 'done'}")
        return video_id, workspace, failed_text

    with ThreadPoolExecutor(INGESTS_IN_PARALLEL) as pool:
        results = list(pool.map(one, sorted(video_ids)))
    return {v: w for v, w, _ in results}, {v: f for v, _, f in results if f}


async def ask(workspace: Workspace, settings: Settings, release: Release, subset: str, q: dict):
    fixture = f"eval-{subset}/{release.prompt_version}-{q['question_id']}"
    session = ChatSession(workspace, settings, release, fixture=fixture)
    started = time.monotonic()
    text, result = [], None
    await session.connect()
    try:
        async for event in session.ask(question_text(q)):
            if event.kind == "text":
                text.append(event.text)
            elif event.kind == "result":
                result = event.result
    finally:
        await session.disconnect()
        session.tracer.flush()
    return {
        "text": "".join(text),
        "cost_usd": result.total_cost_usd if result else None,
        "latency_s": round(time.monotonic() - started, 1),
        "trace_id": session.last_trace_id,
    }


def baseline(workspace: Workspace, settings: Settings, release: Release, q: dict) -> str | None:
    try:
        vision = get_vision_model(settings, release)
        file, _ = ensure_active(vision, workspace)
        raw = vision.generate_json(
            file=file, prompt=question_text(q), schema=BASELINE_SCHEMA, purpose="baseline"
        )
        return raw.get("answer")
    except Exception as e:  # a baseline failure must not sink the eval
        print(f"  baseline {q['question_id']}: {type(e).__name__}: {e}")
        return None


def score_one(workspace: Workspace, q: dict, reply: dict) -> dict:
    meta = workspace.read_json("meta.json")
    transcript = workspace.read_json(TRANSCRIPT) if workspace.path(TRANSCRIPT).is_file() else {}
    shots = workspace.read_json(CAPTIONS)["shots"] if workspace.path(CAPTIONS).is_file() else []
    cites = scoring.check_citations(reply["text"], meta["duration_s"], transcript, shots)
    letter = scoring.parse_answer(reply["text"])
    return {
        **{k: q[k] for k in ("question_id", "videoID", "duration", "task_type", "answer")},
        "predicted": letter,
        "correct": letter == q["answer"],
        "citations": cites,
        "citations_valid": cites["valid"],
        "cost_usd": reply["cost_usd"],
        "latency_s": reply["latency_s"],
        "trace_id": reply["trace_id"],
        "reply": reply["text"],
    }


async def ask_all(workspaces, settings, release, subset, questions) -> list[dict]:
    gate = asyncio.Semaphore(SESSIONS_IN_PARALLEL)

    async def one(q: dict) -> dict:
        async with gate:
            try:
                reply = await ask(workspaces[q["videoID"]], settings, release, subset, q)
            except Exception as e:  # counts as a wrong, uncited answer
                print(f"  {q['question_id']}: session failed: {type(e).__name__}: {e}")
                reply = {"text": "", "cost_usd": None, "latency_s": None, "trace_id": None}
            row = score_one(workspaces[q["videoID"]], q, reply)
            print(f"  {q['question_id']}: {row['predicted']} (gold {q['answer']})"
                  f" citations {'ok' if row['citations_valid'] else 'bad'}")  # fmt: skip
            return row

    return await asyncio.gather(*(one(q) for q in questions))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--subset", choices=list(SUBSETS), default="full")
    parser.add_argument("--prompt-version", help="override release.json's prompt_version")
    parser.add_argument("--no-baseline", action="store_true", help="skip the Gemini baseline")
    parser.add_argument("--out", type=Path, help="scorecard path (default: evals/results/)")
    args = parser.parse_args(argv)

    settings, release = Settings.load(), load_release()
    if args.prompt_version:
        release = release.model_copy(update={"prompt_version": args.prompt_version})
    questions = load_questions(SUBSETS[args.subset])
    missing = sorted(
        {q["videoID"] for q in questions if not video_path(settings, q["videoID"]).is_file()}
    )
    if missing:
        print(f"error: Video-MME videos missing under {settings.video_mme_dir}: {missing}")
        return 2

    print(f"eval {args.subset}: {len(questions)} questions, prompt {release.prompt_version}")
    workspaces, failed = ingest_all(settings, release, {q["videoID"] for q in questions})
    if failed:  # scoring an agent on a half-ingested video measures the ingest, not the agent
        print(f"error: ingest failed for {len(failed)} video(s); rerun after fixing (it is cached)")
        for video_id, why in sorted(failed.items()):
            print(f"  {video_id}: {why[:300]}")
        return 3
    rows = asyncio.run(ask_all(workspaces, settings, release, args.subset, questions))
    if not args.no_baseline:
        for row, q in zip(rows, questions, strict=True):
            row["baseline"] = baseline(workspaces[q["videoID"]], settings, release, q)

    version = release_hash()
    card = {
        "release": version,
        "prompt_version": release.prompt_version,
        "subset": args.subset,
        "models": {"agent": release.agent_model, "vision": release.gemini_model},
        **scoring.summarize(rows, ACCURACY_MIN, CITATION_MIN),
        "questions": rows,
    }
    out = args.out or RESULTS / f"{version}-{release.prompt_version}-{args.subset}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(card, indent=2, ensure_ascii=False) + "\n")
    gate = card["gate"]
    print(
        f"accuracy {card['accuracy']:.0%} (min {gate['accuracy_min']:.0%}), citations "
        f"{card['citation_rate']:.0%} (min {gate['citation_min']:.0%}), baseline "
        f"{card['baseline_accuracy']}, cost {card['cost_usd']}; scorecard {out}"
    )
    print("PASS" if gate["passed"] else "FAIL: below the gate")
    return 0 if gate["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
