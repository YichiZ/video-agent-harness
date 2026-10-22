"""Issue #23 e2e: a server killed mid-ingest resumes the current video's ingest on restart.

`HARNESS_FAKE_SLOW=overview` keeps `overview` running long enough to kill the
server, which leaves it "running" in `status.json`. The restarted server must
finish every step without a new upload.
"""

from __future__ import annotations

import json

from server.workspace import video_id
from tests.e2e.test_step10_server import server, upload, wait_for

SLOW = {"HARNESS_FAKE_SLOW": "overview"}


def test_restart_resumes_ingest(cli, test_media):
    cuts = test_media / "cuts.mp4"
    with server(cli, SLOW, kill=True) as base:
        assert upload(base, cuts).status_code == 200
        wait_for(base, lambda s: s["steps"].get("overview") == "running", timeout=30)

    saved = json.loads((cli.workspace / video_id(cuts) / "status.json").read_text())
    assert saved["steps"]["overview"]["state"] == "running"  # stale, left by the killed server

    with server(cli, SLOW) as base:
        status = wait_for(
            base, lambda s: s["chat_ready"] and set(s["steps"].values()) == {"done"}, timeout=120
        )
    assert status["video_id"] == video_id(cuts)
    assert set(status["steps"]) == {
        "probe", "transcribe", "overview", "shots", "captions", "index", "proxy"
    }  # fmt: skip
