"""Step 2 e2e: `server.cli ingest <video> --only transcribe` writes `transcript.json`.

On Video-MME `short` the transcript is checked against the video's subtitles.
Offline mode replays `tests/fixtures/scribe/TbaCxIJ_VP4.json`; live mode calls
Scribe (and with `--record` rewrites that fixture). The generated-media cases
run everywhere, including CI, with a hand-written Scribe response.
"""

from __future__ import annotations

import difflib
import html
import json
import re
import subprocess
from pathlib import Path

import pytest

from server.config import REPO_ROOT
from server.ingest.transcribe import Transcript
from server.workspace import video_id

SHORT_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "scribe" / "TbaCxIJ_VP4.json"


def transcript_for(cli, video: Path) -> Transcript:
    path = cli.workspace / video_id(video) / "transcript.json"
    return Transcript.model_validate(json.loads(path.read_text()))


def norm(text: str) -> list[str]:
    return re.sub(r"[^a-z0-9' ]+", " ", text.lower().replace("-", "")).split()


def parse_srt(srt: str) -> list[tuple[float, list[str]]]:
    """[(cue start in seconds, normalized words)], with tags and "[Narrator]" labels removed."""
    cues = []
    for block in re.split(r"\n\s*\n", srt.strip()):
        lines = block.strip().splitlines()
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+) -->", lines[1] if len(lines) > 1 else "")
        if not m:
            continue
        h, mi, s, ms = map(int, m.groups())
        text = html.unescape(re.sub(r"<[^>]+>|\[[^\]]*\]", " ", " ".join(lines[2:])))
        if words := norm(text):
            cues.append((h * 3600 + mi * 60 + s + ms / 1000, words))
    return cues


def wer(ref: list[str], hyp: list[str]) -> float:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def check_short(cli, short: Path, srt: str) -> None:
    result = cli("ingest", str(short), "--only", "transcribe")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "probe: done" in result.stdout and "transcribe: done" in result.stdout

    t = transcript_for(cli, short)
    starts = [w.start for w in t.words]
    assert starts == sorted(starts), "word times must only increase"
    assert all(w.end >= w.start for w in t.words)
    assert t.language == "en"

    # Word error rate against the subtitle text.
    cues = parse_srt(srt)
    ref = [w for _, words in cues for w in words]
    spoken = [w for w in t.words if w.type == "word"]
    hyp_tokens = [norm(w.text) for w in spoken]
    hyp, owner = [], []  # flat hypothesis tokens and the transcript word each came from
    for i, toks in enumerate(hyp_tokens):
        hyp += toks
        owner += [i] * len(toks)
    assert wer(ref, hyp) <= 0.10, f"WER {wer(ref, hyp):.1%}"

    # Cue timing: the first word of each cue starts within 0.5 s of the cue.
    matched = {}
    for block in difflib.SequenceMatcher(None, ref, hyp, autojunk=False).get_matching_blocks():
        for k in range(block.size):
            matched[block.a + k] = owner[block.b + k]
    good, pos = 0, 0
    for cue_start, words in cues:
        i = matched.get(pos)
        good += i is not None and abs(spoken[i].start - cue_start) <= 0.5
        pos += len(words)
    assert good / len(cues) >= 0.9, f"{good}/{len(cues)} cues aligned within 0.5 s"

    phrase = "attaching its spikes to a host cell"
    hits = [s for s in t.segments if phrase in " ".join(norm(s.text))]
    assert hits, f"no segment contains {phrase!r}"
    seg = hits[0]
    attaching = next(
        w for w in t.words[seg.first_word : seg.last_word + 1] if norm(w.text) == ["attaching"]
    )
    assert attaching.start >= 4.0 and seg.end <= 10.5, (attaching, seg)

    again = cli("ingest", str(short), "--only", "transcribe")
    assert "transcribe: skipped" in again.stdout


# Video-MME (plan cases)


def test_short_transcript_offline(cli, videomme, videomme_subtitle):
    if not SHORT_FIXTURE.is_file():
        pytest.skip(
            f"no recorded Scribe response at {SHORT_FIXTURE}; `make test-live ARGS=--record`"
        )
    check_short(cli, videomme("short"), videomme_subtitle("short"))


@pytest.mark.live
def test_short_transcript_live(cli, videomme, videomme_subtitle):
    check_short(cli, videomme("short"), videomme_subtitle("short"))


# Generated media (also run in CI)

TONE_RESPONSE = {
    "language_code": "eng",
    "language_probability": 0.9,
    "text": "Hi there. Hello again (applause)",
    "words": [
        {"text": "Hi", "start": 0.1, "end": 0.3, "type": "word", "speaker_id": "speaker_0"},
        {"text": " ", "start": 0.3, "end": 0.35, "type": "spacing", "speaker_id": "speaker_0"},
        {"text": "there.", "start": 0.35, "end": 0.7, "type": "word", "speaker_id": "speaker_0"},
        {"text": "Hello", "start": 0.8, "end": 1.1, "type": "word", "speaker_id": "speaker_1"},
        {"text": "again", "start": 2.2, "end": 2.5, "type": "word", "speaker_id": "speaker_1"},
        {"text": "(applause)", "start": 2.5, "end": 2.9, "type": "audio_event",
         "speaker_id": "speaker_1"},
    ],
}  # fmt: skip


def test_generated_video_with_audio(cli):
    video = cli.tmp / "tone.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(video),
        ],
        check=True,
    )  # fmt: skip
    fixtures = cli.tmp / "fixtures"
    (fixtures / "scribe").mkdir(parents=True)
    (fixtures / "scribe" / "tone.json").write_text(json.dumps(TONE_RESPONSE))
    env = {"HARNESS_FIXTURES_DIR": str(fixtures)}

    result = cli("ingest", str(video), "--only", "transcribe", env=env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "probe: done" in result.stdout
    assert "transcribe: done (en, 4 words, 3 segments, 2 speaker(s)" in result.stdout

    t = transcript_for(cli, video)
    assert [s.text for s in t.segments] == ["Hi there.", "Hello", "again (applause)"]
    assert t.speakers == ["speaker_0", "speaker_1"]

    again = cli("ingest", str(video), "--only", "transcribe", env=env)
    assert "transcribe: skipped" in again.stdout


def test_no_audio_gives_empty_transcript(cli, test_media):
    cuts = test_media / "cuts.mp4"  # no audio stream, and no Scribe fixture exists for it
    result = cli("ingest", str(cuts), "--only", "transcribe")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "transcribe: done (no audio stream" in result.stdout
    t = transcript_for(cli, cuts)
    assert (t.words, t.segments, t.language) == ([], [], None)


def test_missing_fixture_fails_the_step(cli):
    video = cli.tmp / "unrecorded.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
            "-f", "lavfi", "-i", "sine=duration=1", "-shortest", str(video),
        ],
        check=True,
    )  # fmt: skip
    result = cli("ingest", str(video), "--only", "transcribe")
    assert result.returncode == 3
    assert "no recorded response" in result.stderr
    assert not (cli.workspace / video_id(video) / "transcript.json").exists()
