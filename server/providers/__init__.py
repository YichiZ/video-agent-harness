"""Provider factories. Code asks for a provider here, never constructs one directly.

`HARNESS_FAKE_PROVIDERS=1` returns fakes that replay `tests/fixtures/`.
Otherwise the real provider is imported lazily from its own module, which the
step that introduces it adds:

| Factory | Real implementation | Added in |
|---|---|---|
| `get_transcriber` | `server.providers.elevenlabs.ScribeTranscriber(settings, release)` | step 2 |
| `get_vision_model` | `server.providers.gemini.GeminiVisionModel(settings, release)` | step 3 |
| `get_agent_client` | `claude_agent_sdk.ClaudeSDKClient(options)` | step 0 (used in step 5) |

With `HARNESS_RECORD_FIXTURES=1`, real providers are wrapped so every response
is also written to the fixtures dir.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from server.config import Release, Settings
from server.providers import fixtures
from server.providers.base import JSON, AgentClient, RemoteFile, Transcriber, VisionModel
from server.providers.fakes import (
    FakeTranscriber,
    FakeVisionModel,
    RecordingAgentClient,
    RecordingTranscriber,
    RecordingVisionModel,
    ScriptedAgentClient,
)

if TYPE_CHECKING:
    from claude_agent_sdk import ClaudeAgentOptions

__all__ = [
    "JSON",
    "AgentClient",
    "ProviderUnavailable",
    "RemoteFile",
    "Transcriber",
    "VisionModel",
    "get_agent_client",
    "get_transcriber",
    "get_vision_model",
]


class ProviderUnavailable(RuntimeError):
    """The real provider is not implemented yet, or its key is missing."""


def _load_real(module: str, cls: str, step: int):
    try:
        return getattr(importlib.import_module(module), cls)
    except ModuleNotFoundError as e:
        if e.name != module:
            raise
        raise ProviderUnavailable(
            f"{module}.{cls} is not implemented yet (plan step {step}). "
            "Set HARNESS_FAKE_PROVIDERS=1 to use the fake."
        ) from None


def get_transcriber(settings: Settings, release: Release) -> Transcriber:
    if settings.fake_providers:
        return FakeTranscriber(settings.fixtures_dir)
    real = _load_real("server.providers.elevenlabs", "ScribeTranscriber", 2)(settings, release)
    if settings.record_fixtures:
        return RecordingTranscriber(real, settings.fixtures_dir)
    return real


def get_vision_model(settings: Settings, release: Release) -> VisionModel:
    if settings.fake_providers:
        return FakeVisionModel(settings.fixtures_dir)
    real = _load_real("server.providers.gemini", "GeminiVisionModel", 3)(settings, release)
    if settings.record_fixtures:
        return RecordingVisionModel(real, settings.fixtures_dir)
    return real


def get_agent_client(
    settings: Settings, options: ClaudeAgentOptions, fixture: str = "default"
) -> AgentClient:
    """`fixture` names the recorded stream (`agent/<fixture>.json`) to replay or record."""
    path = fixtures.agent_path(settings.fixtures_dir, fixture)
    if settings.fake_providers:
        return ScriptedAgentClient.from_fixture(path)
    from claude_agent_sdk import ClaudeSDKClient

    real = ClaudeSDKClient(options)
    if settings.record_fixtures:
        return RecordingAgentClient(real, path)
    return real
