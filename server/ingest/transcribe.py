"""Ingest step `transcribe`: speech to text with Scribe, written to `transcript.json`.

The raw Scribe response is turned into word and segment lists. A segment is a
sentence-like run of words that ends on sentence punctuation, a speaker change,
or a pause longer than `PAUSE_S`. Audio events ("(laughter)") are kept as words
of type `audio_event` and appear in segment text. A video with no audio stream
gets an empty transcript without calling the provider.

`transcript.json`:

    {
      "language": "en", "language_probability": 0.98, "model": "scribe_v2",
      "speakers": ["speaker_0", "speaker_1"],
      "words": [{"text": "Researchers", "start": 1.43, "end": 1.9,
                 "type": "word", "speaker": "speaker_0"}, ...],
      "segments": [{"id": 0, "start": 1.43, "end": 9.82, "speaker": "speaker_0",
                    "text": "Researchers have found ... to a host cell.",
                    "first_word": 0, "last_word": 17}, ...]
    }
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from server.ingest import Step, StepContext
from server.ingest.probe import META, format_clock
from server.providers import get_transcriber

TRANSCRIPT = "transcript.json"
PAUSE_S = 0.8
SENTENCE_END = (".", "?", "!", "…")
# Scribe may report ISO 639-3 codes; the rest of the harness uses ISO 639-1.
# ponytail: covers the common languages only, others pass through unchanged.
ISO_639_1 = {
    "eng": "en", "spa": "es", "fra": "fr", "deu": "de", "ita": "it", "por": "pt",
    "nld": "nl", "rus": "ru", "zho": "zh", "cmn": "zh", "jpn": "ja", "kor": "ko",
    "ara": "ar", "hin": "hi", "tur": "tr", "pol": "pl", "ukr": "uk", "swe": "sv",
}  # fmt: skip


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Word(_Model):
    text: str
    start: float
    end: float
    type: Literal["word", "audio_event"]
    speaker: str | None


class Segment(_Model):
    id: int
    start: float
    end: float
    speaker: str | None
    text: str
    first_word: int
    last_word: int


class Transcript(_Model):
    language: str | None
    language_probability: float | None
    model: str
    speakers: list[str]
    words: list[Word]
    segments: list[Segment]


def parse_words(raw: list[dict[str, Any]]) -> list[Word]:
    """Scribe words to `Word`s: spacing dropped, untimed words given their neighbour's time."""
    words: list[Word] = []
    last = 0.0
    for w in raw:
        if w.get("type") not in ("word", "audio_event") or not w.get("text", "").strip():
            continue
        start = w.get("start")
        start = last if start is None else float(start)
        end = w.get("end")
        end = start if end is None else max(float(end), start)
        words.append(
            Word(
                text=w["text"].strip(),
                start=round(start, 3),
                end=round(end, 3),
                type=w["type"],
                speaker=w.get("speaker_id"),
            )
        )
        last = end
    return sorted(words, key=lambda w: w.start)  # stable: ties keep Scribe's order


def build_segments(words: list[Word], pause_s: float = PAUSE_S) -> list[Segment]:
    segments: list[Segment] = []
    first = 0
    for i, word in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else None
        if (
            nxt is None
            or word.text.endswith(SENTENCE_END)
            or nxt.speaker != word.speaker
            or nxt.start - word.end > pause_s
        ):
            run = words[first : i + 1]
            segments.append(
                Segment(
                    id=len(segments),
                    start=run[0].start,
                    end=max(w.end for w in run),
                    speaker=run[0].speaker,
                    text=" ".join(w.text for w in run),
                    first_word=first,
                    last_word=i,
                )
            )
            first = i + 1
    return segments


def build_transcript(raw: dict[str, Any], model: str) -> Transcript:
    words = parse_words(raw.get("words") or [])
    language = raw.get("language_code")
    return Transcript(
        language=ISO_639_1.get(language, language) if language else None,
        language_probability=raw.get("language_probability"),
        model=model,
        speakers=sorted({w.speaker for w in words if w.speaker}),
        words=words,
        segments=build_segments(words),
    )


def run(ctx: StepContext) -> str:
    model = ctx.release.stt_model
    if ctx.workspace.read_json(META)["audio"] is None:
        transcript = Transcript(
            language=None, language_probability=None, model=model,
            speakers=[], words=[], segments=[],
        )  # fmt: skip
        ctx.workspace.write_json(TRANSCRIPT, transcript.model_dump())
        return "no audio stream, empty transcript"

    raw = get_transcriber(ctx.settings, ctx.release).transcribe(ctx.workspace.source)
    transcript = build_transcript(raw, model)
    ctx.workspace.write_json(TRANSCRIPT, transcript.model_dump())
    spoken = sum(w.type == "word" for w in transcript.words)
    end = transcript.words[-1].end if transcript.words else 0.0
    return (
        f"{transcript.language or 'unknown language'}, {spoken} words, "
        f"{len(transcript.segments)} segments, {len(transcript.speakers)} speaker(s), "
        f"speech to {format_clock(end)}"
    )


STEP = Step(name="transcribe", outputs=[TRANSCRIPT], run=run, requires=["probe"])
