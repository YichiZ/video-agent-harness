import pytest

from server.auth import AuthMode, claude_env, detect_auth
from server.config import Settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    for name in ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"]:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "claude").mkdir()

    def load():
        return Settings.load()

    return load


def test_api_key_wins(settings, monkeypatch, tmp_path):
    (tmp_path / "claude" / ".credentials.json").write_text("{}")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-x")
    s = settings()
    assert detect_auth(s).mode is AuthMode.API_KEY
    assert claude_env(s) == {"ANTHROPIC_API_KEY": "sk-x"}


def test_credentials_file_means_subscription(settings, tmp_path):
    (tmp_path / "claude" / ".credentials.json").write_text("{}")
    s = settings()
    assert detect_auth(s).mode is AuthMode.SUBSCRIPTION
    assert claude_env(s) == {}


def test_oauth_token_means_subscription(settings, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")
    assert detect_auth(settings()).mode is AuthMode.SUBSCRIPTION


def test_nothing_found(settings):
    status = detect_auth(settings())
    assert status.mode is AuthMode.NONE
    assert "ANTHROPIC_API_KEY" in status.detail
