# Devvy — Evidence-Based Development Application Specification

**Document status:** As-built implementation and reproducible rebuild specification
**Application version:** 0.1.0
**Last verified against source:** 2026-08-03
**Repository:** `Devvy`
**Product name:** Devvy — Evidence-Based Development

## 1. Purpose

This document is the implementation-grade source of truth for Devvy. It describes the product
behaviour, architecture, module responsibilities, data models, transport protocols, safety rules,
configuration, UI states, failure behaviour, and acceptance criteria in enough detail to rebuild
the project without access to its original source.

Normative language:

- **MUST** — required for compatibility or safety.
- **SHOULD** — the intended production-quality behaviour unless a documented constraint prevents it.
- **MAY** — optional.
- **As-built** — what version 0.1.0 currently does, including known boundaries.
- **Production hardening** — work required before changing the trusted local deployment model.

When this specification and the code disagree, the code is the as-built authority until both are
deliberately reconciled.

---

## 2. Product definition

Devvy is a trusted, single-user, evidence-based **local web application**. One FastAPI process
serves an HTTP/WebSocket API and hosts one shared local Gemma language-model runtime. A React
single-page application, built by Vite and opened in an ordinary browser, provides four workspaces.

> **There is no desktop shell.** Version 0.1.0 previously embedded the renderer in Electron. That
> shell, its preload bridge, and its native file dialogs have been removed. The frontend MUST run
> as plain static assets in a browser, and MUST NOT depend on any injected native bridge.

### 2.1 Product goals

1. Keep ordinary AI inference and user work on the local computer.
2. Make CPU latency understandable through token streaming and progressive status events.
3. Reuse one model implementation across chat, voice, code-change, and estimation use cases.
4. Put explicit gates around destructive or external side effects.
5. Continue delivering useful text when optional media or observability dependencies are absent.
6. Keep the default installation functional on CPU-only laptops.
7. Show the user what happened: context used, checks performed, and decisions taken.

### 2.2 Workspaces

| Workspace | Primary outcome | Transport | Persistence |
| --- | --- | --- | --- |
| Home | Select a workspace; shows how many requests are running | None | Page state only |
| Activity | Status and responses for every request, running or finished | HTTP polling | SQLite job store |
| Chat | Conversational answer or generated artifact | HTTP + SSE | SQLite history |
| Talk | Typed/voice conversation with optional audio, image, video | WebSocket | Connection memory only |
| Smart Code | Reviewed repository change or review findings | HTTP + named SSE; HTTP apply | Preview memory; backup/run files after apply |
| Estimate Code | Validated evidence-led story estimate | HTTP + named SSE | UI memory; optional JSON download / Jira write |

### 2.3 Actors

- **Local user** — owns the machine, supplies paths, approves writes, verifies output.
- **Local API** — validates input, orchestrates workflows, owns persistence, protects side effects.
- **Local model** — proposes natural-language or structured output. It has no filesystem, network,
  or Jira authority, and never decides policy.
- **Optional public web sources** — used only by explicit Research behaviour.
- **Optional Jira** — story source, and an explicitly enabled destination for points.
- **Optional Phoenix collector** — receives traces when enabled and reachable.

### 2.4 Non-goals in 0.1.0

- Multi-user accounts, authentication, authorization, or tenancy.
- Internet-facing deployment.
- Autonomous code writes without preview and human approval.
- Executing arbitrary generated code, or running project tests/builds as verification.
- Server-side storage of Talk history.
- Jira OAuth, webhooks, or automatic write-back.
- A packaged installer or backend process supervision.

---

## 3. Technology baseline

### 3.1 Backend

Python `>=3.11,<3.14`; FastAPI 0.115.x with Starlette 0.40–0.41; Uvicorn; Pydantic v2 and
pydantic-settings; SQLModel over SQLite; Transformers 4.x, PyTorch 2.x, Accelerate, Hugging Face
Hub; LangChain Core and LangGraph; HTTPX, DDGS, BeautifulSoup; pypdf, python-docx, openpyxl.

Optional extras (`pyproject.toml`):

| Extra | Contents | Enables |
| --- | --- | --- |
| `image` | diffusers, safetensors, Pillow | Local image generation |
| `voice` | faster-whisper, pyttsx3 | Talk STT and TTS |
| `visual` | manim | Talk visual explanations |
| `talk` | voice + visual combined | All Talk media |
| `gpu` | bitsandbytes | Quantized loading; **MUST NOT** be enabled on CPU Windows |
| `dev` | pytest, pytest-asyncio, ruff, httpx | Tests and lint |

### 3.2 Frontend

React 19, TypeScript 5.7 strict, Vite 6, `marked` for Markdown, `lucide-react` for icons, and
hand-written CSS with no component framework. Build output is a static bundle in `frontend/dist/`.

The frontend MUST have no runtime dependency on any desktop shell, native bridge, or Node API.

### 3.3 Default ports and origins

| Service | Default |
| --- | --- |
| FastAPI | `http://127.0.0.1:8765` |
| OpenAPI UI | `http://127.0.0.1:8765/docs` |
| Vite dev server | `http://localhost:5173` |
| Phoenix collector | `http://127.0.0.1:6006/v1/traces` |

---

## 4. System architecture

```mermaid
flowchart TD
  B["Browser: React SPA"] -->|"REST / SSE"| F["FastAPI composition root"]
  B -->|"WebSocket"| F
  F --> D["SQLModel / SQLite"]
  F --> C["ChatAgent"]
  F --> T["TalkAgentGraph"]
  F --> S["SmartCodeService"]
  F --> P["EstimateService"]
  C --> M["GemmaRuntime"]
  T --> M
  S --> M
  P --> M
  C --> X["Documents / research / images"]
  T --> V["Whisper / TTS / Manim"]
  F --> L["RunLedger (JSONL)"]
  F -. "optional traces" .-> O["Phoenix"]
```

### 4.1 Process model

1. Backend and frontend are started separately; neither supervises the other.
2. `backend.api` is the composition root. At import time it creates module-level singletons:
   `settings`, `GemmaRuntime`, `ChatAgent`, `TalkAgentGraph`, `VoiceEngine`, `AnimationEngine`,
   `SmartCodeService`, `EstimateService`, and `RunLedger`.
3. FastAPI lifespan initializes SQLite and optional observability.
4. The SPA assumes the API is already reachable and reports clearly when it is not.
5. Pages are selected by in-memory React state. There is no router and no URL persistence.
6. Generated media is served by FastAPI under `/generated`.

Because singletons are built at import time, tests MUST set `PHOENIX_ENABLED=false` in the
environment **before** importing `backend.api`.

### 4.2 Shared model invariant

All model-backed work MUST use one `GemmaRuntime`. It:

- loads tokenizer/model/pipeline lazily on first use;
- guards loading with a double-checked `threading.Lock` so only one load occurs;
- records a human-readable `load_error` for health reporting;
- calls `torch.set_num_threads` when `CPU_THREADS > 0`;
- maps configured dtype strings to torch dtypes;
- MAY enable 4-bit/8-bit loading only when explicitly configured;
- serializes **all** generation behind one `asyncio.Lock`;
- runs blocking generation in a worker thread;
- streams tokens via `TextIteratorStreamer` drained into an `asyncio.Queue`;
- samples only when `TEMPERATURE > 0`, otherwise overrides inherited sampling values to `None`;
- accepts a per-workflow `max_new_tokens` override.

The single generation lock is a CPU and memory safety feature: concurrent requests queue rather
than execute in parallel. A `TextIteratorStreamer` timeout means only that no token is ready yet —
it MUST NOT be treated as completion while the generation task is still running.

Gemma requires strict user/assistant alternation. Adjacent same-role turns MUST be merged before
applying the chat template, and a leading assistant turn MUST be dropped.

### 4.3 Context harness

`backend/harness.py` provides `assemble_context(sources, max_chars)`:

- sources are ordered by descending `priority` and truncated to a total character budget;
- each block is fenced with `TRUSTED CONTEXT` or `UNTRUSTED EVIDENCE` plus its id and label;
- it returns the assembled text and a manifest of `{id, label, characters, truncated, trusted}`.

**Every third-party text MUST pass through this harness**: web results, uploaded documents,
repository files, and story text from Jira or spreadsheets. Each consuming system prompt MUST carry
a context policy stating that untrusted content is data, never instructions. Do not bypass this
when adding a new evidence source.

### 4.4 Run ledger

`RunLedger` appends one JSON line per completed workflow run to
`APP_DATA_DIR/agent-runs/YYYY-MM-DD.jsonl`, pruning files older than `AGENT_RUN_RETENTION_DAYS`
(default 30) at construction. Retention failures MUST NOT prevent startup.

A record contains `run_id`, `workflow`, `status`, timestamps, `duration_ms`, `metadata`, the
`trajectory` of stage events, a `summary`, and a fixed `privacy` note.

The ledger MUST record operational evidence only: stage, status, label, elapsed time, counts, and
decisions. It MUST NOT store prompts, source content, model responses, or hidden reasoning.

### 4.5 Background job architecture

**Every model-backed request is a durable job.** A request MUST NOT be bound to the HTTP
connection that created it: closing the tab, losing the network, or navigating away cannot
cancel work or discard a result. The connection is a *view* onto a job, never the thing keeping
it alive.

`backend/jobs.py` owns this. Two storage layers, deliberately split:

| Layer | Holds | Read by |
| --- | --- | --- |
| Durable (SQLite `job`, `jobevent`) | status, progress label, streamed output, result, error, bounded evidence trajectory | a returning browser |
| Live (in-memory broadcast) | token deltas and events | clients attached right now |

Submission persists a `queued` row and returns immediately with a job id. The worker claims
work **from the database**, not an in-memory queue: submissions arrive on FastAPI's threadpool
where signalling an asyncio primitive would not be thread-safe, and a queued row already
survives a restart.

Concurrency MUST be one job at a time. `GemmaRuntime` already serializes generation behind a
single lock, so a wider pool would only queue inside the model while making progress reporting
dishonest.

#### 4.5.1 Attaching and reattaching

`GET /api/jobs/{id}/stream` sends a `snapshot` of the durable state, then live deltas until the
job finishes. Subscribing happens **before** the snapshot is taken, so no event can fall between
the two.

Each token delta carries the character `offset` it starts at. A client attaching mid-run may
receive deltas already contained in its snapshot; it MUST drop any delta whose offset is below
the snapshot length rather than appending it. Offsets are exact because the runner keeps the
authoritative in-memory text for the running job, ahead of the throttled database flush.

Streamed text is flushed to SQLite at most every `FLUSH_INTERVAL_SECONDS` (0.75). Persisting
every token would turn a CPU generation into a write-per-token workload for no benefit, since a
returning client only ever reads the latest snapshot.

Attaching is idempotent and safe at any point in a job's life, including after it finished.

#### 4.5.2 Lifecycle and recovery

Statuses: `queued`, `running`, `succeeded`, `failed`, `cancelled`, `interrupted`.

On startup the runner MUST reconcile: any job left `queued` or `running` by a previous process
is marked `interrupted` with an explanatory error. A generation cannot be resumed, so an orphan
MUST NOT be silently retried, and MUST NOT be left claiming to be running forever. Jobs older
than `JOB_RETENTION_DAYS` (default 7) are purged with their events.

Cancellation cancels the running task, or marks a still-queued job `cancelled` so the worker
never claims it. Partial output produced before cancellation MUST remain readable. A finished
job cannot be cancelled and returns 409.

A job whose `kind` has no registered handler MUST fail that job, not the worker.

#### 4.5.3 Client obligations

The client MUST warn before unload while any job is `queued` or `running`. The warning is a
courtesy, not a safety mechanism — the work continues regardless — and exists so a user does not
believe they cancelled something, and knows a result will be waiting.

On load, screens MUST reattach to a job of their kind that is still active, rather than
presenting an idle form while work is in flight. Chat additionally reattaches per conversation.

Stopping MUST cancel the job on the server, not merely detach the tab from it.

### 4.6 Structured-output loop

Smart Code and Estimate Code MUST use `backend/structured_output.py`:

1. Serialize the target Pydantic JSON schema into the user prompt.
2. Instruct the model to return exactly one JSON object with no Markdown.
3. Extract the first balanced JSON object, tolerating code fences and prefix text.
4. Validate against the model, then run an optional caller-supplied semantic validator.
5. On failure, retry **once**, feeding back the prior output and a concise, specific error.
6. If the second attempt fails, raise without applying any side effect.

The repair message MUST name the specific defect (for example, which factor ids are unscored)
rather than reporting a generic failure; a compact model otherwise reproduces the same omission.

---

## 5. Configuration

`backend/config.py` `Settings` (pydantic-settings, `.env`, `extra="ignore"`), cached by
`get_settings()`. `ensure_dirs()` creates the uploads and generated directories.

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` | `Devvy — Evidence-Based Development` | Display name |
| `APP_HOST` / `APP_PORT` | `127.0.0.1` / `8765` | Bind address |
| `APP_DATA_DIR` | `./data` | Root for DB, uploads, generated media, ledger |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowlist |
| `HF_TOKEN` | none | Hugging Face read token |
| `MODEL_ID` | `google/gemma-3-1b-it` | Chat model |
| `MODEL_DEVICE` / `MODEL_DTYPE` | `cpu` / `float32` | Placement and precision |
| `MODEL_QUANTIZATION` | `none` | `4bit`/`8bit` require the `gpu` extra |
| `MAX_NEW_TOKENS` | `1024` | Default generation cap |
| `MODEL_CONTEXT_MESSAGES` | `12` | Recent-turn window |
| `CPU_THREADS` | `0` | `0` leaves torch defaults |
| `TEMPERATURE` | `0.2` | `0` disables sampling |
| `DOCUMENT_MAX_CHARS` | `24000` | Document/research context budget |
| `SMART_CODE_MAX_CONTEXT_CHARS` | `48000` | Repository evidence budget |
| `SMART_CODE_MAX_OUTPUT_TOKENS` | `4096` | Smart Code generation cap |
| `ESTIMATE_MAX_OUTPUT_TOKENS` | `3072` | Estimate generation cap |
| `AGENT_RUN_RETENTION_DAYS` | `30` | Ledger retention |
| `WHISPER_MODEL` / `WHISPER_COMPUTE_TYPE` | `base.en` / `int8` | STT |
| `TTS_RATE` / `TTS_VOICE` | `170` / `female` | TTS |
| `MANIM_EXECUTABLE` | `manim` | Visual explanations |
| `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` | none | Jira credentials |
| `JIRA_STORY_POINTS_FIELD` | `customfield_10016` | Points field id |
| `JIRA_WRITE_ENABLED` | `false` | Master switch for write-back |
| `IMAGE_MODEL_ID` | none | Enables image generation |
| `IMAGE_INFERENCE_STEPS` | `8` | Clamped to 1–50 |
| `PHOENIX_ENABLED` | `true` | Tracing switch |
| `PHOENIX_COLLECTOR_ENDPOINT` | `http://127.0.0.1:6006/v1/traces` | OTLP endpoint |

Frontend configuration is `VITE_API_URL`, defaulting to `http://127.0.0.1:8765`.

### 5.1 CORS

`allow_origins` comes from settings; `allow_origin_regex` MUST be
`^https?://(localhost|127\.0\.0\.1)(:\d+)?$` so the dev server may move ports.

The literal `null` origin MUST NOT be allowed. It existed only for the removed Electron renderer
loading over `file://`, and would otherwise match sandboxed iframes and local files.

---

## 6. Persistence

SQLite at `APP_DATA_DIR/gemma_studio.db` via SQLModel. On connect the engine MUST set
`journal_mode=WAL`, `foreign_keys=ON`, and `busy_timeout=30000`; the connection uses
`check_same_thread=False` and a 30-second timeout.

| Table | Columns |
| --- | --- |
| `conversation` | `id` UUID pk, `title`, `created_at`, `updated_at` |
| `message` | `id` UUID pk, `conversation_id` FK indexed, `role`, `content`, `created_at`, `attachments` JSON, `metadata` JSON (attribute `message_metadata`) |

Messages are always listed ordered by `created_at`. Only Chat persists; Talk history is
connection-scoped and MUST NOT be written to disk.

---

## 7. API surface

All endpoints are under `http://127.0.0.1:8765`.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Liveness, model id, load state, load error |
| GET | `/api/system/status` | Secret-free capability and trust metadata |
| GET | `/api/jobs` | Job list plus active count (drives the close guard) |
| GET | `/api/jobs/{id}` | Job detail: status, output, result, evidence |
| GET | `/api/jobs/{id}/stream` | Snapshot then live deltas |
| POST | `/api/jobs/{id}/cancel` | Cancel queued or running work; 409 when finished |
| GET | `/api/conversations` | List, newest first |
| POST | `/api/conversations` | Create |
| GET | `/api/conversations/{id}/messages` | List messages; 404 if unknown |
| PATCH | `/api/conversations/{id}` | Rename (1–120 chars) |
| DELETE | `/api/conversations/{id}` | Delete with messages; 204 |
| POST | `/api/uploads` | Chat/Talk attachment upload |
| POST | `/api/chat/jobs` | Submit a chat turn; returns a job id (202) |
| WS | `/api/talk/ws` | Talk session |
| POST | `/api/smart-code/jobs` | Submit a preview; returns a job id (202) |
| POST | `/api/smart-code/apply` | Approved atomic write |
| GET | `/api/estimate-code/config` | Framework rubric, maturity taxonomy, stack options |
| POST | `/api/estimate-code/jobs` | Submit a single estimate (202) |
| POST | `/api/estimate-code/batch-jobs` | Submit a batch estimate (202) |
| POST | `/api/estimate-code/upload/parse` | CSV/XLSX parse and column mapping |
| POST | `/api/estimate-code/upload/estimate` | Estimate mapped rows |
| GET | `/api/estimate-code/jira/issues` | Read backlog issues |
| POST | `/api/estimate-code/jira/{key}/points` | Guarded write-back |
| — | `/generated/*` | Static generated media |

### 7.1 Uploads

Extension allowlist: `.pdf .docx .txt .md .py .js .ts .json .csv`. Unsupported types MUST return
415. Content is streamed in 1 MB chunks and MUST abort with 413 beyond 25 MB, deleting the partial
file. Stored as `APP_DATA_DIR/uploads/<uuid><ext>`; the response returns id, name, content type,
and size. A request MUST carry at most 10 attachment ids.

Attachment ids MUST be validated as UUIDs before any filesystem lookup, and unknown ids MUST be
skipped rather than raising.

---

## 8. Chat specification

### 8.1 Graph

`backend/agent.py` `ChatAgent`: `route → (research | image | respond)`; `research → respond`;
`image` and `respond` terminate.

### 8.2 Routing

In `auto` mode, routing is ordered keyword matching over the last user message:

1. **image** — `generate an image`, `create an image`, `draw `, `illustrate `
2. **research** — `search web`, `research `, `latest `, `current `, `look up`
3. **code** — `write code`, `implement `, `debug `, `python`, `typescript`
4. **document** — when extracted attachment context is non-empty
5. **chat** — otherwise

Attachments outrank the code trigger: a question about an uploaded file is a document question even
when it names a language.

Routing MUST return a `route_reason` naming the matched phrase, or stating that the user selected
the mode explicitly. It is surfaced as the `detail` of the `route` agent event. Explaining the
decision is part of the product contract, not a debugging aid.

### 8.3 Research

Research is the only behaviour besides model download that touches the network. `web_search` uses
DDGS for discovery, then HTTPX and BeautifulSoup to retrieve and clean each page.

Retrieval safety MUST include: http(s) only; DNS resolution checked against private, loopback,
link-local and reserved ranges; manual redirect following capped at 6 hops with re-validation at
each hop; HTML/plain-text content types only; a 2 MB response cap enforced against both
`content-length` and streamed bytes; and text truncated to 3,000 characters per page.

**Research failure MUST NOT abort the turn.** A search exception or an empty result set MUST
produce context that tells the model live data was unavailable and forbids invention, in both Chat
and Talk. Successful research MUST return a structured `sources` list of
`{title, url, characters}`, emitted as a `research` agent event. Sources are the citable basis for
the answer, so the UI MUST render their URLs as links rather than a count.

### 8.4 Prompt and history

The system prompt separates `<role>`, `<response_contract>`, and `<context_policy>`, and includes
the current local date and time with an instruction never to infer the date from training data.
Code mode appends a production-quality instruction. Research and document evidence are appended
through the context harness as untrusted blocks. Only `MODEL_CONTEXT_MESSAGES` recent turns are
supplied, merged to satisfy Gemma's alternation requirement.

### 8.5 Streaming protocol

`POST /api/chat/stream` returns `text/event-stream` with `Cache-Control: no-cache` and
`X-Accel-Buffering: no`. Payloads are unnamed SSE `data:` frames with a `type` field:

| `type` | Meaning |
| --- | --- |
| `start` | `run_id`, `conversation_id`, `message_id`, `model`, `local` |
| `agent_event` | Ledger event: stage, status, label, optional detail/evidence |
| `status` | Heartbeat while the CPU model prefills |
| `token` | Streamed content chunk |
| `error` | Failure message |
| `done` | `run_id` and the persisted assistant message |

The queue is drained with a 2-second `asyncio.wait_for`; each timeout emits a `status` frame
("Preparing the local model…" under 10 s, then elapsed seconds). Tool-only responses such as image
errors bypass the token stream, so if nothing streamed the final answer MUST be emitted as one
token before `done`.

### 8.6 Document extraction

| Extension | Extractor |
| --- | --- |
| PDF | pypdf page text |
| DOCX | python-docx paragraphs |
| TXT/MD/source/JSON/CSV | UTF-8 with replacement |

Combined text MUST be capped at `DOCUMENT_MAX_CHARS` before prompting.

### 8.7 Image generation

Requires `IMAGE_MODEL_ID` and the `image` extra. The pipeline is cached per model id under a
threading lock, moved to CUDA when available (otherwise CPU with attention slicing), and every
generation is serialized behind an asyncio lock because diffusers pipelines are not concurrency
safe. Output is written to the generated directory and referenced as `/generated/<uuid>.png`.

---

## 9. Talk specification

### 9.1 Graph

`backend/agent_graph.py` `TalkAgentGraph`: `route_visual → (research →)? companion`.

`route_visual` sets `requires_research` from news/weather/currency terms and `requires_animation`
from math/visual terms, and MUST also produce a `route_reason` naming the matched phrases.

### 9.2 WebSocket protocol

Client → server: `{type:"text", content, mode, attachment_ids}`, `{type:"commit", mime}` after
binary audio frames, and `{type:"reset"}`. Binary frames append to the per-connection audio buffer.

Server → client: `state` (`idle|listening|thinking|speaking|error`), `status`, `transcript`,
`token`, `heartbeat`, `text_complete`, `agent_event`, `image_ready`, `audio_ready`, `video_ready`,
`animation_state`, `media_warning`, `reset_complete`, `error`.

Talk modes other than `talk` reuse the Chat agent. Unsupported modes and more than 10 attachments
MUST raise. Missing attachments MUST raise rather than silently degrading.

### 9.3 Media

TTS and Manim rendering run after the text response is complete. A media failure MUST emit
`media_warning` and MUST NOT discard the completed text. Whisper STT, pyttsx3 TTS, and Manim import
lazily and run outside the event loop — Manim in a subprocess, STT/TTS in executors — so the
backend works without the optional extras installed. Voice uploads are deleted after transcription.

---

## 10. Smart Code specification

### 10.1 Input schema

| Field | Constraint |
| --- | --- |
| `objective` | required, 3–20,000 chars |
| `workspace_root` | required, 1–2,000 chars |
| `mode` | `generate`, `modify`, or `review` (default `modify`) |
| `target_paths` | up to 20, trimmed |
| `acceptance_criteria` | up to 20, trimmed |
| `language` / `framework` | optional hints |
| `risk` | `low`, `medium`, or `high` |

Because the browser cannot read a real path from a file input, the workspace root and target files
are entered as text. Targets MAY be absolute or relative to the workspace root; the API resolves
both. A native file picker MUST NOT be reintroduced as a dependency.

### 10.2 Supported files and retrieval

Source extensions: `.py .js .jsx .ts .tsx .java .go .rs .rb .php .cs .cpp .c .h .hpp .json .toml
.yaml .yml .md .html .css .sql .xml`. Skipped directories: `.git .hg .svn .venv venv node_modules
dist build target coverage __pycache__ .smartcode .idea .vscode`.

1. Resolve workspace and every target to canonical absolute paths.
2. Reject paths outside the workspace, including traversal and resolved symlink escape.
3. Reject unsupported extensions.
4. In Modify or Review, explicit targets MUST already exist.
5. Without targets, recursively scan and score path words against objective words; smaller files
   break ties; files over 512,000 bytes are skipped.
6. Prefer scored matches, else fall back to ranked source files; select at most 40.
7. Assemble content through the context harness within `SMART_CODE_MAX_CONTEXT_CHARS`, preserving
   the relevance ranking as source priority.

Review mode with no candidate files MUST fail. Generate/Modify MAY seed an empty workspace.

**Repository content MUST be marked `UNTRUSTED EVIDENCE`.** A checked-in README, fixture, or
docstring is third-party text that can contain instructions aimed at a model. The system prompt
MUST carry a context policy stating that repository content is data to be read and edited, never a
directive that can redirect the objective or widen file access.

Retrieval MUST return a manifest naming each included file, its included character count, and
whether the budget truncated it, exposed as `evidence.context_manifest` and
`evidence.truncated_files`.

### 10.3 Model output

`SmartCodeModelOutput`: `summary`, `plan` (1–12 steps), `edits` (≤20), `findings` (≤30). The schema
MUST tolerate compact model shapes — `operation`/`type` for `action`, `file`/`filename` for `path`,
`code`/`new_content` for `content`, and `changes`/`file_changes`/`files` or a path→content mapping
for `edits`.

A semantic validator MUST reject edits in Review mode, and reject an empty edit list in
Generate/Modify with an instruction to return the smallest concrete whole-file change.

### 10.4 Preview safety

- Every edit path is re-validated against the workspace.
- When explicit targets were supplied, an edit outside them MUST be rejected.
- `action` is normalized from the filesystem: `replace` when the file exists, else `create`.
- Structural verification runs per file: Python AST parse, JSON parse, or bracket balance.
  Empty content fails. Verification does **not** execute tests, linters, or builds.
- Unified diffs are produced against current on-disk content.
- Preview MUST NOT write to the workspace.
- A `preview_token` (UUID) is stored with the root, output, materialized files, pre-edit hashes,
  and verification, and expires after 30 minutes.
- `can_apply` is true only when at least one file was materialized and every check passed.

### 10.5 Apply contract

Apply MUST require: `approved=true`; a known, unexpired, single-use token (popped on use);
unchanged SHA-256 for every target since preview; and passing verification. Each write re-checks
workspace containment, backs up any existing file to
`APP_DATA_DIR/smart-code/backups/<run-id>/`, then writes via temp file + `flush` + `fsync` +
atomic `os.replace`. Run evidence is written to `APP_DATA_DIR/smart-code/runs/<run-id>.json`.

### 10.6 Preview SSE events

Named events: `started`, `status`, `stage`, `agent_event`, `loop`, `result`, `error`.

Stages are `classify`, `retrieve`, `plan`, `code`, `verify`, `critique`, `gate`. `classify` is
satisfied by request validation and `gate` by human approval; the rest come from the service.

Stages MUST be emitted **as the service reaches them**, not in a burst after generation returns. On
a CPU-bound model the generation dominates wall-clock time, and a checklist that fills in all at
once tells the user nothing while they wait. A stage carries its real status: a failed structural
check MUST render as failed, never as complete. A backstop pass after completion closes out any
stage the service did not report, so the checklist never ends half-ticked.

---

## 11. Estimate Code specification

Estimate Code implements the **Agile Story Point Estimation Framework v2.0 (Full-Stack Edition)**,
specified in [`agile_story_point_estimation_framework_fullstack.md`](agile_story_point_estimation_framework_fullstack.md).
Section references (`§`) below point at that document, which is normative. Where its worked
examples disagree with its rule tables, **the rule tables win**; the divergences are recorded in
`tests/test_estimation_framework.py`.

### 11.1 Story schema

| Field | Constraint |
| --- | --- |
| `title` | required, 1–500 chars |
| `user_story` | optional, ≤20,000 |
| `acceptance_criteria` | ≤50; a string input splits on newline/semicolon |
| `technical_breakdown` | optional, ≤20,000 |
| `existing_points` | optional number |
| `key` / `status` | optional Jira metadata |
| `labels` / `components` | string lists |
| `source` | `manual`, `jira`, or `upload` |
| `stack` | StackProfile (11.2); defaults apply when omitted |

Batches contain 1–100 stories, processed sequentially because the model is serialized, and apply
one StackProfile to every row.

### 11.2 Technology stack calibration layer

| Field | Values | Effect |
| --- | --- | --- |
| `frontend` | `none`, `react`, `angular`, `other` | selects guidance, anchors, risks |
| `backend` | `none`, `spring_boot`, `fastapi`, `flask`, `other` | as above |
| `database` | free text ≤80 | prompt context only |
| `maturity_level` | 1–5 (§5) | adjustment and point cap |
| `team_experience` | 1–5 | adjustment |
| `scenario` | `standard`, `new_framework`, `framework_upgrade`, `framework_migration` | gate |
| `new_testing_layer` | boolean | +1 |
| `new_observability_signal` | boolean | +1 |
| `build_pattern_change` | boolean | +1 |
| `additional_stacks` | 0–6 | +1 per additional stack |

Per-stack guidance (§4) is injected only for declared stacks, on the factors the framework
calibrates. `GET /api/estimate-code/config` serves the factor rubric, maturity taxonomy, Fibonacci
ladder, and stack options from the same definitions the calculation uses, so the UI cannot drift
from the engine.

### 11.3 Required scorecard

Exactly one entry per factor, scored 1–5, in framework order:

| # | Factor id | # | Factor id |
| --- | --- | --- | --- |
| 1 | `requirements_clarity` | 9 | `security_review` |
| 2 | `technical_complexity` | 10 | `observability_operations` |
| 3 | `integration_surface` | 11 | `cross_team_dependency` |
| 4 | `data_model_change` | 12 | `reversibility` |
| 5 | `frontend_effort` | 13 | `uncertainty` |
| 6 | `backend_effort` | 14 | `performance_scalability` |
| 7 | `test_effort` | 15 | `documentation_knowledge_transfer` |
| 8 | `regulatory_compliance` | 16 | `dod_overhead` |

Each entry carries `score`, `reason`, `group`, `stack_notes`, and `provenance` of `model` or
`heuristic`.

**The model scores; it does not calculate.** The model is asked only for a 1–5 score and a short
reason per factor, and MUST NOT determine the point value. Factors it omits are filled from
story-text keyword heuristics and marked `heuristic`; `evidence.scoring_provenance` reports the
split. A bare integer score carries no reason, so the reason falls back to keyword evidence rather
than echoing the digit. The model's own point guess, if offered, is reported in
`evidence.model_cross_check` as a cross-check signal only.

### 11.3.1 Controlled agentic evidence pipeline

The application MUST implement the local, resource-aware profile of
`agentic_story_point_estimation_pipeline_spec.md` around the normative v2 calculator. The profile
uses two serialized and independent model passes, not simulated parallel agents:

1. normalize the story into stable `EV-*` evidence ids and a SHA-256 input hash;
2. evaluate readiness and surface assumptions or targeted questions;
3. deterministically route product, architecture, frontend, backend, data, test, security,
   operations, and dependency lenses when their evidence triggers are present;
4. run a primary 16-factor assessment;
5. run a blind reviewer on the same evidence and rubric without primary scores;
6. detect dimension deltas, protected-risk differences, and Fibonacci-boundary differences;
7. create critic challenges only for material disagreements;
8. arbitrate ordinary material differences by the published midpoint policy and protected-risk
   differences conservatively by the higher score;
9. run the unchanged deterministic v2 arithmetic and policy gates;
10. replay the calculation and publish dimension stability, point stability, protected conflicts,
    and warnings; then hand the result to a human team decision.

The model MUST NOT determine points in either pass. Specialist routing, comparison, criticism,
arbitration, calculation, and consistency checks are application controls. The output MUST contain
`agentic_pipeline` with canonical evidence, readiness, routes, primary and blind assessments,
disagreements, critic challenges, arbitration decisions, audit, final report, prompt versions,
model policy, and a pending human-review record. Only concise public rationale is persisted;
hidden chain-of-thought MUST NOT be requested or stored.

### 11.4 Deterministic calculation

1. **Base sum** — the 16 scores (16–80).
2. **Base adjustments** (§8.1): uncertainty ≥ 4 → +3; cross-team ≥ 4 → +2; reversibility ≥ 4 → +2;
   frontend and backend both ≥ 3 → +1; regulatory or security ≥ 4 → +2.
3. **Stack adjustments** (§8.2): maturity 5 → +3; maturity 4 → +2; maturity 1 → +2; team
   experience ≤ 2 → +2; new testing layer → +1; new observability signal → +1; build pattern
   change → +1; +1 per additional polyglot stack.
4. **Fibonacci map** (§9): 16–24 → 3; 25–34 → 5; 35–44 → 8; 45–54 → 13; 55–64 → 21; 65+ → 34.
5. **Maturity cap** (§9): level 5 → 5 points; 4 → 8; 3 → 13; 2 → 21; 1 → 8.

Every rule MUST be recorded in `calculation.steps` **whether or not it fired**, with its spec
reference, delta, and running total. A penalty that was evaluated and did not apply is evidence.
Summing `delta` across all steps MUST equal `adjusted_score`.

A cap breach MUST NOT silently reduce the reported points; the mapped value is reported as-is and
the recommendation escalates instead.

### 11.5 Gates, confidence, and decision

Gates (§10, §13.4) are evaluated every run and reported in `evidence.policy_checks` with `rule`,
`reference`, `passed`, and `detail`: `uncertainty_max`, `maturity_max`, `knowledge_gap`,
`multiple_extremes`, `size_ceiling`, `maturity_cap`, `not_a_migration`.

The decision walks §13.4's flowchart in order and returns the first verdict: `epic_discovery`,
`upgrade_framework_first`, `spike_first`, `decompose`, or `proceed`. Where §13.4 offers
"DECOMPOSE or SPIKE", uncertainty ≥ 4 selects the spike.

Confidence follows the §13.2 table: `Low` when any factor is 5, three or more factors are ≥ 4, or
maturity ≥ 4; `High` only when every factor is ≤ 3 and no stack penalty applied; else `Medium`.

Risk flags list every factor ≥ 4 plus the declared stacks' named hazards. An escalated
recommendation MUST carry a filled-in spike definition (§10 template).

### 11.6 Loop and degradation

Each independent pass uses the same semantic gate: at least 8 of 16 factors scored, with a repair
message naming the missing ids. A pass that fails both attempts degrades to evidence heuristics;
the other pass still completes. If both degrade, the final scorecard is fully heuristic and the
audit records that state rather than failing the job.

### 11.7 Event streams

Single estimate jobs emit progressive events/nodes for `normalize`, `readiness`,
`assemble_context`, `declare_stack`, `specialist_routing`, `primary_estimate`, `blind_review`,
`disagreement`, `critic`, `arbitration`, `score_factors`, `apply_base_adjustments`,
`apply_stack_adjustments`, `map_to_fibonacci`, `evaluate_gates`, `decide`,
`consistency_audit`, and `human_review`. Attempt and degradation events use their owning model-pass
stage. Nodes MUST be emitted as work happens and reconstruct correctly after job reattachment.

Batch: `batch_started {count}`, `item_started {index,title}`, `item_node`, `status`, `agent_event`,
`loop`, `item_result`, `item_error`, `batch_result {results}`. An item failure MUST NOT stop later
items.

### 11.8 Import and Jira

CSV or XLSX up to 15 MB and 100 rows. Column mapping is suggested by fuzzy-matching headers against
alias sets for title, user story, acceptance criteria, technical breakdown, and existing points,
accepting a match at ratio ≥ 0.55 and never reusing a column. Title is mandatory; rows without one
are skipped; if no rows survive, the request fails.

Jira reads require base URL, email, and API token, request at most 100 issues, retain the
description as serialized JSON because Jira may return ADF, use a 30-second timeout, and map
upstream HTTP errors to 502.

Jira write MUST require all of: points in `1, 2, 3, 5, 8, 13, 21, 34`; `confirm=true`;
`JIRA_WRITE_ENABLED=true`; complete credentials; an issue key matching
`[A-Za-z][A-Za-z0-9_]+-\d+`; and a UI confirmation before the request. When the recommendation is
anything other than `proceed`, the UI MUST warn that it is writing a number the framework says
should not yet be committed, and take a separate confirmation first. Configuration or policy errors
are 403; upstream errors are 502.

---

## 12. Frontend specification

### 12.1 Application shell

`main.tsx` mounts `DesktopApp` into `#root` inside `React.StrictMode`. `DesktopApp` owns a page
union — `home`, `chat`, `talk`, `smart-code`, `estimate-code` — selected by React state, not a
router, and not persisted across reloads.

Each page MUST be wrapped in an error boundary, keyed by page. React unmounts the whole tree when a
render throws, so without one any error presents as an empty black window indistinguishable from a
slow load. The boundary MUST show the error message, expose the component stack, log both to the
console, and offer retry, return-to-home, and reload actions.

`useEffect` callbacks MUST use a block body. A concise arrow returns its expression, and React calls
an effect's return value as the cleanup function; a non-callable value there crashes the tree with
`destroy is not a function` on the next re-run or unmount. TypeScript does **not** catch this,
because `EffectCallback` permits `void` and DOM methods are typed `void` regardless of their runtime
return. An ESLint `no-restricted-syntax` rule enforces it.

Styling uses the local system font stack, dark near-black/green surfaces for Home, Chat, Talk and
Smart Code, and a light editorial layout for Estimate Code. Responsive breakpoints collapse grids
and hide non-essential labels with no font-network dependency. A `prefers-reduced-motion` block
disables animation.

### 12.2 Chat UI

Sidebar open/closed; welcome state with four prompt suggestions; title search; create and delete;
mode picker for Auto, Chat, Code, Research, Image, Document; multi-file upload chips that switch to
Document mode; optimistic user and blank assistant turns while streaming; status text in place of
blank assistant content until the first token; a stop button that aborts the fetch; Markdown with
code, links and images; persisted `/generated/...` URLs rewritten against the API origin; an error
banner and a local-model disclaimer.

The conversation loader MUST NOT refetch the conversation currently being streamed into. A new
conversation receives its id from the `start` event; reloading at that moment would replace the
optimistic turns with the server's copy, which does not yet contain the assistant row, and every
subsequent token would be discarded. On `done`, the optimistic assistant turn is replaced by the
persisted message so it carries its real id and metadata.

Markdown MUST be sanitized after `marked`: dangerous elements removed, attributes allowlisted per
tag, non-HTTP links and image sources stripped, and external links given
`target="_blank" rel="noreferrer noopener"`.

### 12.3 Talk UI

States `connecting`, `idle`, `listening`, `thinking`, `speaking`, `error`. Microphone capture uses
`MediaRecorder` with echo cancellation and noise suppression; audio is sent as binary frames then
committed. Playback drives an `AnalyserNode` for mouth animation and word-level subtitles. The
socket reconnects with exponential backoff capped at 10 seconds. Reset clears local state and
sends `{type:"reset"}`.

### 12.4 Smart Code UI

Mode, workspace path, target list, objective, language/framework hints, acceptance criteria, and
risk tier. Targets are added from a text field by button or Enter, de-duplicated, and removable.
Field hints MUST state that the workspace path is absolute and that an empty target list lets Devvy
rank files itself.

The pipeline renders the seven stages with their real status. Results show summary, plan strip,
findings, per-file diff tabs, verification row, and an apply button enabled only when
`can_apply` is true. Apply requires a confirmation dialog and appends an `apply` evidence event.

### 12.5 Estimate Code UI

Sources: Jira selection, manual single-story form, CSV/XLSX upload with mapping confirmation.

A stack calibration panel precedes all three and applies to every story in a batch. Each control
MUST display the adjustment it carries (`+2 emerging`, `+1 new test layer`) so the user sees the
cost of a declaration before running. The maturity slider shows the level's name, definition, and
point cap, sourced from the config endpoint.

The UI MUST show the controlled pipeline progress list, errors, batch result selection with a per-row
recommendation chip, a point hero with confidence, the Fibonacci scale, JSON download, and a
conditional Jira write button.

The primary result workspace MUST provide horizontally scrollable, keyboard-operable tabs for
Final report, Readiness, Evidence, Specialists, Primary, Blind review, Critic & resolution,
Calculation, References, AI scenario, Human consensus, and Audit. Per-dimension views show the
score range, public rationale, evidence ids, why-not-lower/higher boundary, confidence, and
provenance. The disagreement view pairs both scores with the critic challenge, arbitration policy,
selected value, and human-review flag. The audit shows input hash, prompt versions, local model,
calculation replay, stability metrics, and explicitly states that hidden chain-of-thought is not
stored. An AI delivery discount MUST NOT be applied without calibrated team outcome evidence.

The framework appendix MUST include, progressively disclosed: the **calculation ledger** (every rule with spec
reference, applied flag, delta, running total, with non-firing rules behind a toggle); the
**16-factor scorecard** with a 1–5 indicator, provenance badge and stack notes; the **gate list**
including passing gates; risk flags; calibration anchors; effort envelope; hidden sub-tasks;
risks and assumptions; a filled-in **spike definition** when escalated; and **provenance** — the
context manifest and the model-versus-calculated cross-check.

A verdict banner carrying the recommendation sits above the point hero. Because a CPU run takes
minutes, the evidence panel MUST be visible **during** the run, not only after the result arrives.

### 12.6 Evidence panel

Shared by all four workspaces. Renders the agent-event trajectory as a timeline with per-event
status icon, stage, elapsed seconds, optional detail, and evidence rows. Running, failed, and the
most recent events are expanded by default.

Evidence values MUST stay legible: URL lists render as clickable hostname chips, string lists as
chips, booleans as yes/no. Retrieved source URLs MUST NOT be collapsed to a count. The empty state
MUST state that hidden chain-of-thought is never shown or stored.

### 12.7 API client

`src/api.ts` exposes typed helpers over `VITE_API_URL`. It provides a generic JSON helper that
surfaces `detail` from error bodies, an unnamed-SSE reader for Chat, and a named-SSE reader
(`consumeSSE`) for Smart Code and Estimate Code that parses `event:`/`data:` blocks split on blank
lines and supports `AbortSignal`.

---

## 13. Security and safety

| Control | Rule |
| --- | --- |
| Binding | Loopback only by default |
| CORS | Settings allowlist plus loopback regex; `null` origin forbidden |
| Uploads | Extension allowlist, 25 MB cap, UUID-named storage, id validation |
| Research egress | http(s) only, private/loopback/link-local/reserved IPs blocked, 6-hop redirect cap with re-validation, 2 MB cap, content-type restriction |
| Prompt injection | All third-party text fenced as `UNTRUSTED EVIDENCE` with a system-prompt context policy |
| Code writes | Preview never writes; apply needs approval, a fresh single-use token, unchanged hashes, and passing verification |
| Path containment | Canonical resolution, symlink-escape rejection, extension allowlist, approved-target enforcement |
| Atomicity | Temp file, fsync, `os.replace`, plus pre-write backups |
| Jira write | Disabled by default; requires credentials, flag, valid key, allowed points, and explicit confirmation |
| Model authority | The model never decides policy, never computes story points, and has no filesystem or network access |
| Privacy | The ledger stores operational metadata only, for a bounded retention period |
| Markdown | Sanitized allowlist before assignment to `innerHTML` |

### 13.1 Production hardening

The current design is a trusted, single-user, loopback application. The API has no authentication,
authorization, tenancy, rate limiting, or malware scanning. Before exposing it beyond loopback, add
authenticated principals, per-user authorization and storage isolation, request and rate limits, a
hardened egress policy, upload scanning, secret management, TLS, audit retention, and a production
database. There is no packaged installer and nothing supervises the Python process.

---

## 14. Failure behaviour

| Condition | Required behaviour |
| --- | --- |
| Model not licensed / gated repo | Actionable message naming the license acceptance step |
| `HF_TOKEN` missing | Explicit message naming the variable |
| Model load failure | Readable `load_error` on health and system status |
| Slow CPU prefill | Periodic `status` frames; never silence |
| Research failure or empty results | Honest "live data unavailable" context; never invention; turn continues |
| Optional media missing | `media_warning`; completed text preserved |
| Structured output invalid | One targeted repair; Estimate Code then degrades to heuristics |
| Smart Code verification failure | `can_apply=false`; stage marked failed; no write |
| Stale preview token or changed file | Apply rejected with 409 |
| Phoenix unreachable | Tracing skipped after a 0.3 s probe; app unaffected |
| Renderer exception | Error boundary shows message, stack, and recovery actions |
| API unreachable | System chip shows "API offline"; screens surface an error banner |

---

## 15. Build, run, and verify

```powershell
.\scripts\setup.ps1                                      # venv, pip install -e ".[dev,image]", npm install
.\scripts\start-backend.ps1                              # uvicorn on 127.0.0.1:8765
cd frontend; npm run dev                                 # Vite on 5173, opens the browser

.\.venv\Scripts\python.exe -m ruff check backend tests   # line-length 100, py311
.\.venv\Scripts\python.exe -m pytest                     # asyncio_mode=auto
cd frontend; npm run lint                                # eslint --max-warnings=0
cd frontend; npm run build                               # tsc -b && vite build
cd frontend; npm run preview                             # serve dist/
```

### 15.1 Acceptance criteria

1. Health and system status respond before any model load and report load errors.
2. Chat streams tokens, persists both turns, and shows them after reload.
3. A brand-new conversation's first assistant reply appears and survives reload.
4. Chat routing reports the phrase that selected the workflow.
5. Research failure yields an honest answer rather than an exception or an invention.
6. Research success exposes citable source URLs in the evidence panel.
7. Uploads enforce the extension allowlist and the 25 MB cap.
8. Talk reconnects after a backend restart, and typed mode works without voice extras.
9. A voice turn transitions listening → thinking → speaking → idle when extras exist.
10. Smart Code preview leaves disk unchanged, shows a diff, and rejects traversal.
11. Smart Code marks repository content untrusted and reports budget truncation.
12. Smart Code detects a target modified between preview and apply.
13. Smart Code apply creates a backup and evidence, and cannot reuse the token.
14. Smart Code stages advance progressively and render failure as failure.
15. Estimate returns all 16 factors, a ledger whose deltas reconcile to the adjusted score, and a
    valid Fibonacci value.
16. Estimate reproduces the framework's four published §12 walkthroughs.
17. Estimate escalates on every gate, and degrades to heuristics rather than failing.
18. Estimate import caps execution at 100 mapped stories and keeps per-item errors visible.
19. The Jira write button is absent unless configured and enabled, and prompts before writing.
20. A render error shows the error boundary, not a blank page.
21. The application runs in an ordinary browser with no native bridge present.
22. A submitted request returns a job id immediately, without holding the connection.
23. A job runs to completion and retains its output and result with no client attached.
24. Reloading mid-run reattaches and resumes the answer without duplicating text or events.
25. Home reports the active job count, and Activity lists running and finished requests with
    their responses and evidence.
26. The unload guard fires while work is in flight and stays silent when idle.
27. Cancelling stops the job server-side and preserves partial output.
28. A restart marks orphaned jobs `interrupted` rather than leaving them `running`.
29. Two workers racing on the same database cannot claim the same job.

### 15.2 Regression suite

`tests/` covers the API surface, chat routing and research degradation, Talk routing and research,
harness context assembly and the ledger, structured-output repair, Smart Code preview/apply safety
and prompt-injection marking, Estimate Code behaviour, and the estimation framework's own worked
examples. All tests, `ruff`, `eslint`, and `tsc -b && vite build` MUST pass before a change is
considered complete.
