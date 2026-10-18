# video-agent-harness

A local demo for chatting with an AI agent about one video. You upload a video, and the agent answers questions about it with timestamps you can click to jump to that moment.

- **Speech to text:** [ElevenLabs Scribe v2](https://elevenlabs.io/docs/capabilities/speech-to-text) produces a word-level, speaker-labelled transcript.
- **Visual understanding:** [Gemini](https://ai.google.dev/gemini-api/docs/video-understanding) (`gemini-3.8-flash`) produces shot captions and an overview, and answers focused questions about time ranges.
- **Agent:** [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) on Claude Opus 5.5 plans and calls the video tools.

The design builds on [HKUDS/VideoAgent](https://github.com/HKUDS/VideoAgent) and [Deep Video Discovery](https://arxiv.org/html/2505.18079v4).

## Running the chat

The agent chat CLI works end to end (PLAN.md steps 0–7). The web UI comes later.

### 1. Install

You need Python 3.12+, [uv](https://docs.astral.sh/uv/) and ffmpeg (macOS: `brew install ffmpeg`).

```
uv sync
cp .env.example .env
```

### 2. Configure

Fill in `.env` (real environment variables win over it):

| Key | Needed for |
|---|---|
| `ELEVENLABS_API_KEY` | Transcript (Scribe v2) |
| `GOOGLE_CLOUD_PROJECT` | Gemini on Vertex AI |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI location, optional (default `global`) |
| `HARNESS_GCS_BUCKET` | A bucket in that project; videos upload under `video-agent-harness/` |
| `ANTHROPIC_API_KEY` | Optional. Without it the agent uses your Claude Code login (subscription) |

Gemini authenticates with Application Default Credentials, not a key. Run this once:

```
gcloud auth application-default login
```

Then check the setup. `make doctor` reports ffmpeg, `release.json`, which keys are set, the Claude auth mode and the Google credentials; `uv run python -m server.cli doctor --live` also makes one tiny Claude call and refreshes the Google credentials.

```
make doctor
```

### 3. Chat

```
make chat VIDEO=path/to/video.mp4
uv run python -m server.cli chat path/to/video.mp4 --resume   # continue the last chat about it
```

The first run ingests the video into `workspace/<id>/`, where the id is a hash of the file's contents: ffprobe metadata, the transcript, a Gemini upload and overview, shot detection and shot captions. Each step prints `done` as it finishes. Later runs reuse what exists, so they start at once. Videos longer than 60 minutes (`max_video_minutes` in `release.json`) are rejected.

In the chat, answers stream as they are written and each tool call shows as one dim line. Ctrl-C interrupts an answer (at the prompt it clears the line), and `/quit` or Ctrl-D exits.

To try it on [Video-MME](https://video-mme.github.io/), put the dataset under `~/data/video-mme` (or set `VIDEO_MME_DIR`):

```
make chat VIDEO=~/data/video-mme/videos/data/TbaCxIJ_VP4.mp4
```

### Tests

```
make test        # offline tests, no keys needed
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
- The keys in [Configure](#2-configure)

## Contributing

All work happens in git worktrees on feature branches, never in the main checkout. See [CLAUDE.md](CLAUDE.md).
