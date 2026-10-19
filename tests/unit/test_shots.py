from server.ingest.shots import MAX_SHOT_S, MIN_SHOT_S, build_shots


def check_cover(shots, duration):
    assert shots[0][0] == 0 and shots[-1][1] == duration
    for (_, end), (start, _) in zip(shots, shots[1:], strict=False):
        assert end == start


def test_plain_cuts():
    assert build_shots([4, 8, 12, 16], 20) == [(0, 4), (4, 8), (8, 12), (12, 16), (16, 20)]


def test_no_cuts_and_out_of_range_cuts_ignored():
    assert build_shots([0, 20, 25], 20) == [(0, 20)]


def test_short_shot_merges_into_shorter_neighbour():
    # (5, 5.5) is short; its neighbours are 5 s and 2.5 s long, so it joins the second.
    assert build_shots([5, 5.5, 8], 20) == [(0, 5), (5, 8), (8, 20)]


def test_short_first_and_last_shots_merge_inward():
    shots = build_shots([0.4, 10, 19.8], 20)
    assert shots == [(0, 10), (10, 20)]


def test_burst_of_short_shots():
    cuts = [5 + 0.3 * k for k in range(1, 10)]
    shots = build_shots(cuts, 20)
    check_cover(shots, 20)
    assert all(b - a >= MIN_SHOT_S for a, b in shots)


def test_long_shot_is_split_evenly():
    shots = build_shots([10], 100)
    check_cover(shots, 100)
    assert shots[0] == (0, 10)
    assert [round(b - a, 3) for a, b in shots[1:]] == [30, 30, 30]
    assert all(b - a <= MAX_SHOT_S for a, b in shots)


def test_video_shorter_than_min_shot():
    assert build_shots([0.2], 0.5) == [(0, 0.5)]
