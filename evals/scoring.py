"""Scoring for `evals.run`: answer letters, citation checks and the scorecard summary.

A citation is a `[mm:ss]` or `[mm:ss–mm:ss]` in the answer. An answer's citations
are valid when it has at least one, every one lies inside the video, and at least
one is grounded: the transcript and captions within `WINDOW_S` of it share a
content word with the answer.

ponytail: grounding is a shared-word heuristic; an LLM judge per citation would be
stricter if the gate needs it.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from evals.smoke import parse_answer

__all__ = ["citations", "content_words", "grounded", "parse_answer", "summarize"]

CLOCK = r"(\d{1,2}):(\d{2})(?::(\d{2}))?"
CITATION = re.compile(rf"\[{CLOCK}(?:\s*[–—-]\s*{CLOCK})?\]")
WINDOW_S = 10.0
_STOP = (
    "about above after again also answer because been before being between both cannot  "
    "could does doing down during each from further have having here into itself just  "
    "more most only option other over same shown shows should some such than that their  "
    "them then there these they this those through under until very video what when  "
    "where which while whom with would your frame frames scene clip"
)
STOPWORDS = frozenset(_STOP.split())


def _seconds(h_or_m: str, m_or_s: str, s: str | None) -> float:
    if s is None:
        return int(h_or_m) * 60 + int(m_or_s)
    return int(h_or_m) * 3600 + int(m_or_s) * 60 + int(s)


def citations(text: str) -> list[tuple[float, float]]:
    """Each citation as (start, end) seconds; a single time has start == end."""
    out = []
    for m in CITATION.finditer(text):
        start = _seconds(*m.group(1, 2, 3))
        end = _seconds(*m.group(4, 5, 6)) if m.group(4) else start
        out.append((start, max(start, end)))
    return out


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z'-]{3,}", text.lower())
    return {w.strip("'-") for w in words} - STOPWORDS


def evidence_text(transcript: dict, shots: list[dict], start: float, end: float) -> str:
    lo, hi = start - WINDOW_S, end + WINDOW_S
    said = [
        s["text"] for s in transcript.get("segments", []) if s["end"] >= lo and s["start"] <= hi
    ]
    shown = [
        " ".join([s.get("caption", ""), s.get("action", ""), *s.get("on_screen_text", []),
                  *s.get("entities", [])])
        for s in shots
        if s["end_s"] >= lo and s["start_s"] <= hi
    ]  # fmt: skip
    return " ".join(said + shown)


def grounded(answer: str, transcript: dict, shots: list[dict], cite: tuple[float, float]) -> bool:
    words = content_words(CITATION.sub(" ", answer))
    return bool(words & content_words(evidence_text(transcript, shots, *cite)))


def check_citations(answer: str, duration: float, transcript: dict, shots: list[dict]) -> dict:
    cites = citations(answer)
    inside = [c for c in cites if c[0] >= 0 and c[1] <= duration + 1]
    grounded_count = sum(grounded(answer, transcript, shots, c) for c in inside)
    return {
        "count": len(cites),
        "inside": len(inside),
        "grounded": grounded_count,
        "valid": bool(cites) and len(inside) == len(cites) and grounded_count > 0,
    }


def _rate(rows: list[dict], key: str) -> float:
    return round(sum(bool(r[key]) for r in rows) / len(rows), 4) if rows else 0.0


def _group(rows: Iterable[dict], field: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r[field]].append(r)
    return {k: {"n": len(v), "accuracy": _rate(v, "correct")} for k, v in sorted(groups.items())}


def summarize(rows: list[dict], accuracy_min: float, citation_min: float) -> dict:
    costs = [r["cost_usd"] for r in rows if r.get("cost_usd") is not None]
    latencies = [r["latency_s"] for r in rows if r.get("latency_s") is not None]
    baseline = [r for r in rows if r.get("baseline") is not None]
    accuracy, cited = _rate(rows, "correct"), _rate(rows, "citations_valid")
    return {
        "n": len(rows),
        "accuracy": accuracy,
        "citation_rate": cited,
        "by_task_type": _group(rows, "task_type"),
        "by_length": _group(rows, "duration"),
        "cost_usd": {"total": round(sum(costs), 4), "mean": round(sum(costs) / len(costs), 4)}
        if costs
        else None,
        "latency_s": {"mean": round(sum(latencies) / len(latencies), 1)} if latencies else None,
        "baseline_accuracy": round(
            sum(r["baseline"] == r["answer"] for r in baseline) / len(baseline), 4
        )
        if baseline
        else None,
        "gate": {
            "accuracy_min": accuracy_min,
            "citation_min": citation_min,
            "passed": accuracy >= accuracy_min and cited >= citation_min,
        },
    }
