# video-agent-harness

A local demo for chatting with an AI agent about one video. You upload a video, and the agent answers questions about it with timestamps you can click to jump to that moment.

- **Speech to text:** [ElevenLabs Scribe v2](https://elevenlabs.io/docs/capabilities/speech-to-text) produces a word-level, speaker-labelled transcript.
- **Visual understanding:** [Gemini](https://ai.google.dev/gemini-api/docs/video-understanding) (`gemini-3.8-flash`) produces shot captions and an overview, and answers focused questions about time ranges.
- **Agent:** [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) on Claude Opus 5.5 plans and calls the video tools.

The design builds on [HKUDS/VideoAgent](https://github.com/HKUDS/VideoAgent) and [Deep Video Discovery](https://arxiv.org/html/2505.18079v4).

## Status

The project is at the design stage, and there is no runnable code yet. See [DESIGN.md](DESIGN.md) for the architecture and [PLAN.md](PLAN.md) for the step-by-step build and test plan.

## Planned layout

```
server/        FastAPI: upload, ingest pipeline, agent session, video tools
web/           Vite + React: player, transcript, chat
.claude/       Skills for the agent and worktrees for development
workspace/     Per-video data (gitignored)
```

## Requirements (planned)

- Python 3.12+, Node 20+, ffmpeg
- `ANTHROPIC_API_KEY`, `ELEVENLABS_API_KEY`, `GEMINI_API_KEY` in `.env`

## Contributing

All work happens in git worktrees on feature branches, never in the main checkout. See [CLAUDE.md](CLAUDE.md).
