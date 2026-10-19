import asyncio
import subprocess
import sys

import pytest

from server.api.library import Library, UploadError, safe_name
from server.config import REPO_ROOT, Settings, load_release


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
