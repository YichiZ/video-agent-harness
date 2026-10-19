"""Step 8 e2e: `ingest --only index` builds `index.sqlite`, and `search` reads it.

On `medium`: "American Express" is only on screen, so it gives caption hits and no
transcript hit; a spoken phrase from the subtitles finds its segment; and the agent
answers Video-MME's OCR question with D, citing the shot that shows the logo. Offline
mode replays `tests/fixtures/scribe/`, `gemini/t6V9i8fFADI/` and
`agent/t6V9i8fFADI-ocr.json` (the agent runs in process, so its stream has its own
fixture). The `medium` cases need Video-MME (skip in CI); `cuts.mp4` runs everywhere.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from unittest import mock

import pytest

from server.agent.session import ChatSession
from server.config import Settings, load_release
from server.workspace import Workspace, video_id
from tests.e2e.test_step02_transcribe import parse_srt

OCR = (
    "Which company is featured in the video but not mentioned in the audio? Options: "
    "A. Coca-Cola. B. Apple. C. Dairy Queen. D. American Express. Answer with the letter."
)
PHRASE_CUES = (0.25, 0.5, 0.75)  # subtitle cues at these fractions of the video


def search(cli, query: str, k: int = 5) -> list[dict]:
    result = cli("tool", "search", json.dumps({"query": query, "k": k}))
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)["hits"]


def ask_agent(cli, video: Path, question: str, fixture: str) -> tuple[list[str], str]:
    """One turn in process (the CLI chat always replays `<media>-chat.json`)."""
    with mock.patch.dict(os.environ, cli.env(), clear=True):
        settings = Settings.load()
        release = load_release()
    workspace, _ = Workspace.for_video(settings.workspace_dir, video)
    session = ChatSession(workspace, settings, release, fixture=fixture)

    async def turn():
        await session.connect()
        try:
            return [e async for e in session.ask(question)]
        finally:
            await session.disconnect()

    events = asyncio.run(turn())
    return [e.text for e in events if e.kind == "tool"], "".join(
        e.text for e in events if e.kind == "text"
    )


def check_medium_search(cli, medium: Path, srt: str) -> None:
    result = cli("ingest", str(medium), "--only", "index")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "index: done" in result.stdout

    amex = search(cli, "American Express")
    assert amex and {h["kind"] for h in amex} == {"caption"}, amex
    assert "american express" in amex[0]["on_screen_text"].lower()

    cues = [(t, words) for t, words in parse_srt(srt) if len(words) >= 5]
    for at in PHRASE_CUES:
        start, words = cues[int(len(cues) * at)]
        hit = search(cli, " ".join(words), k=1)[0]
        assert hit["kind"] == "transcript", (words, hit)
        assert hit["start"] - 1 <= start <= hit["end"] + 1, (start, words, hit)


def check_ocr_answer(cli, medium: Path) -> None:
    if cli.live:  # the agent's get_overview needs it; offline the tools are replayed
        assert cli("ingest", str(medium), "--only", "overview").returncode == 0
    tools, answer = ask_agent(cli, medium, OCR, "t6V9i8fFADI-ocr")
    assert {"search", "list_shots"} & set(tools), tools
    assert re.search(r"\bD\b", answer) and "American Express" in answer, answer

    captions = json.loads((cli.workspace / video_id(medium) / "captions.json").read_text())
    shown = [
        (s["start_s"], s["end_s"])
        for s in captions["shots"]
        if "american express" in " ".join(s["on_screen_text"]).lower()
    ]
    cited = [int(m) * 60 + int(s) for m, s in re.findall(r"(\d{1,2}):(\d{2})", answer)]
    assert any(lo - 1 <= t <= hi + 1 for t in cited for lo, hi in shown), (answer, shown)


def test_medium_search(cli, videomme, videomme_subtitle):
    medium = videomme("medium")
    check_medium_search(cli, medium, videomme_subtitle("medium"))
    check_ocr_answer(cli, medium)


@pytest.mark.live
def test_medium_search_live(cli, videomme, videomme_subtitle):
    medium = videomme("medium")
    check_medium_search(cli, medium, videomme_subtitle("medium"))
    check_ocr_answer(cli, medium)


def test_cuts_index(cli, test_media):
    cuts = test_media / "cuts.mp4"
    result = cli("ingest", str(cuts), "--only", "index")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "index: done (5 rows (0 segments, 5 shots))" in result.stdout

    hits = search(cli, "clip 3")
    assert [(h["kind"], h["shot"]) for h in hits[:1]] == [("caption", 2)], hits
    assert search(cli, "elephant") == []
    again = cli("ingest", str(cuts), "--only", "index")
    assert "index: skipped" in again.stdout
    rerun = cli("ingest", str(cuts), "--only", "captions", "--force")
    assert "captions: done" in rerun.stdout
    # A newer captions.json makes the index stale, so it is rebuilt.
    assert "index: done" in cli("ingest", str(cuts), "--only", "index").stdout
