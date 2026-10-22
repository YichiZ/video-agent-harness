import asyncio
import hashlib
import subprocess
import sys
import threading
import time

import pytest

from server.api.library import Library, UploadError, safe_name
from server.config import REPO_ROOT, Settings, load_release
from server.ingest import Step
from server.workspace import video_id


def test_protocol_ts_is_current():
    result = subprocess.run(
        [sys.executable, "scripts/export_protocol.py", "--check"], cwd=REPO_ROOT, text=True,
        capture_output=True,
    )  # fmt: skip
    assert result.returncode == 0, result.stdout


@pytest.mark.parametrize(
    ("name", "safe"),
    [("clip.mp4", "clip.mp4"), ("../../etc/x.mp4", "x.mp4"), ("my clip?.mov", "my_clip_.mov"),
     (".hidden.mp4", "hidden.mp4"), ("", "video")],
)  # fmt: skip
def test_safe_name(name, safe):
    assert safe_name(name) == safe


def test_receive_rejects_non_video_and_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_WORKSPACE_DIR", str(tmp_path))
    library = Library(Settings.load(), load_release(REPO_ROOT / "release.json"))

    async def body(*chunks):
        for c in chunks:
            yield c

    with pytest.raises(UploadError, match="not a video file"):
        asyncio.run(library.receive("notes.txt", body(b"x")))
    with pytest.raises(UploadError, match="empty upload"):
        asyncio.run(library.receive("a.mp4", body()))
    path = asyncio.run(library.receive("a b.mp4", body(b"abc", b"def")))
    assert path.name == "a_b.mp4" and path.read_bytes() == b"abcdef"
    assert not list((tmp_path / ".uploads").glob("*.part"))


def _library(tmp_path, monkeypatch) -> Library:
    monkeypatch.setenv("HARNESS_WORKSPACE_DIR", str(tmp_path / "workspace"))
    return Library(Settings.load(), load_release(REPO_ROOT / "release.json"))


def test_receive_hashes_and_writes_off_the_event_loop(tmp_path, monkeypatch):
    sha256 = hashlib.sha256

    class SlowDigest:
        def __init__(self):
            self._digest = sha256()

        def update(self, chunk):
            time.sleep(0.05)
            self._digest.update(chunk)

        def hexdigest(self):
            return self._digest.hexdigest()

    monkeypatch.setattr("server.api.library.hashlib.sha256", SlowDigest)
    library = _library(tmp_path, monkeypatch)

    async def body():
        for _ in range(10):
            yield b"x" * 1024

    async def main():
        ticks = 0

        async def tick():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker = asyncio.create_task(tick())
        path = await library.receive("a.mp4", body())
        ticker.cancel()
        return path, ticks

    path, ticks = asyncio.run(main())
    assert path.read_bytes() == b"x" * 10240
    assert ticks >= 10  # the loop kept running through 0.5 s of hashing


def _fake_probe(monkeypatch, hold: dict[str, threading.Event]):
    """A probe that waits on `hold[video_id]`, and ingests that only record that they started."""

    def run(ctx):
        if ctx.workspace.id in hold:
            hold[ctx.workspace.id].wait(5)
        ctx.workspace.write_json("meta.json", {})

    probe = Step(name="probe", outputs=["meta.json"], run=run)
    monkeypatch.setattr("server.ingest.steps", lambda: {"probe": probe})
    started: dict[str, bool] = {}

    def ingest(self, workspace):
        started[workspace.id] = True

    monkeypatch.setattr(Library, "_ingest", ingest)
    return started


def _videos(tmp_path):
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.write_bytes(b"video a")
    b.write_bytes(b"video b")
    return a, b


def test_startup_resumes_the_current_video_ingest(tmp_path, monkeypatch):
    a, _ = _videos(tmp_path)
    started = _fake_probe(monkeypatch, {})
    library = _library(tmp_path, monkeypatch)
    library.replace(a, "a.mp4")
    started.clear()
    Library(library.settings, library.release)  # the server restarts
    time.sleep(0.1)
    assert list(started) == [video_id(a)]
