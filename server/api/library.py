"""The current video. There is one at a time, and each upload replaces it.

`Library.receive()` streams the body to `<workspace_dir>/.uploads/` (hashing
and writing in a worker thread, so the event loop stays free). `replace()` probes
it synchronously (so a rejected video never replaces the current one), points
`current.json` at it, cancels the previous video's ingest and starts the rest of
the ingest in a background thread. The video's chats (`server.chats`) are kept,
so uploading it again brings them back. On startup the current video's ingest
resumes (finished steps are skipped). `status()` reads the workspace's `status.json`.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from server import ingest
from server.api.protocol import IngestStatus
from server.config import Release, Settings
from server.workspace import ID_LENGTH, Workspace

CURRENT = "current.json"
UPLOADS = ".uploads"
MAX_UPLOAD_BYTES = 8 * 1024**3  # a 60-minute video, with room to spare
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".ts"}

log = logging.getLogger(__name__)


class UploadError(Exception):
    """The upload is not acceptable. The message is shown to the user as is."""


def safe_name(name: str) -> str:
    """The base name with anything but letters, digits, `.`, `-` and `_` replaced."""
    base = re.sub(r"[^\w.-]", "_", Path(name).name).lstrip(".")
    return base or "video"


class Library:
    def __init__(self, settings: Settings, release: Release):
        self.settings = settings
        self.release = release
        self.root = settings.workspace_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._ingests: dict[str, tuple[threading.Thread, threading.Event]] = {}
        """Ingest threads by video id, with the event that cancels each."""
        self.generation = 0
        """Counts uploads, so a chat connection knows the video changed under it."""
        self.busy: set[str] = set()
        """Chats with a turn running (their folders), so a second tab can't start another."""
        self.current, self.name = self._load_current()
        if self.current is not None:  # the server may have stopped mid-ingest
            with self._lock:
                self._start_ingest(self.current)

    def _load_current(self) -> tuple[Workspace | None, str | None]:
        path = self.root / CURRENT
        if not path.is_file():
            return None, None
        data = Workspace(self.root).read_json(CURRENT)
        try:
            return Workspace.open(self.root, data["video_id"]), data.get("name")
        except (FileNotFoundError, KeyError):
            return None, None

    def context(
        self, workspace: Workspace, cancel: threading.Event | None = None
    ) -> ingest.StepContext:
        return ingest.StepContext(
            workspace=workspace,
            settings=self.settings,
            release=self.release,
            cancel=cancel or threading.Event(),
        )

    async def receive(self, name: str, chunks: AsyncIterator[bytes]) -> Path:
        """Stream an upload to disk. Returns `.uploads/<video_id>/<name>`."""
        suffix = Path(safe_name(name)).suffix.lower()
        if suffix not in VIDEO_SUFFIXES:
            raise UploadError(
                f"{name}: not a video file (expected one of {sorted(VIDEO_SUFFIXES)})"
            )
        uploads = self.root / UPLOADS
        uploads.mkdir(exist_ok=True)
        part = uploads / f"{uuid.uuid4().hex}.part"
        digest, size = hashlib.sha256(), 0
        try:
            with part.open("wb") as f:

                def write(chunk: bytes) -> None:
                    digest.update(chunk)
                    f.write(chunk)

                async for chunk in chunks:
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise UploadError(f"{name}: larger than {MAX_UPLOAD_BYTES // 1024**3} GB")
                    await asyncio.to_thread(write, chunk)
            if size == 0:
                raise UploadError(f"{name}: empty upload")
            # Keep the file name: fixtures and Gemini display names are keyed by it.
            folder = uploads / digest.hexdigest()[:ID_LENGTH]
            earlier = sorted(folder.glob("*"))
            if earlier:  # the same bytes again: keep the file a running ingest reads
                return earlier[0]
            final = folder / safe_name(name)
            folder.mkdir(exist_ok=True)
            part.replace(final)
            return final
        finally:
            part.unlink(missing_ok=True)

    def replace(self, video: Path, name: str) -> Workspace:
        """Probe `video` and make it the current video. Raises `ingest.Rejected`."""
        workspace, created = Workspace.for_video(self.root, video)
        probe = ingest.steps()["probe"]
        try:
            if not probe.is_done(workspace):  # run directly: status.json belongs to the ingest
                probe.run(self.context(workspace))
        except ingest.Rejected:
            if created:
                workspace.remove()
            video.unlink(missing_ok=True)
            raise
        with self._lock:
            previous = self.current
            Workspace(self.root).write_json(CURRENT, {"video_id": workspace.id, "name": name})
            self.current, self.name = workspace, name
            self.generation += 1
            if previous is not None and previous.id != workspace.id:
                self._cancel_ingest(previous)
            self._start_ingest(workspace)
        return workspace

    def _start_ingest(self, workspace: Workspace) -> None:
        """Start `workspace`'s ingest unless it is running. Call with `_lock` held."""
        running = self._ingests.get(workspace.id)
        if running and running[0].is_alive() and not running[1].is_set():
            return  # the same video uploaded again
        # A cancelled run may still be stopping; the workspace lock makes this one wait.
        cancel = threading.Event()
        thread = threading.Thread(target=self._ingest, args=(workspace, cancel), daemon=True)
        self._ingests = {**self._ingests, workspace.id: (thread, cancel)}
        thread.start()

    def _cancel_ingest(self, workspace: Workspace) -> None:
        running = self._ingests.get(workspace.id)
        if running:
            running[1].set()

    def _ingest(self, workspace: Workspace, cancel: threading.Event) -> None:
        try:
            failed = ingest.failures(ingest.run_steps(self.context(workspace, cancel)))
            if failed:
                log.warning("ingest of %s: %s", workspace.id, [(r.name, r.state) for r in failed])
        except Exception:  # recorded in status.json; nothing else to tell
            log.exception("ingest of %s failed", workspace.id)

    def status(self) -> IngestStatus:
        workspace, name = self.current, self.name
        if workspace is None:
            return IngestStatus(
                video_id=None, generation=self.generation, name=None, chat_ready=False, steps={}
            )
        path = workspace.path(ingest.STATUS)
        data = workspace.read_json(ingest.STATUS) if path.is_file() else {}
        steps = data.get("steps", {})
        return IngestStatus(
            video_id=workspace.id,
            generation=self.generation,
            name=name,
            chat_ready=bool(data.get("chat_ready")),
            steps={n: s["state"] for n, s in steps.items()},
            errors={
                n: s.get("detail", "")
                for n, s in steps.items()
                if s["state"] in ("failed", "blocked")
            },
        )
