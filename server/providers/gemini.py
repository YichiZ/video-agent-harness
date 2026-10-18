"""`GeminiVisionModel`: the real `VisionModel`, on the Gemini Files API and `google-genai`.

The model id comes from `release.gemini_model`. Requests that pass `start`, `end`
or `fps` send them as the video part's `VideoMetadata`, so Gemini only looks at
that range.
"""

from __future__ import annotations

import json
import mimetypes
from datetime import UTC
from pathlib import Path

from google import genai
from google.genai import types

from server.config import Release, Settings
from server.providers import fixtures
from server.providers.base import JSON, RemoteFile


def _remote(file: types.File) -> RemoteFile:
    state = file.state.name if file.state else "STATE_UNSPECIFIED"
    if file.expiration_time is None:
        raise RuntimeError(f"Gemini file {file.name} has no expiration time")
    return RemoteFile(
        name=file.name or "",
        display_name=file.display_name or "",
        uri=file.uri or "",
        mime_type=file.mime_type or "video/mp4",
        state=state,
        expires_at=file.expiration_time.astimezone(UTC),
    )


def _offset(seconds: float) -> str:
    return f"{seconds:.3f}s"


class GeminiVisionModel:
    def __init__(self, settings: Settings, release: Release):
        if not settings.gemini_api_key:
            from server.providers import ProviderUnavailable

            raise ProviderUnavailable("GEMINI_API_KEY is not set (add it to .env)")
        self.client = genai.Client(api_key=settings.gemini_api_key)
        self.model = release.gemini_model

    def upload(self, media: Path) -> RemoteFile:
        mime = mimetypes.guess_type(media.name)[0] or "video/mp4"
        file = self.client.files.upload(
            file=media.resolve(),
            config=types.UploadFileConfig(display_name=fixtures.media_key(media), mime_type=mime),
        )
        return _remote(file)

    def get_file(self, name: str) -> RemoteFile:
        return _remote(self.client.files.get(name=name))

    def generate_json(
        self,
        *,
        file: RemoteFile,
        prompt: str,
        schema: JSON,
        purpose: str,
        start: float | None = None,
        end: float | None = None,
        fps: float | None = None,
    ) -> JSON:
        metadata = None
        if start is not None or end is not None or fps is not None:
            metadata = types.VideoMetadata(
                start_offset=_offset(start) if start is not None else None,
                end_offset=_offset(end) if end is not None else None,
                fps=fps,
            )
        video = types.Part(
            file_data=types.FileData(file_uri=file.uri, mime_type=file.mime_type),
            video_metadata=metadata,
        )
        response = self.client.models.generate_content(
            model=self.model,
            contents=[video, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )
        if not response.text:
            raise RuntimeError(f"Gemini returned no text for {purpose}")
        return json.loads(response.text)
