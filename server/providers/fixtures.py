"""Recorded provider responses under the fixtures dir (`tests/fixtures/` by default).

Layout, one JSON file per recorded call:

    scribe/<media_key>.json               Transcriber.transcribe
    gemini/<media_key>/<purpose>-<hash>.json  VisionModel.generate_json, one per distinct request
    gemini/<media_key>/<purpose>.json     ... fallback used for any request with that purpose
    agent/<name>.json                     AgentClient turns ({"turns": [[message, ...], ...]})

`media_key` is the source file's stem after resolving symlinks, so a workspace
`source.mp4` that links to `TbaCxIJ_VP4.mp4` replays the `TbaCxIJ_VP4` fixtures.
Live runs with `HARNESS_RECORD_FIXTURES=1` (`pytest -m live --record`) rewrite them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class FixtureMissing(LookupError):
    """No recorded response exists for a fake call."""


def media_key(media: Path) -> str:
    return media.resolve().stem


def read(path: Path) -> Any:
    if not path.is_file():
        raise FixtureMissing(
            f"no recorded response at {path}. Record one with a live run: "
            "`uv run pytest -m live --record`"
        )
    return json.loads(path.read_text())


def write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n")


def scribe_path(fixtures_dir: Path, media: Path) -> Path:
    return fixtures_dir / "scribe" / f"{media_key(media)}.json"


def request_hash(**request: Any) -> str:
    blob = json.dumps(request, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


def gemini_paths(fixtures_dir: Path, media_name: str, purpose: str, digest: str) -> list[Path]:
    """Candidate fixture paths for a Gemini call, most specific first."""
    base = fixtures_dir / "gemini" / media_name
    return [base / f"{purpose}-{digest}.json", base / f"{purpose}.json"]


def agent_path(fixtures_dir: Path, name: str) -> Path:
    return fixtures_dir / "agent" / f"{name}.json"
