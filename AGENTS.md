# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this is

Devvy — Evidence-Based Development: a local-first AI workspace that runs as a local web application. A FastAPI backend runs Gemma 3 1B locally on CPU via transformers; a React frontend served by Vite talks to it over HTTP/SSE (Chat) and WebSocket (Talk voice mode). There is no Electron shell — the UI is opened in an ordinary browser. The Python package is `backend/`, built with hatchling (`pyproject.toml`).

## Commands (Windows / PowerShell)

The venv lives at `.venv` (older setups may have `venv` — `scripts/start-backend.ps1` checks both).

```powershell
.\scripts\setup.ps1                                  # one-time: venv, pip install -e ".[dev]", npm install
.\scripts\start-backend.ps1                          # backend (uvicorn on 127.0.0.1:8765; runs python -m backend)
cd frontend; npm run dev                             # Vite dev server (5173), opens the browser
cd frontend; npm run preview                         # serve the production build from dist/

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

`api.py` is the composition root: `settings`, `GemmaRuntime`, `ChatAgent`, `TalkAgentGraph`, `VoiceEngine`, `AnimationEngine`, `SmartCodeService`, `EstimateService`, and `RunLedger` are module-level singletons created at import time. Tests therefore set `os.environ["PHOENIX_ENABLED"] = "false"` **before** importing `backend.api` — preserve that ordering in new tests.

Four model-backed services share one runtime. Two are LangGraph agents:

- **`agent.py` `ChatAgent`** — Chat workspace. Graph: `route → (research | image | respond)`. In `auto` mode, routing is keyword matching on the last user message (image/research/document/code/chat); attachments outrank the code trigger. `_route` returns a `route_reason` naming the matched phrase, which the UI shows as the reason for the decision. Research uses `tools.py` (DDGS search + httpx fetch + BeautifulSoup extraction); it is the only mode that touches the network besides model download.
- **`agent_graph.py` `TalkAgentGraph`** — Talk voice companion. Graph: `route_visual → (research →)? companion`. Keyword sets decide `requires_research` (news/weather/current) and `requires_animation` (math/visual terms, which trigger a Manim render), and also produce a `route_reason`.

**Research must never abort a turn.** Both agents wrap `web_search` and treat a network failure or an empty result set as evidence: the model is told plainly that live data was unavailable and instructed not to invent an answer. Successful research returns a structured `sources` list (title, URL, character count) that the API emits as a `research` agent event — sources are the citable basis for the answer, so the UI renders their URLs as links.

- **`smart_code.py` `SmartCodeService`** — repository-aware preview/apply. Preview never writes; `apply` requires an unexpired single-use token, unchanged-file hashes, and passing structural checks, then writes atomically with backups.
- **`estimate_code.py` `EstimateService`** — story estimation. See below.

**`model.py` `GemmaRuntime`** loads the model lazily on first use (double-checked `threading.Lock`) and serializes generation with an `asyncio.Lock`. Inference runs in a thread executor; tokens stream out through an `asyncio.Queue`. Gemma requires strict user/assistant turn alternation — `ChatAgent._respond` merges adjacent same-role turns before applying the chat template; keep that invariant when touching history handling.

**Streaming pattern** (used by both endpoints): the API creates a `token_queue`, starts the agent in a task, and drains the queue with `asyncio.wait_for` timeouts, emitting status/heartbeat events while the CPU model is still prefilling. Chat uses SSE (`POST /api/chat/stream`, event types `start/token/status/done/error`); Talk uses WebSocket (`/api/talk/ws`, events like `state/transcript/token/text_complete/audio_ready/video_ready/media_warning`). Talk keeps conversation history in per-connection memory only; Chat persists to SQLite via SQLModel (`db.py`, stored under `APP_DATA_DIR`, default `./data`).

`voice_engine.py` (Whisper STT / pyttsx3 TTS) and `animation_engine.py` (Manim) import their heavy deps lazily and run outside the event loop — Manim in a subprocess, STT/TTS in executors — so the backend works without the optional extras installed.

**`estimation_framework.py`** implements the Agile Story Point Estimation Framework v2.0 (spec: `agile_story_point_estimation_framework_fullstack.md`). It is the deterministic half of Estimate Code and owns **every number**: the 16-factor base sum, the §8.1/§8.2 adjustments, the §9 Fibonacci band (3/5/8/13/21/34), the framework-maturity cap, the §10 gates, confidence, and the recommendation. `estimate_code.py` only handles the conversation with the model, which is asked for one thing: a 1-5 score and a short reason per factor.

Keep that split. The model must never determine the point value — the product's core claim is that a reader can replay the arithmetic by hand from the scorecard. Every rule is recorded in `calculation.steps` whether or not it fired, and the deltas must reconcile to `adjusted_score` (enforced by a test). Factors the model skips are filled from keyword heuristics and labelled `heuristic`. The spec's four §12 walkthroughs are pinned in `tests/test_estimation_framework.py`; where that document's prose disagrees with its own rule tables, the tables win.

**`estimate_history.py`** records every completed estimate in its own table, keyed by story rather than by execution and not purged on a timer — jobs are, and an estimate is the artefact a team refers back to. The complete result payload is stored verbatim so a recalled entry renders through the same `EstimateResultView` as a fresh run; denormalised columns exist only for listing, search, and calibration stats. Writing history is a side effect: a failure there is logged and swallowed, never allowed to fail the estimate.

Schema changes go in `migrations.py`, not just the model: `create_all` never alters an existing table, so a new column would be missing for anyone who already had data. Append a numbered migration and never renumber a released one.

The blind review is conditional (near a band edge, elevated protected risk, heuristic-heavy, or a stack penalty) and runs warmer than the primary pass — at a shared temperature both passes converge and the second generation buys nothing. When it does not run the reviewer **mirrors the primary**; never fall back to the heuristic scorecard there, because arbitrating against it manufactures disagreement and silently moves scores.

**`harness.py`** provides `assemble_context` (priority-ordered, character-budgeted context with a provenance manifest) and `RunLedger` (privacy-preserving JSONL trajectories). All four workflows route third-party text — web results, uploaded documents, repository files, story text from Jira — through `assemble_context` so it is marked `UNTRUSTED EVIDENCE`, and every system prompt carries a context policy saying such content is data, never instructions. Do not bypass this when adding a new evidence source.

Configuration is `config.py` `Settings` (pydantic-settings, reads `.env`); `get_settings()` is `lru_cache`d.

### Frontend (`frontend/`)

`src/main.tsx` mounts `DesktopApp`, which switches between `HomeScreen` (Chat, Talk, Smart Code, Estimate Code), `App.tsx` (chat workspace: sidebar, mode picker, SSE streaming via `src/api.ts`), `TalkScreen.tsx` (WebSocket voice UI with animated avatar states Idle/Listening/Thinking/Speaking), `SmartCodeScreen.tsx`, and `EstimateCodeScreen.tsx`. `EvidencePanel.tsx` is shared by all four and renders the agent-event trajectory.

In `App.tsx`, `streamingRef` guards the `[activeId]` message loader: a new conversation gets its id from the `start` event, and refetching at that moment would replace the optimistic messages with the server's copy, where the assistant row does not exist yet — dropping every subsequent token. Keep that guard when touching the streaming path. The API base URL comes from `VITE_API_URL` in `src/api.ts`, defaulting to `http://127.0.0.1:8765`.

**Every model-backed request is a durable background job** (`backend/jobs.py`). Submitting returns a job id immediately; the worker claims queued rows from SQLite and runs them one at a time, because `GemmaRuntime` already serializes generation. Closing the tab cannot cancel work or lose a result. `GET /api/jobs/{id}/stream` sends a snapshot then live deltas — token deltas carry a character `offset` and agent events carry a `seq` so a client attaching mid-run drops the overlap instead of duplicating it. On startup the runner marks jobs orphaned by a previous process as `interrupted`, since a generation cannot be resumed. `useJobs` mounts once in `DesktopApp` to drive the activity badge and the `beforeunload` guard; each screen reattaches on load to a job of its kind that is still active.

Four rules in `jobs.py` are load-bearing and were each written after a failure. **Terminal is
terminal**: the move into a finished state is a conditional UPDATE, because a handler's result is
written from a database thread and a shutdown cancellation delivered just afterwards used to
rewrite a `succeeded` job as `cancelled`, discarding a result the user had watched complete.
**Shutdown is bounded**: cancelling a task inside `asyncio.to_thread` does not interrupt the
thread, so an unbounded await in `stop()` hangs the process — and made the test suite hang on
roughly every other run. **The wake event is created in `start()`**, not `__init__`, because an
`asyncio.Event` binds to the first loop that awaits it and the runner is a module-level singleton
that outlives any single loop; `start()` also resets the stop flag, or a restarted worker exits
immediately and every later submission sits queued forever. And **counting happens in SQL** —
loading a trajectory to `len()` it made an n-event run cost O(n²) row loads.

Costs that grow without limit are treated as bugs, not tuning: subscriber queues are bounded so a
stalled viewer cannot grow the worker for a whole generation; the streamer's drain thread is
stoppable by a flag so a failed generation does not leak one thread per request; uploads and
generated media have a retention sweep like jobs and the ledger do; and on the client, polling
stops in a hidden tab while streamed tokens are coalesced to one render per animation frame.

Every page is wrapped in `ErrorBoundary` (keyed per page). Without it a render error unmounts the tree and shows an empty black window that cannot be told apart from a slow load. Relatedly, `useEffect` callbacks must use a block body — a concise arrow returns its expression and React calls that as the cleanup function, which fails with `destroy is not a function`. TypeScript cannot catch this, so an ESLint `no-restricted-syntax` rule enforces it.

`Tooltip.tsx` carries the explanation layer: every status chip, score, badge, gate, pipeline
stage, and mode control says on hover **what** it is and **why** it is that way — the reasoning
that was previously only reachable by opening a panel. Two constraints are easy to get wrong
and were both hit while building it. It **clones its handlers onto the child** rather than
wrapping it: an earlier `display: contents` wrapper is invisible to layout but not to CSS, and
it silently broke every `.parent > div` rule (the Smart Code pipeline lost its whole
appearance). And a wrapped element that nothing can focus — a badge, an icon — is given a tab
stop, since otherwise its explanation is mouse-only. Scroll **repositions** the tooltip instead
of closing it, because focusing an off-screen element scrolls it into view and that scroll would
dismiss the tooltip the focus just opened.

The browser cannot read filesystem paths from a file input, so Smart Code takes the workspace root and target files as text. Never reintroduce a native picker dependency.

## Conventions and constraints

- Defaults are tuned for CPU laptops (`float32`, no quantization, `MAX_NEW_TOKENS=1024`, `DOCUMENT_MAX_CHARS=24000` cap on extracted document text). Don't regress CPU friendliness when changing generation code.
- Uploads are extension-allowlisted and size-limited (25 MB) in `api.py`; document text is capped before prompting.
- Agent failure messages are deliberate UX: research failures instruct the model to say live data was unavailable rather than invent answers; tool-only responses (e.g. image errors) are emitted as a single token since they bypass the LLM stream.
- **Emit pipeline progress as it happens, never in a burst at the end.** Generation on a CPU-bound 1B model dominates wall-clock time (typically 1-3 minutes), so a checklist that fills in only after the model returns tells the user nothing while they wait. Both `estimate_events` and the Smart Code preview map service progress stages onto UI checkpoints and forward them live, with a backstop pass so a checklist never finishes half-ticked. A stage carries its real status — a failed structural check renders as failed, not complete.
- **Never show a small model a JSON Schema it can copy.** Handed one, Gemma 3 1B returns the schema — `$defs`, `properties` and all. It is valid JSON and it validates whenever required fields have defaults, so it lands as an empty answer, fails the workflow rule instead of the contract, and burns both attempts reporting the wrong problem. Pass `example=` to `generate_structured` instead: a model shown a filled-in instance fills in an instance. The loop also detects the echo and names it in the repair message. Guard the example too: swapping schema for example moves the failure, and the model then copies the example verbatim — a well-formed, plausible change to a file nobody asked about. It is framed as shape-only for an unrelated task, and an answer equal to the example is rejected.
- Structured workflows degrade rather than fail: when the model cannot satisfy the contract across both loop attempts, Estimate Code falls back to a heuristic scorecard and reports the degradation in its evidence.
