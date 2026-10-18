"""`doctor`: check the local setup and report the Claude and Gemini auth modes.

Never prints key values, only whether each key is set.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess

import google.auth
from google.auth.credentials import Credentials
from google.auth.exceptions import DefaultCredentialsError
from google.auth.transport.requests import Request as GoogleRequest
from rich.console import Console

from server import cli
from server.auth import AuthMode, detect_auth
from server.config import ConfigError, Release, Settings, load_release, release_path

KEYS = ["ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "GOOGLE_CLOUD_PROJECT", "HARNESS_GCS_BUCKET"]


def register(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("doctor", help="check tools, config, keys and Claude auth")
    p.add_argument(
        "--live",
        action="store_true",
        help="also make one tiny Claude call and refresh the Google credentials",
    )
    p.set_defaults(func=run)


def _tool_version(name: str) -> str | None:
    path = shutil.which(name)
    if not path:
        return None
    try:
        out = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=10)
        first = out.stdout.splitlines()[0] if out.stdout else ""
    except (OSError, subprocess.TimeoutExpired, IndexError):
        first = ""
    # "ffmpeg version 6.1.1-3ubuntu5 Copyright ..." -> "6.1.1-3ubuntu5"
    parts = first.split()
    version = parts[2] if len(parts) > 2 and parts[1] == "version" else "unknown version"
    return f"{version} ({path})"


def _google_credentials() -> Credentials | None:
    """Application Default Credentials, or None. Loads them without a network call."""
    try:
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    except DefaultCredentialsError:
        return None
    return creds


def run(args: argparse.Namespace) -> int:
    console = Console(soft_wrap=True, highlight=False)
    ok = True

    def line(label: str, status: str, detail: str = "") -> None:
        style = {"ok": "green", "set": "green", "missing": "yellow", "FAIL": "red"}.get(status)
        padded = f"{status:<8} " if detail else status
        mark = f"[{style}]{padded}[/]" if style else padded
        console.print(f"{label:<20} {mark}{detail}")

    for tool in ["ffmpeg", "ffprobe"]:
        version = _tool_version(tool)
        if version:
            line(tool, "ok", version)
        else:
            ok = False
            line(tool, "FAIL", "not found on PATH (macOS: `brew install ffmpeg`)")

    release: Release | None = None
    try:
        release = load_release()
        line(
            "release.json",
            "ok",
            f"agent {release.agent_model}, gemini {release.gemini_model}, "
            f"stt {release.stt_model}, prompt {release.prompt_version}",
        )
    except ConfigError as e:
        ok = False
        line("release.json", "FAIL", f"{e} ({release_path()})")

    settings = Settings.load()
    present = {
        "ANTHROPIC_API_KEY": settings.anthropic_api_key,
        "ELEVENLABS_API_KEY": settings.elevenlabs_api_key,
        "GOOGLE_CLOUD_PROJECT": settings.google_cloud_project,
        "HARNESS_GCS_BUCKET": settings.gcs_bucket,
    }
    for key in KEYS:
        line(key, "set" if present[key] else "missing")

    auth = detect_auth(settings)
    console.print(f"{'Claude auth':<20} {auth.mode.value} ({auth.detail})")
    google_creds = _google_credentials()
    if google_creds:
        project = settings.google_cloud_project or "not set"
        where = f"project {project}, location {settings.google_cloud_location}"
        console.print(f"{'Gemini auth':<20} Vertex AI, Application Default Credentials ({where})")
    else:
        console.print(
            f"{'Gemini auth':<20} none found (run `gcloud auth application-default login`)"
        )

    video_mme = settings.video_mme_dir
    found = "found" if video_mme.is_dir() else "not found, so Video-MME tests will skip"
    console.print(f"{'Video-MME':<20} {video_mme} ({found})")
    if settings.fake_providers:
        console.print(f"{'Providers':<20} fake (HARNESS_FAKE_PROVIDERS=1)")

    if args.live:
        if release is None:
            ok = False
            line("Claude call", "FAIL", "skipped because release.json is invalid")
        elif auth.mode is AuthMode.NONE:
            ok = False
            line("Claude call", "FAIL", "no Claude auth found")
        else:
            from server.providers.claude import ping

            try:
                reply = asyncio.run(ping(settings, release))
                line("Claude call", "ok", f"{auth.mode.value} replied {reply[:40]!r}")
            except Exception as e:  # report any SDK or CLI failure as a doctor failure
                ok = False
                line("Claude call", "FAIL", str(e))

        if google_creds:
            try:
                google_creds.refresh(GoogleRequest())
                line("Google credentials", "ok", "refreshed")
            except Exception as e:  # an expired login raises RefreshError
                ok = False
                line("Google credentials", "FAIL", str(e))

    return cli.EXIT_OK if ok else cli.EXIT_CONFIG
