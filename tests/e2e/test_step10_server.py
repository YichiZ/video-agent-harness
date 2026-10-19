"""Step 10 e2e: `server.cli serve` as a subprocess, driven over HTTP and the WebSocket.

- Upload a video, poll its status until chat-ready, and range-request the proxy (206).
- Chat over `WS /chat`: text deltas stream before the `result`, the answer cites a
  correct `[mm:ss]`, and an `interrupt` sent mid-stream ends the turn.
- A second upload replaces the first, and chat is refused until it is ready
  (`HARNESS_FAKE_SLOW=overview` offline keeps it not-ready for a while).
- A video over the cap is refused with 422 and leaves the current video alone.

The chat needs `short` (Video-MME, skips in CI); the rest runs on generated media.
"""

from __future__ import annotations

import json
import re
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from websockets.sync.client import connect

from server.config import REPO_ROOT
from server.workspace import video_id
from tests.e2e.test_step05_chat import GROUNDED, VISUAL

READY_TIMEOUT_S = 900  # a live ingest of `short`
CLOCK = r"\[(\d{2}):(\d{2})"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextmanager
def server(cli, env: dict[str, str | None] | None = None) -> Iterator[str]:
    port = free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "server.cli", "serve", "--port", str(port)],
        cwd=REPO_ROOT,
        env=cli.env(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                httpx.get(f"{base}/docs", timeout=1)
                break
            except httpx.TransportError:
                assert proc.poll() is None, proc.stdout.read()
                assert time.monotonic() < deadline, "server did not start"
                time.sleep(0.1)
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def upload(base: str, video: Path) -> httpx.Response:
    def chunks():
        with video.open("rb") as f:
            while chunk := f.read(1 << 20):
                yield chunk

    return httpx.post(f"{base}/video", params={"name": video.name}, content=chunks(), timeout=120)


def wait_for(base: str, check, timeout: float = READY_TIMEOUT_S) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        status = httpx.get(f"{base}/video/current/status").json()
        if check(status):
            return status
        assert time.monotonic() < deadline, status
        assert not {"failed", "blocked"} & set(status["steps"].values()), status
        time.sleep(0.25)


def check_upload_and_proxy(base: str, video: Path) -> None:
    response = upload(base, video)
    assert response.status_code == 200, response.text
    assert response.json()["video_id"] == video_id(video)
    wait_for(base, lambda s: s["chat_ready"])
    wait_for(base, lambda s: s["steps"].get("proxy") == "done")
    ranged = httpx.get(f"{base}/video/current/proxy", headers={"Range": "bytes=0-1023"})
    assert ranged.status_code == 206 and len(ranged.content) == 1024
    assert ranged.headers["content-type"] == "video/mp4"
    wait_for(base, lambda s: s["steps"].get("shots") == "done")
    thumb = httpx.get(f"{base}/video/current/thumbs/0.jpg")
    assert thumb.status_code == 200 and thumb.content[:2] == b"\xff\xd8"


def first_status(ws) -> dict:
    """The first `ingest_status` frame, after the `history` frame a connection starts with."""
    frame = json.loads(ws.recv(timeout=10))
    while frame["type"] != "ingest_status":
        frame = json.loads(ws.recv(timeout=10))
    return frame


def ws_url(base: str) -> str:
    return base.replace("http://", "ws://") + "/chat"


def frames_until_result(ws, interrupt_after_text: bool = False) -> list[dict]:
    frames = []
    while True:
        frame = json.loads(ws.recv(timeout=600))
        if frame["type"] in ("ingest_status", "history"):
            continue
        frames.append(frame)
        if interrupt_after_text and frame["type"] == "text_delta":
            ws.send(json.dumps({"type": "interrupt"}))
            interrupt_after_text = False
        if frame["type"] in ("result", "error"):
            return frames


def check_chat(base: str) -> None:
    with connect(ws_url(base)) as ws:
        first = first_status(ws)
        assert first["chat_ready"], first

        ws.send(json.dumps({"type": "user_message", "text": GROUNDED}))
        frames = frames_until_result(ws)
        kinds = [f["type"] for f in frames]
        assert kinds[-1] == "result", frames[-1]
        assert "text_delta" in kinds and kinds.index("text_delta") < kinds.index("result")
        assert {"search", "get_transcript"} & {f["name"] for f in frames if f["type"] == "tool_use"}
        text = "".join(f["text"] for f in frames if f["type"] == "text_delta")
        assert "spike" in text.lower(), text
        cited = [int(m) * 60 + int(s) for m, s in re.findall(CLOCK, text)]
        assert any(0 <= t <= 12 for t in cited), text

        ws.send(json.dumps({"type": "user_message", "text": VISUAL}))
        frames = frames_until_result(ws, interrupt_after_text=True)
        assert frames[-1]["type"] == "result" and frames[-1]["interrupted"], frames[-1]


def check_replace_and_refuse(base: str, video: Path) -> None:
    response = upload(base, video)
    assert response.status_code == 200, response.text
    with connect(ws_url(base)) as ws:
        status = first_status(ws)
        assert status["video_id"] == video_id(video) and not status["chat_ready"], status
        ws.send(json.dumps({"type": "user_message", "text": "hello"}))
        frame = json.loads(ws.recv(timeout=10))
        while frame["type"] == "ingest_status":
            frame = json.loads(ws.recv(timeout=10))
        assert frame == {"type": "error", "message": "the video is not chat-ready yet"}


def check_over_cap(base: str, over_cap: Path, current: Path) -> None:
    response = upload(base, over_cap)
    assert response.status_code == 422 and "longer than 60 minutes" in response.text
    assert httpx.get(f"{base}/video/current/status").json()["video_id"] == video_id(current)
    bad = httpx.post(f"{base}/video", params={"name": "notes.txt"}, content=b"hi")
    assert bad.status_code == 422 and "not a video file" in bad.text


def test_server_generated_media(cli, test_media):
    with server(cli, {"HARNESS_FAKE_SLOW": "overview"}) as base:
        assert httpx.get(f"{base}/video/current/status").status_code == 404
        check_replace_and_refuse(base, test_media / "cuts.mp4")
        check_upload_and_proxy(base, test_media / "cuts.mp4")
        check_over_cap(base, test_media / "over_cap.mp4", test_media / "cuts.mp4")


def test_server_chat(cli, videomme, test_media):
    with server(cli) as base:
        check_upload_and_proxy(base, videomme("short"))
        check_chat(base)
    with server(cli, {"HARNESS_FAKE_SLOW": "overview"}) as base:
        check_replace_and_refuse(base, test_media / "cuts.mp4")


@pytest.mark.live
def test_server_chat_live(cli, videomme, test_media):
    with server(cli) as base:
        check_upload_and_proxy(base, videomme("short"))
        check_chat(base)
        check_over_cap(base, test_media / "over_cap.mp4", videomme("short"))
