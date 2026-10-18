"""`GeminiVisionModel`: the real `VisionModel`, on Vertex AI with Application Default Credentials.

Auth matches `~/code/video-rag`: `genai.Client(vertexai=True, project, location)`
with the ADC from `gcloud auth application-default login`. Vertex has no Files
API, so `upload` copies the video to `gs://<HARNESS_GCS_BUCKET>/video-agent-harness/`
and requests reference it by its `gs://` URI. GCS objects do not expire.

The model id comes from `release.gemini_model`. Requests that pass `start`, `end`
or `fps` send them as the video part's `VideoMetadata`, so Gemini only looks at
that range.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path

from google import genai
from google.cloud import storage
from google.genai import types

from server.config import Release, Settings
from server.providers import fixtures
from server.providers.base import JSON, RemoteFile

GCS_PREFIX = "video-agent-harness"


def _offset(seconds: float) -> str:
    return f"{seconds:.3f}s"


class GeminiVisionModel:
    def __init__(self, settings: Settings, release: Release):
        from server.providers import ProviderUnavailable

        missing = [
            name
            for name, value in [
                ("GOOGLE_CLOUD_PROJECT", settings.google_cloud_project),
                ("HARNESS_GCS_BUCKET", settings.gcs_bucket),
            ]
            if not value
        ]
        if missing:
            raise ProviderUnavailable(f"{' and '.join(missing)} not set (add to .env)")
        self.client = genai.Client(
            vertexai=True,
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
        )
        self.bucket = storage.Client(project=settings.google_cloud_project).bucket(
            settings.gcs_bucket
        )
        self.model = release.gemini_model

    def upload(self, media: Path) -> RemoteFile:
        media = media.resolve()
        key = fixtures.media_key(media)
        mime = mimetypes.guess_type(media.name)[0] or "video/mp4"
        blob = self.bucket.blob(f"{GCS_PREFIX}/{key}/{media.name}")
        blob.upload_from_filename(str(media), content_type=mime)
        return RemoteFile(
            name=blob.name,
            display_name=key,
            uri=f"gs://{self.bucket.name}/{blob.name}",
            mime_type=mime,
        )

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
