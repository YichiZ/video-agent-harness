"""Issue #33 e2e: races between uploads, and between the CLI and the server.

- Two overlapping uploads, the larger (and slower) first: the current video ends
  up as the one whose body finished last.
- `server.cli ingest` on the video the server is ingesting (`HARNESS_FAKE_SLOW=overview`)
  waits for the server's run, then reports `overview` skipped; the Gemini upload
  recorded in `gemini_file.json` is not redone.
"""

from __future__ import annotations

import json
import threading
import time

import httpx

from server.workspace import video_id
from tests.e2e.test_step10_server import server, wait_for


def test_last_upload_wins(cli, test_media):
    large, small = test_media / "cuts.mp4", test_media / "windowed.mp4"
    assert large.stat().st_size > small.stat().st_size
    sent: dict[str, float] = {}

    def send(base, video, pause):
        def chunks():
            with video.open("rb") as f:
                while chunk := f.read(1 << 16):
                    time.sleep(pause)
                    yield chunk
            sent[video.name] = time.monotonic()

        response = httpx.post(f"{base}/video", params={"name": video.name}, content=chunks())
        assert response.status_code == 200, response.text

    with server(cli) as base:
        uploads = [
            threading.Thread(target=send, args=(base, large, 0.2)),  # about 1.5 s
            threading.Thread(target=send, args=(base, small, 0)),
        ]
        uploads[0].start()
        time.sleep(0.3)
        uploads[1].start()
        for t in uploads:
            t.join(60)
        last = max(sent, key=sent.get)
        assert last == large.name, sent
        status = httpx.get(f"{base}/video/current/status").json()
    assert status["video_id"] == video_id(large) and status["name"] == large.name


def test_cli_waits_for_the_server_ingest(cli, test_media):
    cuts = test_media / "cuts.mp4"
    gemini_file = cli.workspace / video_id(cuts) / "gemini_file.json"
    with server(cli, {"HARNESS_FAKE_SLOW": "overview"}) as base:
        response = httpx.post(
            f"{base}/video", params={"name": cuts.name}, content=cuts.read_bytes(), timeout=60
        )
        assert response.status_code == 200, response.text
        wait_for(base, lambda s: s["steps"].get("overview") == "running", timeout=30)

        result: dict = {}
        runner = threading.Thread(target=lambda: result.update(run=cli("ingest", str(cuts))))
        started = time.monotonic()
        runner.start()
        wait_for(base, lambda s: set(s["steps"].values()) == {"done"}, timeout=120)
        uploaded_at = json.loads(gemini_file.read_text())["uploaded_at"]
        runner.join(120)

    run = result["run"]
    assert run.returncode == 0, run.stderr
    assert "overview: skipped" in run.stdout, run.stdout
    assert time.monotonic() - started > 5, "the CLI did not wait for the server's ingest"
    assert json.loads(gemini_file.read_text())["uploaded_at"] == uploaded_at
