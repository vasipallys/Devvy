# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Devvy — Evidence-Based Development: a local-first desktop AI workspace. A FastAPI backend runs Gemma 3 1B locally on CPU via transformers; an Electron + React frontend talks to it over HTTP/SSE (Chat) and WebSocket (Talk voice mode). The Python package is `backend/`, built with hatchling (`pyproject.toml`).

## Commands (Windows / PowerShell)

The venv lives at `.venv` (older setups may have `venv` — `scripts/start-backend.ps1` checks both).

```powershell
.\scripts\setup.ps1                                  # one-time: venv, pip install -e ".[dev]", npm install
.\scripts\start-backend.ps1                          # backend (uvicorn on 127.0.0.1:8765; runs python -m backend)
cd frontend; npm run dev                             # Vite (5173) + Electron, concurrently

.\.venv\Scripts\python.exe -m ruff check backend tests   # lint (line-length 100, py311)
.\.venv\Scripts\python.exe -m pytest                     # all tests
.\.venv\Scripts\python.exe -m pytest tests/test_api.py::test_health   # single test

cd frontend; npm run build                           # tsc -b && vite build
cd frontend; npm run lint                            # eslint --max-warnings=0
```

- pytest runs with `asyncio_mode = "auto"` — async test functions need no decorator.
- Real model inference requires `HF_TOKEN` in `.env` plus an accepted Gemma license on Hugging Face; tests do not need it.
- Optional extras: `.[voice]` (faster-whisper + pyttsx3 for Talk), `.[visual]` (Manim), `.[image]` (Diffusers), `.[gpu]` (bitsandbytes — do not enable on CPU Windows).
- Optional Phoenix tracing: `scripts\start-phoenix.ps1`; the app runs fine without it.

## Architecture

### Backend (`backend/`)

`api.py` is the composition root: `settings`, `GemmaRuntime`, `ChatAgent`, `TalkAgentGraph`, `VoiceEngine`, and `AnimationEngine` are module-level singletons created at import time. Tests therefore set `os.environ["PHOENIX_ENABLED"] = "false"` **before** importing `backend.api` — preserve that ordering in new tests.

Two separate LangGraph agents share one model runtime:

- **`agent.py` `ChatAgent`** — Chat workspace. Graph: `route → (research | image | respond)`. In `auto` mode, routing is keyword matching on the last user message (image/research/document/code/chat). Research uses `tools.py` (DDGS search + httpx fetch + BeautifulSoup extraction); it is the only mode that touches the network besides model download.
- **`agent_graph.py` `TalkAgentGraph`** — Talk voice companion. Graph: `route_visual → (research →)? companion`. Keyword sets decide `requires_research` (news/weather/current) and `requires_animation` (math/visual terms, which trigger a Manim render).

**`model.py` `GemmaRuntime`** loads the model lazily on first use (double-checked `threading.Lock`) and serializes generation with an `asyncio.Lock`. Inference runs in a thread executor; tokens stream out through an `asyncio.Queue`. Gemma requires strict user/assistant turn alternation — `ChatAgent._respond` merges adjacent same-role turns before applying the chat template; keep that invariant when touching history handling.

**Streaming pattern** (used by both endpoints): the API creates a `token_queue`, starts the agent in a task, and drains the queue with `asyncio.wait_for` timeouts, emitting status/heartbeat events while the CPU model is still prefilling. Chat uses SSE (`POST /api/chat/stream`, event types `start/token/status/done/error`); Talk uses WebSocket (`/api/talk/ws`, events like `state/transcript/token/text_complete/audio_ready/video_ready/media_warning`). Talk keeps conversation history in per-connection memory only; Chat persists to SQLite via SQLModel (`db.py`, stored under `APP_DATA_DIR`, default `./data`).

`voice_engine.py` (Whisper STT / pyttsx3 TTS) and `animation_engine.py` (Manim) import their heavy deps lazily and run outside the event loop — Manim in a subprocess, STT/TTS in executors — so the backend works without the optional extras installed.

Configuration is `config.py` `Settings` (pydantic-settings, reads `.env`); `get_settings()` is `lru_cache`d.

### Frontend (`frontend/`)

`src/main.tsx` mounts `DesktopApp`, which switches between `HomeScreen` (Chat vs Talk choice), `App.tsx` (chat workspace: sidebar, mode picker, SSE streaming via `src/api.ts`), and `TalkScreen.tsx` (WebSocket voice UI with animated avatar states Idle/Listening/Thinking/Speaking). The backend URL is hardcoded in `src/api.ts`. Electron (`electron/main.cjs`) uses context isolation, sandbox, no Node integration in the renderer, and opens external links in the system browser — keep those settings intact.

## Conventions and constraints

- Defaults are tuned for CPU laptops (`float32`, no quantization, `MAX_NEW_TOKENS=1024`, `DOCUMENT_MAX_CHARS=24000` cap on extracted document text). Don't regress CPU friendliness when changing generation code.
- Uploads are extension-allowlisted and size-limited (25 MB) in `api.py`; document text is capped before prompting.
- Agent failure messages are deliberate UX: research failures instruct the model to say live data was unavailable rather than invent answers; tool-only responses (e.g. image errors) are emitted as a single token since they bypass the LLM stream.
