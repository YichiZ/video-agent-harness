"""ElevenLabs Scribe speech to text (the real `Transcriber`, plan step 2).

The video file is uploaded as is (Scribe accepts video), with word timestamps,
diarization and audio-event tags, and the language detected automatically. The
SDK retries 429, 408, 409 and 5xx responses with exponential backoff (honouring
`Retry-After`); `MAX_RETRIES` raises its default of 2.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from elevenlabs.client import ElevenLabs

from server.config import Release, Settings
from server.providers import ProviderUnavailable
from server.providers.base import JSON

MAX_RETRIES = 5
TIMEOUT_S = 1800  # a 60-minute video can take several minutes to transcribe


class ScribeTranscriber:
    def __init__(self, settings: Settings, release: Release, client: ElevenLabs | None = None):
        if client is None:
            if not settings.elevenlabs_api_key:
                raise ProviderUnavailable("ELEVENLABS_API_KEY is not set (in .env or the env)")
            client = ElevenLabs(api_key=settings.elevenlabs_api_key, timeout=TIMEOUT_S)
        self.client = client
        self.model = release.stt_model

    def transcribe(self, media: Path) -> JSON:
        mime = mimetypes.guess_type(media.name)[0] or "application/octet-stream"
        response = self.client.speech_to_text.convert(
            model_id=self.model,
            # Bytes rather than an open file, so a retried request re-sends the whole body.
            file=(media.resolve().name, media.read_bytes(), mime),
            diarize=True,
            tag_audio_events=True,
            timestamps_granularity="word",
            request_options={"max_retries": MAX_RETRIES},
        )
        return response.model_dump(mode="json", exclude_none=True)
