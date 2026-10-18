"""Step 0 e2e: `server.cli doctor` reports tools, config, keys and the Claude auth mode."""

from __future__ import annotations

import json

import pytest

from server.config import REPO_ROOT


def test_no_api_key_reports_subscription_fallback(cli):
    # A Claude Code login on disk, and no ANTHROPIC_API_KEY.
    (cli.tmp / "claude-config" / ".credentials.json").write_text("{}")
    result = cli("doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Claude subscription (fallback)" in result.stdout
    assert "ANTHROPIC_API_KEY    missing" in result.stdout


def test_no_api_key_and_no_login_reports_none_found(cli):
    result = cli("doctor")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Claude auth          none found" in result.stdout


def test_dummy_key_reports_api_key_without_printing_it(cli):
    secret = "sk-ant-dummy-0123456789"
    result = cli("doctor", env={"ANTHROPIC_API_KEY": secret, "GOOGLE_CLOUD_PROJECT": "g-project"})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Claude auth          API key" in result.stdout
    assert "ANTHROPIC_API_KEY    set" in result.stdout
    assert "GOOGLE_CLOUD_PROJECT set" in result.stdout
    assert "Gemini auth" in result.stdout
    assert secret not in result.stdout + result.stderr
    assert "g-secret" not in result.stdout + result.stderr


def test_api_key_from_env_file(cli):
    env_file = cli.tmp / "test.env"
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-from-file\n")
    result = cli("doctor", env={"HARNESS_ENV_FILE": str(env_file)})
    assert "Claude auth          API key" in result.stdout
    assert "sk-ant-from-file" not in result.stdout


def test_reports_ffmpeg_and_release(cli):
    result = cli("doctor")
    assert "ffmpeg               ok" in result.stdout
    assert "ffprobe              ok" in result.stdout
    assert "release.json         ok" in result.stdout
    assert "gemini-3.8-flash" in result.stdout


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda r: r.pop("gemini_model"), "gemini_model"),
        (lambda r: r.update(max_turns="many"), "max_turns"),
        (lambda r: r.update(max_budget_usd=-1), "max_budget_usd"),
        (lambda r: r.update(gemini_modle="typo"), "gemini_modle"),
    ],
)
def test_broken_release_exits_1_and_names_field(cli, mutate, field):
    release = json.loads((REPO_ROOT / "release.json").read_text())
    mutate(release)
    broken = cli.tmp / "release.json"
    broken.write_text(json.dumps(release))
    result = cli("doctor", env={"HARNESS_RELEASE_FILE": str(broken)})
    assert result.returncode == 1
    assert "release.json         FAIL" in result.stdout
    assert f"field '{field}'" in result.stdout


def test_release_not_json_exits_1(cli):
    broken = cli.tmp / "release.json"
    broken.write_text("{not json")
    result = cli("doctor", env={"HARNESS_RELEASE_FILE": str(broken)})
    assert result.returncode == 1
    assert "invalid JSON" in result.stdout


def test_missing_ffmpeg_exits_1(cli):
    # A PATH with python but without ffmpeg.
    result = cli("doctor", env={"PATH": str(cli.tmp)})
    assert result.returncode == 1
    assert "ffmpeg               FAIL" in result.stdout


@pytest.mark.live
def test_live_claude_call_in_detected_mode(cli):
    result = cli("doctor", "--live", timeout=180)
    assert "Claude auth" in result.stdout
    if "none found" in result.stdout:
        pytest.skip("no Claude auth on this machine")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Claude call          ok" in result.stdout
