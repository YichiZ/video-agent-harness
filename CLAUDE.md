# CLAUDE.md

A local demo: upload one video, then chat with an agent about it. The full design is in [DESIGN.md](DESIGN.md), so read it before changing the architecture.

## Status

The project is at the design stage. Nothing is scaffolded yet, so there are no build, test or run commands. Add them here when the scaffold lands.

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
