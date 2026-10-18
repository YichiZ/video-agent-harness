from server.ingest.captions import WINDOW_S, ShotCaption, build_prompt, merge, windows


def shots(*bounds: float) -> list[dict]:
    pairs = zip(bounds, bounds[1:], strict=False)
    return [{"index": i, "start_s": a, "end_s": b} for i, (a, b) in enumerate(pairs)]


def test_short_video_is_one_window():
    s = shots(0, 10, 20)
    assert windows(s, 20) == [s]
    assert windows(s, WINDOW_S) == [s]


def test_long_video_splits_on_shot_starts():
    s = shots(0, 600, 1200, 1290, 1500)  # 25 min -> two windows of 750 s
    assert [[x["index"] for x in g] for g in windows(s, 1500)] == [[0, 1], [2, 3]]


def test_prompt_lists_shots_and_overlapping_speech():
    segments = [
        {"start": 1.0, "end": 3.0, "speaker": "speaker_0", "text": " Hello there."},
        {"start": 50.0, "end": 55.0, "speaker": None, "text": "Later."},
    ]
    prompt = build_prompt(shots(0, 4.5, 20), segments)
    assert "0: 00:00-00:04\n1: 00:04-00:20" in prompt
    assert "[00:01] speaker_0: Hello there." in prompt and "Later." not in prompt
    assert "(no speech)" in build_prompt(shots(0, 4), [])


def test_merge_keeps_order_and_fills_missing():
    caption = ShotCaption(
        index=1, caption="A card.", on_screen_text=["AMEX"], entities=[], action=""
    )
    merged = merge(shots(0, 4, 8), [caption])
    assert [m["caption"] for m in merged] == ["", "A card."]
    assert merged[1] == {
        "index": 1, "start_s": 4, "end_s": 8, "time": "00:04",
        "caption": "A card.", "on_screen_text": ["AMEX"], "entities": [], "action": "",
    }  # fmt: skip
