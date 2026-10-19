"""Step 13 e2e: `python -m evals.run` writes a scorecard and exits by its gate.

- Offline, on the six smoke questions (replaying `tests/fixtures/agent/eval-smoke/`
  and the Gemini baseline fixtures): the scorecard has accuracy by task type and
  length, citations, cost, latency and the baseline, and the gate passes; with
  `--prompt-version broken` it fails (exit 1).
- `evals/build_dataset.py` is reproducible and matches the committed `dataset.jsonl`.
- Live: the full eval (`make eval`) exits 0 or 1 by its gate, and every question's
  trace is in Langfuse; the broken prompt fails the gate on the smoke questions.

Needs Video-MME, so it skips in CI; the scoring itself is unit-tested.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import Counter

import pytest

from server.config import REPO_ROOT, Settings, load_release

LIVE_TIMEOUT_S = 6 * 3600  # 60 questions, 21 videos (about 7 hours of long video to ingest)


def run_eval(cli, *args: str, timeout: float = 900) -> tuple[subprocess.CompletedProcess, dict]:
    out = cli.tmp / "scorecard.json"
    result = subprocess.run(
        [sys.executable, "-m", "evals.run", *args, "--out", str(out)],
        cwd=REPO_ROOT, env=cli.env(), capture_output=True, text=True, timeout=timeout,
    )  # fmt: skip
    assert out.is_file(), result.stdout + result.stderr
    return result, json.loads(out.read_text())


def check_scorecard(card: dict, n: int) -> None:
    assert card["n"] == n == len(card["questions"])
    assert set(card) >= {
        "release", "prompt_version", "accuracy", "citation_rate", "by_task_type", "by_length",
        "cost_usd", "latency_s", "baseline_accuracy", "gate",
    }  # fmt: skip
    for q in card["questions"]:
        assert q["predicted"] in {"A", "B", "C", "D", None}
        assert set(q["citations"]) == {"count", "inside", "grounded", "valid"}


def test_smoke_eval_and_broken_prompt(cli, videomme):
    videomme("short"), videomme("medium")  # skip without Video-MME
    result, card = run_eval(cli, "--subset", "smoke")
    assert result.returncode == 0, result.stdout + result.stderr
    check_scorecard(card, 6)
    assert card["gate"]["passed"] and card["prompt_version"] == "v2"
    assert card["baseline_accuracy"] is not None
    assert set(card["by_length"]) == {"short", "medium"}

    broken, card = run_eval(cli, "--subset", "smoke", "--prompt-version", "broken", "--no-baseline")
    assert broken.returncode == 1, broken.stdout + broken.stderr
    assert not card["gate"]["passed"] and card["citation_rate"] < 0.9


def test_dataset_is_reproducible(videomme):
    videomme("short")
    from evals.build_dataset import OUT, PER_LENGTH, build

    settings, release = Settings.load(), load_release()
    first = build(settings.video_mme_dir, release.max_video_seconds)
    assert first == build(settings.video_mme_dir, release.max_video_seconds)
    assert [json.loads(line) for line in OUT.read_text().splitlines()] == first
    assert Counter(q["duration"] for q in first) == dict.fromkeys(
        ("short", "medium", "long"), PER_LENGTH
    )
    assert len({q["task_type"] for q in first}) >= 6


def langfuse_roots(trace_ids: list[str]) -> dict[str, list[str]]:
    from langfuse import Langfuse

    s = Settings.load()
    lf = Langfuse(
        public_key=s.langfuse_public_key, secret_key=s.langfuse_secret_key,
        base_url=s.langfuse_base_url,
    )  # fmt: skip
    deadline = time.monotonic() + 120  # Langfuse ingests asynchronously
    while True:
        roots = {
            tid: [o.name for o in lf.api.observations.get_many(trace_id=tid).data]
            for tid in trace_ids
        }
        if all("chat-turn" in names for names in roots.values()) or time.monotonic() > deadline:
            return roots
        time.sleep(5)


@pytest.mark.live
def test_full_eval_live(cli, videomme):
    videomme("long")
    result, card = run_eval(cli, timeout=LIVE_TIMEOUT_S)
    assert result.returncode == (0 if card["gate"]["passed"] else 1), result.stdout
    check_scorecard(card, 60)
    trace_ids = [q["trace_id"] for q in card["questions"]]
    assert all(trace_ids) and len(set(trace_ids)) == 60
    roots = langfuse_roots(trace_ids)
    assert all("chat-turn" in names for names in roots.values()), roots


@pytest.mark.live
def test_broken_prompt_live(cli, videomme):
    videomme("short")
    result, card = run_eval(cli, "--subset", "smoke", "--prompt-version", "broken", "--no-baseline")
    assert result.returncode == 1 and not card["gate"]["passed"], result.stdout
