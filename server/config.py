"""Configuration.

Two sources, kept separate on purpose:

- `release.json` (`Release`): versioned, committed choices such as model ids,
  budgets and the prompt version. Changing it is a release.
- The environment and `.env` (`Settings`): machine-local values such as API keys
  and paths. Real environment variables win over `.env`. `.env` is read without
  touching `os.environ`, so a key that only lives in `.env` never leaks into
  subprocesses by accident (see `server.auth.claude_env`).

Environment variables the harness reads:

| Variable | Meaning |
|---|---|
| `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY` | Provider keys |
| `GOOGLE_CLOUD_PROJECT` | GCP project for Gemini on Vertex AI (auth is ADC) |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI location (default `global`) |
| `HARNESS_GCS_BUCKET` | GCS bucket that videos upload to for Gemini |
| `VIDEO_MME_DIR` | Video-MME root (default `~/data/video-mme`) |
| `HARNESS_ENV_FILE` | `.env` path (default `<repo>/.env`) |
| `HARNESS_RELEASE_FILE` | `release.json` path (default `<repo>/release.json`) |
| `HARNESS_WORKSPACE_DIR` | Per-video workspaces (default `<repo>/workspace`) |
| `HARNESS_FIXTURES_DIR` | Recorded provider responses (default `<repo>/tests/fixtures`) |
| `HARNESS_FAKE_PROVIDERS` | `1` replaces every provider with a fake that replays fixtures |
| `HARNESS_RECORD_FIXTURES` | `1` records real provider responses into the fixtures dir |
| `LANGFUSE_PUBLIC_KEY`, `_SECRET_KEY`, `_BASE_URL` | Langfuse tracing (step 13); off when unset |
| `HARNESS_FAKE_FAIL`, `HARNESS_FAKE_SLOW` | Fake providers only: steps that fail or are slow |
| `HARNESS_FAKE_STREAM_DELAY` | Fake agent only: seconds between replayed text deltas |
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]


class ConfigError(Exception):
    """A config file is missing or invalid. The message names the file and field."""


class Release(BaseModel):
    """The contents of `release.json`. Unknown keys are rejected to catch typos."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_model: str = Field(min_length=1)
    title_model: str = Field(min_length=1)
    gemini_model: str = Field(min_length=1)
    stt_model: str = Field(min_length=1)
    max_budget_usd: float = Field(gt=0)
    max_turns: int = Field(gt=0)
    inspect_limit: int = Field(ge=0)
    prompt_version: str = Field(min_length=1)
    max_video_minutes: float = Field(gt=0)

    @property
    def max_video_seconds(self) -> float:
        return self.max_video_minutes * 60


def release_path() -> Path:
    return Path(os.environ.get("HARNESS_RELEASE_FILE") or REPO_ROOT / "release.json")


def load_release(path: Path | None = None) -> Release:
    """Load and validate `release.json`, raising `ConfigError` naming the bad field."""
    path = path or release_path()
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError as e:
        raise ConfigError(f"{path.name}: file not found at {path}") from e
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path.name}: invalid JSON at line {e.lineno}: {e.msg}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name}: expected a JSON object")
    try:
        return Release.model_validate(raw)
    except ValidationError as e:
        problems = []
        for err in e.errors():
            field = ".".join(str(p) for p in err["loc"]) or "(root)"
            msg = "unknown field" if err["type"] == "extra_forbidden" else err["msg"]
            problems.append(f"field '{field}': {msg}")
        raise ConfigError(f"{path.name}: " + "; ".join(problems)) from e


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Machine-local settings, from the environment with `.env` as a fallback."""

    anthropic_api_key: str | None
    elevenlabs_api_key: str | None
    google_cloud_project: str | None
    google_cloud_location: str
    gcs_bucket: str | None
    video_mme_dir: Path
    workspace_dir: Path
    fixtures_dir: Path
    fake_providers: bool
    record_fixtures: bool
    env_file: Path
    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_base_url: str | None = None
    fake_fail: frozenset[str] = frozenset()
    fake_slow: frozenset[str] = frozenset()

    @classmethod
    def load(cls) -> Settings:
        env_file = Path(os.environ.get("HARNESS_ENV_FILE") or REPO_ROOT / ".env")
        file_values = dotenv_values(env_file) if env_file.is_file() else {}

        def get(name: str) -> str | None:
            # An empty value counts as unset, so `KEY=` in .env.example is harmless.
            value = os.environ.get(name)
            if value is None or value == "":
                value = file_values.get(name)
            return value or None

        def names(name: str) -> frozenset[str]:
            return frozenset(n.strip() for n in (get(name) or "").split(",") if n.strip())

        def path(name: str, default: Path) -> Path:
            return Path(get(name) or default).expanduser()

        return cls(
            anthropic_api_key=get("ANTHROPIC_API_KEY"),
            elevenlabs_api_key=get("ELEVENLABS_API_KEY"),
            google_cloud_project=get("GOOGLE_CLOUD_PROJECT"),
            google_cloud_location=get("GOOGLE_CLOUD_LOCATION") or "global",
            gcs_bucket=get("HARNESS_GCS_BUCKET"),
            video_mme_dir=path("VIDEO_MME_DIR", Path("~/data/video-mme")),
            workspace_dir=path("HARNESS_WORKSPACE_DIR", REPO_ROOT / "workspace"),
            fixtures_dir=path("HARNESS_FIXTURES_DIR", REPO_ROOT / "tests" / "fixtures"),
            fake_providers=_truthy(get("HARNESS_FAKE_PROVIDERS")),
            record_fixtures=_truthy(get("HARNESS_RECORD_FIXTURES")),
            env_file=env_file,
            langfuse_public_key=get("LANGFUSE_PUBLIC_KEY"),
            langfuse_secret_key=get("LANGFUSE_SECRET_KEY"),
            langfuse_base_url=get("LANGFUSE_BASE_URL"),
            fake_fail=names("HARNESS_FAKE_FAIL"),
            fake_slow=names("HARNESS_FAKE_SLOW"),
        )
