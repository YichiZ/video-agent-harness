import json

import pytest

from server.config import REPO_ROOT, ConfigError, Settings, load_release


def test_committed_release_is_valid():
    release = load_release(REPO_ROOT / "release.json")
    assert release.gemini_model == "gemini-3.8-flash"
    assert release.stt_model == "scribe_v2"
    assert release.max_video_seconds == 3600


def test_missing_field_is_named(tmp_path):
    raw = json.loads((REPO_ROOT / "release.json").read_text())
    del raw["inspect_limit"]
    path = tmp_path / "release.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ConfigError, match="field 'inspect_limit': Field required"):
        load_release(path)


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="file not found"):
        load_release(tmp_path / "release.json")


def test_settings_env_beats_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GEMINI_API_KEY=from-file\nELEVENLABS_API_KEY=el-file\nANTHROPIC_API_KEY=\n"
    )
    monkeypatch.setenv("HARNESS_ENV_FILE", str(env_file))
    monkeypatch.setenv("GEMINI_API_KEY", "from-env")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    s = Settings.load()
    assert s.gemini_api_key == "from-env"
    assert s.elevenlabs_api_key == "el-file"
    assert s.anthropic_api_key is None  # empty counts as unset


def test_settings_does_not_touch_os_environ(tmp_path, monkeypatch):
    import os

    env_file = tmp_path / ".env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-file-only\n")
    monkeypatch.setenv("HARNESS_ENV_FILE", str(env_file))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert Settings.load().anthropic_api_key == "sk-file-only"
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_settings_flags_and_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("HARNESS_FAKE_PROVIDERS", "1")
    monkeypatch.setenv("VIDEO_MME_DIR", "~/somewhere")
    monkeypatch.delenv("HARNESS_RECORD_FIXTURES", raising=False)
    s = Settings.load()
    assert s.fake_providers is True
    assert s.record_fixtures is False
    assert s.video_mme_dir.is_absolute() and s.video_mme_dir.name == "somewhere"
