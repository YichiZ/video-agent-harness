"""Shared test fixtures.

- `videomme(name)` returns the path of a Video-MME video from `tests/videomme.json`
  ("short", "medium", "long"), or skips the test when it is not on this machine
  (always the case in CI).
- `videomme_subtitle(name)` returns that video's Video-MME subtitles (`.srt` text),
  from `subtitle/<id>.srt` or `subtitle.zip` under `VIDEO_MME_DIR`, or skips.
- `test_media` builds the generated media with `scripts/make_test_media.sh` once
  per session and returns its directory.
- `--record` (with `-m live`) sets `HARNESS_RECORD_FIXTURES=1`, so live runs
  rewrite the recorded responses that offline mode replays.
"""

from __future__ import annotations

import json
import os
import subprocess
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest

from server.config import REPO_ROOT, Settings

TESTS_DIR = Path(__file__).resolve().parent
VIDEOMME = json.loads((TESTS_DIR / "videomme.json").read_text())


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--record",
        action="store_true",
        help="live runs also re-record tests/fixtures (sets HARNESS_RECORD_FIXTURES=1)",
    )


def pytest_configure(config: pytest.Config) -> None:
    if config.getoption("--record"):
        os.environ["HARNESS_RECORD_FIXTURES"] = "1"


def videomme_path(name: str) -> Path:
    entry = VIDEOMME[name]
    return Settings.load().video_mme_dir / "videos" / "data" / f"{entry['id']}.mp4"


@pytest.fixture
def videomme() -> Callable[[str], Path]:
    def get(name: str) -> Path:
        path = videomme_path(name)
        if not path.is_file():
            pytest.skip(f"Video-MME '{name}' video not found at {path} (set VIDEO_MME_DIR)")
        return path

    return get


@pytest.fixture
def videomme_subtitle() -> Callable[[str], str]:
    def get(name: str) -> str:
        root = Settings.load().video_mme_dir
        member = f"subtitle/{VIDEOMME[name]['id']}.srt"
        if (root / member).is_file():
            return (root / member).read_text(encoding="utf-8-sig")
        try:
            with zipfile.ZipFile(root / "subtitle.zip") as z:
                return z.read(member).decode("utf-8-sig")
        except (FileNotFoundError, KeyError):
            pytest.skip(f"Video-MME '{name}' subtitles not found under {root}")

    return get


@pytest.fixture(scope="session")
def test_media() -> Path:
    out = REPO_ROOT / "tests" / "media"
    subprocess.run([str(REPO_ROOT / "scripts" / "make_test_media.sh"), str(out)], check=True)
    return out
