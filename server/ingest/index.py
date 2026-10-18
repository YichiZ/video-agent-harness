"""Ingest step `index`: a SQLite FTS5 index over the transcript and captions, `index.sqlite`.

One row per transcript segment (`kind` "transcript") and one per shot (`kind`
"caption"). Caption rows index the caption and action, the on-screen text and the
entities as separate columns, so a logo that is shown but never spoken is found
too. Every row carries its time range and shot index. The tokenizer is
`porter unicode61`, so "taxes" finds "taxed".

`search()` ranks rows by BM25: rows with the exact phrase first, then rows with
every query word. Only if neither matches does it fall back to rows with any query
word, so a phrase that is shown but never spoken returns no transcript noise.
"""

from __future__ import annotations

import bisect
import re
import sqlite3
from pathlib import Path

from server.ingest import Step, StepContext
from server.ingest.captions import CAPTIONS
from server.ingest.overview import format_clock
from server.ingest.transcribe import TRANSCRIPT
from server.providers import JSON

INDEX = "index.sqlite"
SEP = " | "

SCHEMA = """
CREATE VIRTUAL TABLE docs USING fts5(
    text, on_screen, entities,
    kind UNINDEXED, start UNINDEXED, end UNINDEXED, shot UNINDEXED, speaker UNINDEXED,
    tokenize = 'porter unicode61'
)"""


def rows(segments: list[JSON], shots: list[JSON]) -> list[tuple]:
    starts = [s["start_s"] for s in shots]

    def shot_at(t: float) -> int | None:
        i = bisect.bisect_right(starts, t) - 1
        return shots[i]["index"] if i >= 0 else None

    transcript = [
        (seg["text"].strip(), "", "", "transcript", seg["start"], seg["end"],
         shot_at(seg["start"]), seg["speaker"])
        for seg in segments
    ]  # fmt: skip
    captions = [
        (f"{s['caption']} {s['action']}".strip(), SEP.join(s["on_screen_text"]),
         SEP.join(s["entities"]), "caption", s["start_s"], s["end_s"], s["index"], None)
        for s in shots
    ]  # fmt: skip
    return transcript + captions


def build(path: Path, segments: list[JSON], shots: list[JSON]) -> int:
    """Write the index to `path` atomically. Returns the number of rows."""
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    data = rows(segments, shots)
    with sqlite3.connect(tmp) as db:
        db.execute(SCHEMA)
        db.executemany("INSERT INTO docs VALUES (?, ?, ?, ?, ?, ?, ?, ?)", data)
    db.close()
    tmp.replace(path)
    return len(data)


def match_queries(query: str) -> list[str]:
    """FTS5 MATCH strings: phrase, all words, any word. Quoting keeps FTS5 syntax out."""
    words = re.findall(r"\w+", query.lower())
    if not words:
        return []
    quoted = [f'"{w}"' for w in words]
    tiers = [f'"{" ".join(words)}"', " ".join(quoted), " OR ".join(quoted)]
    return list(dict.fromkeys(tiers))


def search(path: Path, query: str, k: int) -> list[JSON]:
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        hits: dict[int, JSON] = {}
        for tier, match in enumerate(match_queries(query)):
            if hits and tier == 2:  # the any-word fallback
                break
            found = db.execute(
                "SELECT rowid, -bm25(docs), text, on_screen, entities, kind, start, end, shot,"
                " speaker FROM docs WHERE docs MATCH ? ORDER BY bm25(docs), start LIMIT ?",
                (match, k),
            ).fetchall()
            for rowid, score, text, on_screen, entities, kind, start, end, shot, speaker in found:
                if len(hits) < k and rowid not in hits:
                    hit = {"kind": kind, "start": start, "end": end, "time": format_clock(start),
                           "shot": shot, "score": round(score, 2), "text": text}  # fmt: skip
                    if kind == "transcript":
                        hit["speaker"] = speaker
                    else:
                        hit |= {"on_screen_text": on_screen, "entities": entities}
                    hits[rowid] = hit
        return list(hits.values())
    finally:
        db.close()


def run(ctx: StepContext) -> str:
    segments = ctx.workspace.read_json(TRANSCRIPT)["segments"]
    shots = ctx.workspace.read_json(CAPTIONS)["shots"]
    count = build(ctx.workspace.path(INDEX), segments, shots)
    return f"{count} rows ({len(segments)} segments, {len(shots)} shots)"


STEP = Step(
    name="index",
    outputs=[INDEX],
    run=run,
    requires=["transcribe", "captions"],
    inputs=[TRANSCRIPT, CAPTIONS],  # rebuilt after either is re-run
)
