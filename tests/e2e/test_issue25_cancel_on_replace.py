"""Issue #25 e2e: replacing the video cancels the old video's ingest.

With `HARNESS_FAKE_SLOW=overview,transcribe`, uploading A and then B right away
catches A's ingest early. A's run must stop: `captions` and `index` (and the
slow steps) end "cancelled" in its `status.json`, well before the 10 s the slow
steps would take, and it never writes `captions.json`. The server is a separate
process, so the test checks files rather than fake call logs.
"""

from __future__ import annotations

import json
import time

from server.workspace import video_id
from tests.e2e.test_step10_server import server, upload

STOPPED_WITHIN_S = 8  # under HARNESS_FAKE_SLOW's 10 s


def test_replace_cancels_old_ingest(cli, test_media):
    a, b = test_media / "cuts.mp4", test_media / "windowed.mp4"
    status_a = cli.workspace / video_id(a) / "status.json"
    with server(cli, {"HARNESS_FAKE_SLOW": "overview,transcribe"}) as base:
        assert upload(base, a).status_code == 200
        replaced = time.monotonic()
        assert upload(base, b).status_code == 200
        while True:
            steps = {n: s["state"] for n, s in json.loads(status_a.read_text())["steps"].items()}
            if not {"pending", "running"} & set(steps.values()):
                break
            assert time.monotonic() - replaced < STOPPED_WITHIN_S, steps
            time.sleep(0.1)

    assert steps["captions"] == "cancelled" and steps["index"] == "cancelled", steps
    assert steps["overview"] == "cancelled" and steps["transcribe"] == "cancelled", steps
    assert not (cli.workspace / video_id(a) / "captions.json").exists()
