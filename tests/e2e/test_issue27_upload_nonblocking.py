"""Issue #27 e2e: a large upload does not block the server's event loop.

A 1 GB body (not a video, so it is refused once received) is sent at full speed
while another client polls `GET /video/current/status`; every poll must answer
within `MAX_POLL_S`.
"""

from __future__ import annotations

import os
import threading
import time

import httpx

from tests.e2e.test_step10_server import server

SIZE = 1024**3
CHUNK = 1 << 20
MAX_POLL_S = 0.5


def test_upload_does_not_block_status(cli):
    block = os.urandom(CHUNK)

    def body():
        for _ in range(SIZE // CHUNK):
            yield block

    with server(cli) as base:
        result: dict[str, httpx.Response] = {}

        def send():
            result["upload"] = httpx.post(
                f"{base}/video", params={"name": "big.mp4"}, content=body(), timeout=300
            )

        sender = threading.Thread(target=send)
        sender.start()
        polls = []
        with httpx.Client(timeout=10) as client:
            while sender.is_alive():
                start = time.monotonic()
                client.get(f"{base}/video/current/status")  # 404: no video yet
                polls.append(time.monotonic() - start)
                time.sleep(0.05)
        sender.join()

    assert result["upload"].status_code == 422, result["upload"].text  # received, then refused
    assert len(polls) > 10, polls
    assert max(polls) < MAX_POLL_S, f"slowest of {len(polls)} polls: {max(polls):.3f} s"
