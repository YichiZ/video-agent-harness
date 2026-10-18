from types import SimpleNamespace

from server.config import REPO_ROOT, Settings, load_release
from server.ingest.transcribe import Transcript, build_segments, build_transcript, parse_words
from server.providers.elevenlabs import ScribeTranscriber


def w(text, start, end, speaker="speaker_0", type="word"):
    return {"text": text, "start": start, "end": end, "type": type, "speaker_id": speaker}


def space(t):
    return {"text": " ", "start": t, "end": t, "type": "spacing", "speaker_id": "speaker_0"}


RAW = {
    "language_code": "eng",
    "language_probability": 0.97,
    "words": [
        w("Hello", 0.0, 0.4), space(0.4), w("there.", 0.5, 0.9),  # punctuation ends it
        w("How", 1.0, 1.2), space(1.2), w("are", 1.2, 1.4),
        w("you", 2.5, 2.8),  # 1.1 s pause ends the previous segment
        w("Fine", 2.9, 3.2, speaker="speaker_1"),  # speaker change
        w("(laughter)", 3.3, 4.0, speaker="speaker_1", type="audio_event"),
    ],
}  # fmt: skip


def test_build_transcript_segments_on_punctuation_pause_and_speaker():
    t = build_transcript(RAW, "scribe_v2")
    assert t.language == "en"
    assert t.speakers == ["speaker_0", "speaker_1"]
    assert [x.text for x in t.words][:2] == ["Hello", "there."]  # spacing dropped
    assert [s.text for s in t.segments] == ["Hello there.", "How are", "you", "Fine (laughter)"]
    assert [(s.first_word, s.last_word) for s in t.segments] == [(0, 1), (2, 3), (4, 4), (5, 6)]
    assert t.segments[3].speaker == "speaker_1"
    assert (t.segments[0].start, t.segments[0].end) == (0.0, 0.9)
    assert Transcript.model_validate(t.model_dump()) == t


def test_untimed_and_out_of_order_words():
    words = parse_words(
        [w("b", 2.0, 2.5), w("a", 1.0, 1.5), {"text": "c", "type": "word", "start": None}]
    )
    # "c" has no time, so it takes the end of the word before it in Scribe's order ("a").
    assert [(x.text, x.start, x.end) for x in words] == [
        ("a", 1.0, 1.5),
        ("c", 1.5, 1.5),
        ("b", 2.0, 2.5),
    ]


def test_empty_and_unknown_language():
    t = build_transcript({"language_code": "xyz", "words": []}, "scribe_v2")
    assert (t.language, t.words, t.segments, t.speakers) == ("xyz", [], [], [])
    assert build_segments([]) == []


def test_scribe_transcriber_request(tmp_path, monkeypatch):
    calls = []

    def convert(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(model_dump=lambda **_: {"language_code": "eng", "words": []})

    client = SimpleNamespace(speech_to_text=SimpleNamespace(convert=convert))
    monkeypatch.setenv("HARNESS_ENV_FILE", str(tmp_path / "none.env"))
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"data")
    release = load_release(REPO_ROOT / "release.json")
    out = ScribeTranscriber(Settings.load(), release, client=client).transcribe(media)

    assert out["language_code"] == "eng"
    (kw,) = calls
    assert kw["model_id"] == "scribe_v2"
    assert kw["file"] == ("clip.mp4", b"data", "video/mp4")
    assert kw["diarize"] and kw["tag_audio_events"]
    assert kw["timestamps_granularity"] == "word"
    assert "language_code" not in kw  # automatic detection
    assert kw["request_options"]["max_retries"] >= 3
