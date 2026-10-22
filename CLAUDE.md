# CLAUDE.md

A local demo: upload one video, then chat with an agent about it. The full design is in [DESIGN.md](DESIGN.md), so read it before changing the architecture.

## Status

All of [PLAN.md](PLAN.md) (steps 0 to 13) is in place: M1 (the agent chat CLI), M2 (shots, per-shot captions, the search index, and parallel ingest with `status.json` and retries), M3 (the HTTP server and chat WebSocket), M4 (the browser UI: upload, player, ingest panel, shot strip, transcript, and the streaming chat with timestamp chips), step 12 (prompt v2 and the Skills), step 13 (the eval with its gate, and Langfuse tracing) and step 15a (several chats per video). Commands:

```
uv sync                                  # install (Python 3.12+, ffmpeg on PATH)
make doctor                              # tools, release.json, keys, Claude auth mode
uv run python -m server.cli ingest <video> [--only probe|transcribe|overview|shots|captions|index] [--force]
uv run python -m server.cli tool <name> '<json args>' [--video <video>]   # call one agent tool
make chat VIDEO=<video>                  # streaming terminal chat (`server.cli chat <video> --resume` or `--chat <id>` continues)
uv run python -m server.cli chats <video> # list a video's chats
make serve                               # HTTP server + chat WebSocket on localhost:8000; serves web/dist
make web / make test-web                 # build the UI (Vite + React + Tailwind) / vitest + build
cd web && npm run dev                    # UI dev server on :5173, proxying the API to :8000
make test                                # unit + offline e2e tests, no keys needed
make test-live                           # real APIs; ARGS=--record refreshes tests/fixtures
make eval                                # 60 Video-MME questions, scorecard in evals/results/, exit 1 below the gate
make eval SUBSET=smoke                   # the same on six questions (ARGS=--prompt-version broken must fail)
make eval-compare                        # prompt v1 vs v2 on the six smoke questions
make lint / make fmt                     # ruff
uv run python -m server.cli <command>    # the CLI
```

## Code map

- **Config:** `server/config.py`. `release.json` → `Release` (model ids, budgets, prompt version, `max_video_minutes`; unknown keys rejected). Env and `.env` → `Settings` (keys, paths, `HARNESS_*` flags; the module docstring lists them). `.env` is read without touching `os.environ`.
- **Claude auth:** `server/auth.py`. `detect_auth()` gives "API key", "Claude subscription (fallback)" or "none found"; `claude_env()` is the `env` to pass to `ClaudeAgentOptions`.
- **Providers:** `server/providers/`. Protocols `Transcriber`, `VisionModel`, `AgentClient` in `base.py`; always get one from the factories in `__init__.py`. Real implementations go in their own module (`elevenlabs.py` in step 2, `gemini.py` in step 3) and the factory picks them up. Fakes and recording wrappers are in `fakes.py`; fixture layout is in `fixtures.py`.
- **Workspace:** `server/workspace.py`. `workspace/<video_id>/`, where the id is the first 16 hex characters of the file's SHA-256. `source<ext>` links to the original. The module docstring lists which step writes which file; write outputs with `Workspace.write_json` (atomic).
- **Ingest steps:** `server/ingest/`. One module per step defining `STEP = Step(name, outputs, run, requires)`, listed in `STEP_MODULES`. `run_steps` runs them in parallel threads as their `requires` finish, writes `status.json` (chat-ready once `CHAT_READY` steps are done), and returns failed or blocked results instead of raising. A step whose outputs exist is skipped. Raise `ingest.Rejected` for unacceptable input. `ingest --only <name>` picks the step up automatically. `HARNESS_FAKE_FAIL` / `HARNESS_FAKE_SLOW` inject failures and delays in tests.
- **Agent tools:** `server/agent/tools.py`. `VideoTools` holds the plain tool methods for one workspace; `build_server()` wraps them as the in-process MCP server `video` (`ALLOWED_TOOLS` lists `mcp__video__*`). Raise `ToolError` for a call the agent should fix.
- **Agent session:** `server/agent/session.py`. `build_options()` is the only place `ClaudeAgentOptions` is set (only the `Skill` built-in, `mcp__video__*`, `dontAsk`, `setting_sources=[]`); `ChatSession.ask()` yields text, tool, tool_result and result events; given a `Chat` it keeps the session id and the turns there (evals pass none, so nothing is stored). The system prompt is `server/agent/prompt.<prompt_version>.md`. Offline replays `tests/fixtures/agent/<media>-chat.json` / `-resume.json`.
- **Chats:** `server/chats.py`. A video has any number of chats in `workspace/<video_id>/chats/<chat_id>/` (`chat.json`, `session.json`, `meta.json`); a new chat gets its folder with its first turn. `server/agent/titles.py` names a chat after its first answer with `release.title_model`; a rename by the user wins.
- **Skills:** `server/agent/plugin/skills/<name>/SKILL.md`, loaded as the local plugin `video`; list a new one in `session.SKILLS` as `video:<name>`.
- **Evals:** `evals/run.py` (`make eval`) ingests each video once, asks each question of `evals/dataset.jsonl` (built by `evals/build_dataset.py`, fixed seed) in a fresh session, scores it with `evals/scoring.py` (accuracy by task type and length, grounded citations, cost, latency, a Gemini-only baseline) and gates on `ACCURACY_MIN` / `CITATION_MIN`. `make eval-compare` (`evals/smoke.py`) compares prompt v1 with v2. Offline evals replay `tests/fixtures/agent/eval-<subset>/`.
- **Tracing:** `server/tracing.py`. `ChatSession.ask()` makes one Langfuse trace per turn (tools as child spans, versioned and tagged with the `release.json` hash) when the `LANGFUSE_*` keys are set; it is off with fake providers and never raises.
- **Server:** `server/api/`. `app.py` has the routes (its docstring lists them), `library.py` the current video and its ingest thread, `chat.py` the `WS /chat?chat=<id>` connection (one turn at a time per chat, `Library.busy`), `protocol.py` the frames; after changing frames run `scripts/export_protocol.py` to update `web/src/protocol.ts`.
- **UI:** `web/src/`. `App.tsx` lays out the upload zone, player, ingest panel, shot strip, transcript and chat (`components/`); `lib/useChat.ts` is the `WS /chat` client, `components/ChatList.tsx` the chat sidebar, and `lib/chips.ts` turns `[mm:ss]` into seek chips; `api.ts` wraps the HTTP routes and `protocol.ts` is generated. Browser e2e tests use Python Playwright with Google Chrome (`page` fixture), since Chromium cannot play the H.264 proxy.
- **CLI:** `server/cli/`. One module per subcommand with `register(subparsers)`; add its name to `COMMANDS`. Exit codes: 0 ok, 1 config, 2 bad input, 3 ingest step failed.
- **Tests:** `tests/unit/` and `tests/e2e/test_stepNN_<name>.py`. E2E tests run the CLI as a subprocess through the `cli` fixture, which isolates `.env`, keys and the Claude login and sets `HARNESS_FAKE_PROVIDERS=1`. Mark real-API tests `@pytest.mark.live`. Use the `videomme("short")` fixture for Video-MME videos (skips when absent, as in CI) and `test_media` for generated media.

## Decisions (don't change these without asking Yichi)

- **Deployment:** local, single-user demo with no auth or multi-tenancy.
- **Videos:** one video at a time, 60 minutes at most, mainly English. A new upload replaces the current video; its chats are kept.
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
- **After every merge:** run `git pull` in the main checkout (on `main`) so it stays current with `origin/main`.
