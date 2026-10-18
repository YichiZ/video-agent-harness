# video-agent-harness

A local demo for chatting with an AI agent about one video. You upload a video, and the agent answers questions about it with timestamps you can click to jump to that moment.

- **Speech to text:** [ElevenLabs Scribe v2](https://elevenlabs.io/docs/capabilities/speech-to-text) produces a word-level, speaker-labelled transcript.
- **Visual understanding:** [Gemini](https://ai.google.dev/gemini-api/docs/video-understanding) (`gemini-3.8-flash`) produces shot captions and an overview, and answers focused questions about time ranges.
- **Agent:** [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) on Claude Opus 5.5 plans and calls the video tools.

The design builds on [HKUDS/VideoAgent](https://github.com/HKUDS/VideoAgent) and [Deep Video Discovery](https://arxiv.org/html/2505.18079v4).

## Status

The scaffold, `doctor` and video probing (PLAN.md steps 0–1) are in place. The agent CLI lands over steps 2–5.

```
uv sync
cp .env.example .env    # add keys; ANTHROPIC_API_KEY is optional
make doctor             # checks ffmpeg, release.json, keys and the Claude auth mode
make test               # offline tests, no keys needed
uv run python -m server.cli ingest video.mp4   # probe it into workspace/<id>/
```

## Planned layout

```
server/        FastAPI: upload, ingest pipeline, agent session, video tools
web/           Vite + React: player, transcript, chat
.claude/       Skills for the agent and worktrees for development
workspace/     Per-video data (gitignored)
```

## Requirements

- Python 3.12+, Node 20+, ffmpeg
- `ELEVENLABS_API_KEY`, `GOOGLE_CLOUD_PROJECT` and `HARNESS_GCS_BUCKET` in `.env`, plus `gcloud auth application-default login` for Gemini on Vertex AI. `ANTHROPIC_API_KEY` is optional: without it the agent uses your Claude Code login (subscription).

## Contributing

All work happens in git worktrees on feature branches, never in the main checkout. See [CLAUDE.md](CLAUDE.md).
