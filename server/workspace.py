"""Per-video workspaces: `workspace/<video_id>/`.

The id is a hash of the file's contents, so the same video always maps to the
same workspace and its finished ingest outputs are reused. Every ingest step and
tool reads and writes only inside its workspace. Files, by the step that writes them:

| File | Step |
|---|---|
| `source<ext>` | 1: a symlink to the original video (a copy if links are unsupported) |
| `meta.json` | 1: ffprobe metadata |
| `transcript.json` | 2 |
| `gemini_file.json`, `overview.json` | 3 |
| `inspect/<hash>.json` | 4: cached `inspect` tool answers |
| `shots.json` | 6 |
| `captions.json` | 7 |
| `index.sqlite` | 8 |
| `status.json` | 9 |
| `proxy.mp4`, `thumbs/<shot>.jpg` | 10: the player's 720p copy, shot thumbnails |
| `chats/<chat_id>/` | 15a: one folder per chat (see `server.chats`) |
"""

from __future__ import annotations

import hashlib
import json
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ID_LENGTH = 16
_CHUNK = 1 << 20


def video_id(video: Path) -> str:
    """The first 16 hex characters of the SHA-256 of the file's contents."""
    digest = hashlib.sha256()
    with video.open("rb") as f:
        while chunk := f.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()[:ID_LENGTH]


@dataclass(frozen=True)
class Workspace:
    dir: Path

    @property
    def id(self) -> str:
        return self.dir.name

    @classmethod
    def for_video(cls, root: Path, video: Path) -> tuple[Workspace, bool]:
        """Open or create the workspace for `video`. Returns it and whether it is new."""
        ws = cls(root / video_id(video))
        created = not ws.dir.exists()
        ws.dir.mkdir(parents=True, exist_ok=True)
        ws._link_source(video)
        return ws, created

    @classmethod
    def open(cls, root: Path, video_id: str) -> Workspace:
        ws = cls(root / video_id)
        if not ws.dir.is_dir():
            raise FileNotFoundError(f"no workspace for video {video_id} in {root}")
        return ws

    def path(self, name: str) -> Path:
        return self.dir / name

    @property
    def source(self) -> Path:
        """The video file: `source<ext>`, following the link to the original."""
        matches = sorted(self.dir.glob("source.*"))
        if not matches:
            raise FileNotFoundError(f"workspace {self.id} has no source file")
        return matches[0]

    def _link_source(self, video: Path) -> None:
        target = video.resolve()
        link = self.dir / f"source{video.suffix.lower()}"
        for old in self.dir.glob("source.*"):
            if old != link:
                old.unlink()
        if link.is_symlink() and link.resolve() == target:
            return
        if link.is_symlink() or link.exists():
            link.unlink()
        try:
            link.symlink_to(target)
        except OSError:
            shutil.copy2(target, link)

    def read_json(self, name: str) -> Any:
        return json.loads(self.path(name).read_text())

    def write_json(self, name: str, data: Any) -> Path:
        """Write atomically, so a crash never leaves a half-written output."""
        path = self.path(name)
        tmp = path.with_suffix(f"{path.suffix}.{threading.get_ident()}.tmp")  # threads may race
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(path)
        return path

    def remove(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)
