# CLAUDE.md

A local demo: upload one video, then chat with an agent about it. The full design is in [DESIGN.md](DESIGN.md), so read it before changing the architecture.

## Status

Steps 0 and 1 of [PLAN.md](PLAN.md) (scaffold, `doctor`, probe and workspace) are in place. Commands:

```
uv sync                                  # install (Python 3.12+, ffmpeg on PATH)
make doctor                              # tools, release.json, keys, Claude auth mode
uv run python -m server.cli ingest <video> [--only probe] [--force]
make test                                # unit + offline e2e tests, no keys needed
make test-live                           # real APIs; ARGS=--record refreshes tests/fixtures
make lint / make fmt                     # ruff
uv run python -m server.cli <command>    # the CLI
```

## Code map

- **Config:** `server/config.py`. `release.json` → `Release` (model ids, budgets, prompt version, `max_video_minutes`; unknown keys rejected). Env and `.env` → `Settings` (keys, paths, `HARNESS_*` flags; the module docstring lists them). `.env` is read without touching `os.environ`.
- **Claude auth:** `server/auth.py`. `detect_auth()` gives "API key", "Claude subscription (fallback)" or "none found"; `claude_env()` is the `env` to pass to `ClaudeAgentOptions`.
- **Providers:** `server/providers/`. Protocols `Transcriber`, `VisionModel`, `AgentClient` in `base.py`; always get one from the factories in `__init__.py`. Real implementations go in their own module (`elevenlabs.py` in step 2, `gemini.py` in step 3) and the factory picks them up. Fakes and recording wrappers are in `fakes.py`; fixture layout is in `fixtures.py`.
- **Workspace:** `server/workspace.py`. `workspace/<video_id>/`, where the id is the first 16 hex characters of the file's SHA-256. `source<ext>` links to the original. The module docstring lists which step writes which file; write outputs with `Workspace.write_json` (atomic).
- **Ingest steps:** `server/ingest/`. One module per step defining `STEP = Step(name, outputs, run, requires)`, listed in `STEP_MODULES`. A step whose outputs exist is skipped; `requires` pulls in earlier steps. Raise `ingest.Rejected` for unacceptable input. `ingest --only <name>` picks the step up automatically.
- **CLI:** `server/cli/`. One module per subcommand with `register(subparsers)`; add its name to `COMMANDS`. Exit codes: 0 ok, 1 config, 2 bad input, 3 ingest step failed.
- **Tests:** `tests/unit/` and `tests/e2e/test_stepNN_<name>.py`. E2E tests run the CLI as a subprocess through the `cli` fixture, which isolates `.env`, keys and the Claude login and sets `HARNESS_FAKE_PROVIDERS=1`. Mark real-API tests `@pytest.mark.live`. Use the `videomme("short")` fixture for Video-MME videos (skips when absent, as in CI) and `test_media` for generated media.

## Decisions (don't change these without asking Yichi)

- **Deployment:** local, single-user demo with no auth or multi-tenancy.
- **Videos:** one video at a time, 60 minutes at most, mainly English. A new upload replaces the old video and its chat.
- **v1 scope:** Q&A only, with no editing. Editing (an EDL plus ffmpeg) is phase 2.
- **Speech to text:** ElevenLabs Scribe v2 (`scribe_v2`) with word timestamps and diarization.
- **Vision:** Gemini `gemini-3.8-flash` for everything. The model is a config value in `release.json`, and `gemini-4-argon` is the fallback if quality is weak.
- **Orchestrator:** Claude Agent SDK (Python `claude-agent-sdk`) on Opus 5.5, with `permission_mode="dontAsk"`, an explicit tool allowlist, and no Bash or web tools for the agent.
- **Stack:** a FastAPI server and a Vite + React UI.
- **VideoAgent:** v1 imports no code from `~/code/VideoAgent` (a clone of HKUDS/VideoAgent). Its audio and editing roles come in phase 2 through a `BaseTool` → MCP adapter.

## Conventions

- **Grounding:** every agent claim about the video cites `[mm:ss]` from a tool result.
- **Workspace:** per-video data lives in `workspace/<video_id>/`, which is gitignored. Tools read and write only there.
- **Secrets:** API keys go in `.env`, which is gitignored. Never commit keys.

## Work happens in git worktrees

Never edit files or commit in the main checkout. `main` there stays clean and only moves by merging.

- **Starting work:** create a worktree on a new branch, either `claude --worktree <name>` or `git worktree add .claude/worktrees/<name> -b <name>`, and do all edits and commits inside it.
- **Location:** worktrees live under `.claude/worktrees/`, which is gitignored.
- **Finishing:** push the branch and open a PR to `main`. After it merges, remove the worktree with `git worktree remove .claude/worktrees/<name>`.
