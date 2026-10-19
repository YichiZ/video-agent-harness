# video-agent-harness

A local demo for chatting with an AI agent about one video. You upload a video, and the agent answers questions about it with timestamps you can click to jump to that moment.

- **Speech to text:** [ElevenLabs Scribe v2](https://elevenlabs.io/docs/capabilities/speech-to-text) produces a word-level, speaker-labelled transcript.
- **Visual understanding:** [Gemini](https://ai.google.dev/gemini-api/docs/video-understanding) (`gemini-3.8-flash`) produces shot captions and an overview, and answers focused questions about time ranges.
- **Agent:** [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) on Claude Opus 5.5 plans and calls the video tools.

The design builds on [HKUDS/VideoAgent](https://github.com/HKUDS/VideoAgent) and [Deep Video Discovery](https://arxiv.org/html/2505.18079v4).

## Status

Steps 0 to 13 of the plan are in place: ingest (transcript, overview, shots, captions, search index), the agent chat in the terminal, the HTTP server, the browser UI with player, transcript, shot strip and streaming chat, and a Video-MME eval with a pass/fail gate and Langfuse tracing (`make eval`).

```
uv sync
cp .env.example .env    # add keys; ANTHROPIC_API_KEY is optional
make doctor             # checks ffmpeg, release.json, keys and the Claude auth mode
make test               # offline tests, no keys needed
```

## Running the UI

```
uv sync
make web                # installs web/node_modules on first run, then builds web/dist
make serve              # the server on http://127.0.0.1:8000, which also serves the built UI
```

Open http://127.0.0.1:8000, drop one video (60 minutes at most) on the page, and wait for the ingest chips to turn green. The chat opens once the transcript and overview are ready; click a timestamp in an answer to jump the player there.

To work on the UI with hot reload, keep `make serve` running and start the Vite dev server in a second terminal (it proxies the API to :8000):

```
cd web && npm run dev   # http://localhost:5173
```

## Chatting in the terminal

```
make chat VIDEO=path/to/video.mp4                                  # ingest if needed, then chat
uv run python -m server.cli chat path/to/video.mp4 --resume        # continue the last chat
```

## Layout

```
server/        FastAPI: upload, ingest pipeline, agent session, video tools
web/           Vite + React: player, transcript, chat
evals/         Video-MME eval sets and the eval runner
workspace/     Per-video data (gitignored)
```

## Requirements

- Python 3.12+, Node 20+, ffmpeg
- `ELEVENLABS_API_KEY`, `GOOGLE_CLOUD_PROJECT` and `HARNESS_GCS_BUCKET` in `.env`, plus `gcloud auth application-default login` for Gemini on Vertex AI. `ANTHROPIC_API_KEY` is optional: without it the agent uses your Claude Code login (subscription).

## Contributing

All work happens in git worktrees on feature branches, never in the main checkout. See [CLAUDE.md](CLAUDE.md).
