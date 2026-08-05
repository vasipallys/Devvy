import asyncio
import json
import logging
import mimetypes
import shutil
from importlib.util import find_spec
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, HumanMessage
from sqlmodel import Session, select

from backend.agent import ChatAgent
from backend.agent_graph import TalkAgentGraph
from backend.animation_engine import AnimationEngine
from backend.config import get_settings
from backend.db import (
    Conversation, Message, create_conversation, engine, init_db, list_messages, now, utc_iso,
)
from backend.estimate_code import (
    BatchEstimateRequest,
    EstimateRequest,
    EstimateService,
    JiraWriteRequest,
    Story,
    jira_issues,
    parse_upload as parse_estimate_upload,
    rows_to_stories,
    write_jira_points,
)
from backend.estimate_history import (
    clear_estimates,
    delete_estimate,
    estimate_stats,
    get_estimate,
    list_estimates,
    save_estimate,
)
from backend.estimation_framework import (
    FACTORS,
    FIBONACCI_POINTS,
    FRAMEWORK_DOCUMENT,
    FRAMEWORK_VERSION,
    MATURITY_TAXONOMY,
    StackProfile,
)
from backend.harness import RunLedger
from backend.jobs import FINISHED as FINISHED_JOB_STATES, JobContext, JobRunner
from backend.model import GemmaRuntime
from backend.observability import configure_observability
from backend.schemas import ChatRequest, RenameRequest
from backend.smart_code import SmartCodeApplyRequest, SmartCodeRequest, SmartCodeService
from backend.tools import extract_document
from backend.voice_engine import VoiceEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)
settings = get_settings()
runtime = GemmaRuntime(settings)
agent = ChatAgent(runtime, settings)
talk_agent = TalkAgentGraph(runtime, settings)
voice_engine = VoiceEngine(settings)
animation_engine = AnimationEngine(settings)
smart_code_service = SmartCodeService(runtime, settings)
estimate_service = EstimateService(runtime, settings)
run_ledger = RunLedger(settings.app_data_dir, settings.agent_run_retention_days)
job_runner = JobRunner(engine, retention_days=settings.job_retention_days)


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    configure_observability(settings, application)
    # The worker owns all model execution. Starting it here — not per request — is what
    # lets a run outlive the connection that submitted it.
    await job_runner.start()
    try:
        yield
    finally:
        await job_runner.stop()


app = FastAPI(title=f"{settings.app_name} API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    # Any loopback origin is accepted so the dev server can move ports. The literal "null"
    # origin is deliberately not allowed: it was only ever needed by the Electron renderer
    # loading over file://, and it would otherwise match sandboxed iframes and local files.
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/generated", StaticFiles(directory=settings.generated_dir), name="generated")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def message_dict(item: Message) -> dict:
    return {
        "id": str(item.id), "role": item.role, "content": item.content,
        "created_at": utc_iso(item.created_at), "attachments": item.attachments,
        "metadata": item.message_metadata,
    }


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "model": settings.model_id,
        "model_loaded": runtime.loaded,
        "model_error": runtime.load_error,
    }


@app.get("/api/system/status")
def system_status():
    """Public, secret-free capability and trust metadata for evidence-led UI."""
    return {
        "app": {"name": settings.app_name, "version": app.version, "deployment": "local-desktop"},
        "model": {
            "id": settings.model_id,
            "loaded": runtime.loaded,
            "error": runtime.load_error,
            "device": settings.model_device,
            "dtype": settings.model_dtype,
            "generation": "serialized",
        },
        "capabilities": {
            "chat": True,
            "research": True,
            "image": bool(settings.image_model_id and find_spec("diffusers")),
            "speech_to_text": bool(find_spec("faster_whisper")),
            "text_to_speech": bool(find_spec("pyttsx3")),
            "visual_explanations": bool(shutil.which(settings.manim_executable)),
            "jira_read": bool(
                settings.jira_base_url and settings.jira_email and settings.jira_api_token
            ),
            "jira_write": bool(settings.jira_write_enabled),
        },
        "trust": {
            "privacy": "Local-first",
            "data_dir": str(settings.app_data_dir.resolve()),
            "network": ["Explicit web research", "Optional Jira", "Optional Phoenix traces"],
            "run_ledger": str(run_ledger.directory.resolve()),
            "run_retention_days": settings.agent_run_retention_days,
        },
        "limits": {
            "upload_mb": 25,
            "attachments": 10,
            "context_characters": settings.document_max_chars,
            "smart_code_context_characters": settings.smart_code_max_context_chars,
        },
    }


@app.get("/api/conversations")
def conversations():
    with Session(engine) as session:
        items = session.exec(select(Conversation).order_by(Conversation.updated_at.desc())).all()
        return items


@app.post("/api/conversations")
def new_conversation():
    with Session(engine) as session:
        return create_conversation(session)


@app.get("/api/conversations/{conversation_id}/messages")
def messages(conversation_id: UUID):
    with Session(engine) as session:
        if not session.get(Conversation, conversation_id):
            raise HTTPException(404, "Conversation not found")
        return [message_dict(item) for item in list_messages(session, conversation_id)]


@app.patch("/api/conversations/{conversation_id}")
def rename(conversation_id: UUID, payload: RenameRequest):
    with Session(engine) as session:
        item = session.get(Conversation, conversation_id)
        if not item:
            raise HTTPException(404, "Conversation not found")
        item.title = payload.title.strip()
        item.updated_at = now()
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID):
    with Session(engine) as session:
        for item in list_messages(session, conversation_id):
            session.delete(item)
        conversation = session.get(Conversation, conversation_id)
        if conversation:
            session.delete(conversation)
        session.commit()


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "Filename is required")
    extension = Path(file.filename).suffix.lower()
    if extension not in {".pdf", ".docx", ".txt", ".md", ".py", ".js", ".ts", ".json", ".csv"}:
        raise HTTPException(415, "Unsupported file type")
    upload_id = str(uuid4())
    destination = settings.uploads_dir / f"{upload_id}{extension}"
    size = 0
    try:
        with destination.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 25 * 1024 * 1024:
                    raise HTTPException(413, "File exceeds 25 MB")
                target.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {"id": upload_id, "name": file.filename, "content_type": file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream", "size": destination.stat().st_size}


def attachment_data(ids: list[str]) -> tuple[list[dict], str]:
    attachments, contexts = [], []
    for upload_id in ids:
        try:
            safe_id = str(UUID(str(upload_id)))
        except (TypeError, ValueError, AttributeError):
            continue
        matches = list(settings.uploads_dir.glob(f"{safe_id}.*"))
        if not matches:
            continue
        path = matches[0]
        attachments.append({"id": upload_id, "name": path.name, "content_type": mimetypes.guess_type(path)[0] or "application/octet-stream", "size": path.stat().st_size})
        contexts.append(
            f"DOCUMENT {path.name}:\n"
            f"{extract_document(path, max_chars=settings.document_max_chars)}"
        )
    return attachments, "\n\n".join(contexts)


async def drain_tokens(generation: asyncio.Task, queue: asyncio.Queue[str], context: JobContext) -> bool:
    """Relay tokens to the job while reporting progress during CPU prefill.

    Returns whether anything streamed, so callers can emit tool-only answers that bypass
    the LLM stream as a single token.
    """
    streamed = False
    elapsed = 0
    while not generation.done() or not queue.empty():
        try:
            await context.token(await asyncio.wait_for(queue.get(), timeout=2))
            streamed = True
        except TimeoutError:
            elapsed += 2
            await context.progress(
                "Preparing the local model…" if elapsed < 10 else f"Generating on CPU… {elapsed}s"
            )
    return streamed


async def run_chat_job(request: dict, context: JobContext) -> dict:
    """Generate and persist one assistant turn. Owned by the worker, not by a connection."""
    conversation_id = UUID(request["conversation_id"])
    mode = request["mode"]
    message = request["message"]
    attachment_context = request.get("attachment_context", "")
    run = run_ledger.start(
        "chat",
        metadata={
            "requested_mode": mode,
            "attachments": request.get("attachment_count", 0),
            "model": settings.model_id,
        },
    )
    try:
        with Session(engine) as session:
            prior = [
                item for item in list_messages(session, conversation_id) if item.role != "system"
            ]
        # The submitted message is already persisted, so drop it from the replayed history.
        history = [
            AIMessage(content=item.content) if item.role == "assistant"
            else HumanMessage(content=item.content)
            for item in prior[-21:-1]
        ]
        await context.event(
            "context", "completed", "Context assembled",
            evidence={
                "history_messages": len(history),
                "attachments": request.get("attachment_count", 0),
                "characters": len(attachment_context),
                "budget": settings.document_max_chars,
            },
        )
        await context.event(
            "route", "running", "Selecting the safest workflow", detail=f"Requested mode: {mode}"
        )
        token_queue: asyncio.Queue[str] = asyncio.Queue()
        generation = asyncio.create_task(
            agent.invoke(history, message, mode, attachment_context, token_queue)
        )
        streamed = await drain_tokens(generation, token_queue, context)
        result = await generation

        await context.event(
            "route", "completed",
            f"{str(result.get('mode') or mode).title()} workflow selected",
            detail=result.get("route_reason") or None,
            evidence={"mode": result.get("mode") or mode},
        )
        sources = result.get("sources") or []
        if sources or result.get("research_failed"):
            await context.event(
                "research",
                "failed" if result.get("research_failed") else "completed",
                (
                    "Live research unavailable — the answer must say so"
                    if result.get("research_failed")
                    else f"{len(sources)} public source(s) retrieved"
                ),
                evidence={
                    "sources": [item["url"] for item in sources if item.get("url")],
                    "titles": [item["title"] for item in sources],
                    "retrieved_characters": sum(int(item["characters"]) for item in sources),
                },
            )
        answer = str(result["messages"][-1].content)
        if not streamed:
            await context.token(answer)
        with Session(engine) as session:
            saved = Message(
                conversation_id=conversation_id, role="assistant", content=answer,
                message_metadata={
                    "mode": result.get("mode"), "artifact_url": result.get("artifact_url")
                },
            )
            session.add(saved)
            conversation = session.get(Conversation, conversation_id)
            if conversation:
                conversation.updated_at = now()
                session.add(conversation)
            session.commit()
            session.refresh(saved)
            payload = message_dict(saved)
        await context.event(
            "finalize", "completed", "Response persisted locally",
            evidence={
                "response_characters": len(answer),
                "context_sources": result.get("context_manifest", []),
                "artifact": bool(result.get("artifact_url")),
            },
        )
        run.finish(
            "completed",
            summary={"mode": result.get("mode"), "artifact": bool(result.get("artifact_url"))},
        )
        return {
            "message": payload,
            "mode": result.get("mode"),
            "artifact_url": result.get("artifact_url"),
        }
    except asyncio.CancelledError:
        run.event("cancelled", "failed", "The run was cancelled by the user")
        run.finish("cancelled")
        raise
    except Exception as exc:
        run.event("error", "failed", "The run could not complete", detail=str(exc)[:500])
        run.finish("failed", summary={"error_type": type(exc).__name__})
        await context.event("error", "failed", "The run could not complete", detail=str(exc)[:500])
        raise


job_runner.register("chat", run_chat_job)


@app.post("/api/chat/jobs", status_code=202)
def submit_chat(payload: ChatRequest):
    """Accept a chat turn and return immediately with a job to follow.

    The user message and conversation are persisted before the response is sent, so the UI
    can render the turn instantly and the work is recoverable even if the client never
    reconnects.
    """
    conversation_id = payload.conversation_id
    with Session(engine) as session:
        conversation = session.get(Conversation, conversation_id) if conversation_id else None
        if not conversation:
            conversation = create_conversation(session, payload.message[:60])
        conversation_id = conversation.id
        attachments, attachment_context = attachment_data(payload.attachment_ids)
        user_message = Message(
            conversation_id=conversation_id, role="user",
            content=payload.message, attachments=attachments,
        )
        session.add(user_message)
        conversation.updated_at = now()
        session.add(conversation)
        session.commit()
        session.refresh(user_message)
        user_payload = message_dict(user_message)

    job = job_runner.submit(
        "chat",
        payload.message[:120],
        {
            "conversation_id": str(conversation_id),
            "message": payload.message,
            "mode": payload.mode,
            "attachment_context": attachment_context,
            "attachment_count": len(attachments),
        },
        conversation_id=conversation_id,
    )
    return {
        "job_id": str(job.id),
        "conversation_id": str(conversation_id),
        "message": user_payload,
        "model": settings.model_id,
        "local": True,
    }


# --------------------------------------------------------------------------------------
# Job surface: the only way a client observes work. Nothing here drives execution.
# --------------------------------------------------------------------------------------


@app.get("/api/jobs")
def list_jobs(
    limit: int = Query(default=50, ge=1, le=200),
    kind: str | None = Query(default=None, max_length=20),
):
    """Everything the activity view needs on reopening the browser."""
    return {"jobs": job_runner.list(limit=limit, kind=kind), "active": job_runner.active_count()}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: UUID):
    detail = job_runner.snapshot(job_id)
    if detail is None:
        raise HTTPException(404, "Job not found")
    return detail


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: UUID):
    if not job_runner.cancel(job_id):
        raise HTTPException(409, "That request has already finished.")
    return {"status": "cancelling", "job_id": str(job_id)}


@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: UUID):
    """Attach to a job: a full snapshot, then live deltas until it finishes.

    Subscribing happens *before* the snapshot is taken, so no event can fall between the
    two. Deltas already contained in the snapshot are filtered by the client using each
    token's offset, which makes attaching at any point safe and repeatable.
    """
    queue = job_runner.subscribe(job_id)
    snapshot = job_runner.snapshot(job_id)
    if snapshot is None:
        job_runner.unsubscribe(job_id, queue)
        raise HTTPException(404, "Job not found")

    async def events():
        try:
            yield sse("snapshot", snapshot)
            if snapshot["status"] in FINISHED_JOB_STATES:
                yield sse("done", {"status": snapshot["status"], "result": snapshot["result"]})
                return
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    # Keeps proxies and idle browsers from dropping a long, quiet run.
                    yield sse("heartbeat", {"job_id": str(job_id)})
                    continue
                yield sse(message.get("type", "message"), message)
                if message.get("type") == "done":
                    return
        finally:
            job_runner.unsubscribe(job_id, queue)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def run_talk_job(request: dict, context: JobContext) -> dict:
    """One spoken turn: generate, then produce optional audio and video.

    Runs on the worker like every other request, so closing the tab mid-sentence still
    finishes the answer and the media. The socket is only a listener.
    """
    transcript = request["transcript"]
    mode = request["mode"]
    history = [
        AIMessage(content=item["content"]) if item["role"] == "assistant"
        else HumanMessage(content=item["content"])
        for item in request.get("history", [])
    ]
    run = run_ledger.start(
        "talk",
        metadata={
            "mode": mode,
            "attachments": request.get("attachment_count", 0),
            "model": settings.model_id,
        },
    )
    try:
        await context.event(
            "context", "completed", "Turn context prepared locally",
            evidence={
                "history_messages": len(history),
                "attachments": request.get("attachment_count", 0),
            },
        )
        await context.event("generate", "running", "Devvy is composing a grounded response")
        token_queue: asyncio.Queue[str] = asyncio.Queue()
        if mode == "talk":
            generation = asyncio.create_task(
                talk_agent.invoke(history, transcript, {}, token_queue)
            )
        else:
            generation = asyncio.create_task(
                agent.invoke(
                    history, transcript, mode, request.get("attachment_context", ""), token_queue
                )
            )
        await drain_tokens(generation, token_queue, context)
        result = await generation
        response = result["response"] if mode == "talk" else str(result["messages"][-1].content)

        if result.get("route_reason"):
            await context.event(
                "route", "completed", "Turn routed", detail=str(result["route_reason"]),
                evidence={
                    "research": bool(result.get("requires_research")),
                    "animation": bool(result.get("requires_animation")),
                },
            )
        sources = result.get("sources") or []
        if sources or result.get("research_failed"):
            await context.event(
                "research",
                "failed" if result.get("research_failed") else "completed",
                (
                    "Live research unavailable — the answer must say so"
                    if result.get("research_failed")
                    else f"{len(sources)} public source(s) retrieved"
                ),
                evidence={
                    "sources": [item["url"] for item in sources if item.get("url")],
                    "titles": [item["title"] for item in sources],
                },
            )
        await context.event(
            "generate", "completed", "Response completed",
            evidence={
                "response_characters": len(response),
                "context_sources": result.get("context_manifest", []),
            },
        )
        job_result: dict[str, Any] = {
            "response": response,
            "mode": mode,
            "artifact_url": result.get("artifact_url"),
            "audio_url": None,
            "video_url": None,
            "warnings": [],
        }

        # Media is best-effort: a TTS or Manim failure MUST NOT discard completed text.
        tts_task = asyncio.create_task(voice_engine.synthesize(response))
        animation_task = None
        if mode == "talk" and result.get("requires_animation"):
            animation_task = asyncio.create_task(
                animation_engine.render(transcript[:60], response)
            )
        try:
            job_result["audio_url"] = await tts_task
        except Exception as exc:
            logger.warning("Talk TTS failed: %s", exc)
            job_result["warnings"].append(str(exc))
        if animation_task:
            try:
                job_result["video_url"] = await animation_task
            except Exception as exc:
                logger.warning("Talk animation failed: %s", exc)
                job_result["warnings"].append(str(exc))

        await context.event(
            "media", "completed", "Optional media processing finished",
            evidence={
                "audio": bool(job_result["audio_url"]),
                "animation": bool(job_result["video_url"]),
                "warnings": len(job_result["warnings"]),
            },
        )
        run.finish("completed", summary={"mode": mode})
        return job_result
    except asyncio.CancelledError:
        run.finish("cancelled")
        raise
    except Exception as exc:
        run.event("error", "failed", "The Talk turn could not complete", detail=str(exc)[:500])
        run.finish("failed", summary={"error_type": type(exc).__name__})
        raise


job_runner.register("talk", run_talk_job)


@app.websocket("/api/talk/ws")
async def talk_socket(websocket: WebSocket):
    await websocket.accept()
    audio_buffer = bytearray()
    history: list[dict[str, str]] = []

    async def send(event_type: str, **data):
        await websocket.send_json({"type": event_type, **data})

    async def respond(
        transcript: str,
        mode: str = "chat",
        attachment_ids: list[str] | None = None,
    ):
        """Submit the turn as a job, then relay its events onto the Talk protocol."""
        nonlocal history
        if not transcript.strip():
            await send("error", message="I could not hear any speech. Please try again.")
            await send("state", value="idle")
            return
        if mode != "talk" and mode not in {"auto", "chat", "code", "research", "image", "document"}:
            raise ValueError("Unsupported Talk mode")
        requested_ids = attachment_ids or []
        if len(requested_ids) > 10:
            raise ValueError("Talk messages support up to 10 attachments")
        attachment_context = ""
        if mode != "talk" and requested_ids:
            attachments, attachment_context = attachment_data(requested_ids)
            if len(attachments) != len(requested_ids):
                raise ValueError("One or more selected attachments are no longer available")

        await send("transcript", content=transcript)
        await send("state", value="thinking")
        job = job_runner.submit(
            "talk",
            transcript[:120],
            {
                "transcript": transcript,
                "mode": mode,
                "history": history[-settings.model_context_messages:],
                "attachment_context": attachment_context,
                "attachment_count": len(requested_ids),
            },
        )
        await send("job_started", job_id=str(job.id))
        queue = job_runner.subscribe(job.id)
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=1)
                except TimeoutError:
                    await send("heartbeat")
                    continue
                kind = message.get("type")
                if kind == "token":
                    await send("token", content=message["content"])
                elif kind == "agent_event":
                    await send("agent_event", **{k: v for k, v in message.items() if k != "type"})
                elif kind == "status":
                    await send("status", content=message.get("message", ""))
                elif kind == "done":
                    break
        finally:
            job_runner.unsubscribe(job.id, queue)

        detail = job_runner.get(job.id) or {}
        if detail.get("status") != "succeeded":
            await send("error", message=detail.get("error") or "The turn could not complete.")
            await send("state", value="idle")
            return
        result = detail.get("result") or {}
        response = str(result.get("response", ""))
        history = (history + [
            {"role": "user", "content": transcript},
            {"role": "assistant", "content": response},
        ])[-settings.model_context_messages:]

        await send("text_complete", content=response)
        if result.get("artifact_url"):
            await send("image_ready", url=result["artifact_url"])
        for warning in result.get("warnings") or []:
            await send("media_warning", message=warning)
        if result.get("audio_url"):
            await send("state", value="speaking")
            await send("audio_ready", url=result["audio_url"])
        if result.get("video_url"):
            await send("video_ready", url=result["video_url"])
        if not result.get("audio_url"):
            await send("state", value="idle")

    try:
        await send("state", value="idle")
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                audio_buffer.extend(message["bytes"])
                continue
            raw = message.get("text")
            if raw is None:
                continue
            command = json.loads(raw)
            if command.get("type") == "text":
                await respond(
                    str(command.get("content", ""))[:100_000],
                    str(command.get("mode", "auto")),
                    command.get("attachment_ids")
                    if isinstance(command.get("attachment_ids"), list)
                    else [],
                )
            elif command.get("type") == "commit":
                if not audio_buffer:
                    await send("error", message="No microphone audio was received")
                    continue
                suffix = ".webm" if "webm" in command.get("mime", "") else ".wav"
                path = settings.uploads_dir / f"voice-{uuid4()}{suffix}"
                path.write_bytes(audio_buffer)
                audio_buffer.clear()
                await send("state", value="thinking")
                await send("status", content="Transcribing locally with Whisper…")
                try:
                    transcript = await voice_engine.transcribe(path)
                    await respond(transcript, "talk")
                finally:
                    path.unlink(missing_ok=True)
            elif command.get("type") == "reset":
                history = []
                await send("reset_complete")
    except WebSocketDisconnect:
        logger.info("Talk client disconnected")
    except RuntimeError as exc:
        if "disconnect message has been received" in str(exc):
            logger.info("Talk client disconnected")
            return
        raise
    except Exception as exc:
        # The turn itself records its own failure through the job; this only covers socket
        # and protocol errors, which the client still needs to see.
        logger.exception("Talk session failed")
        try:
            await send("error", message=str(exc))
            await send("state", value="error")
        except Exception:
            pass


#: Pipeline checkpoints the Smart Code screen renders, in order. `classify` is satisfied by
#: request validation and `gate` by the human-approval step, so neither comes from the service.
SMART_CODE_STAGES = ("classify", "retrieve", "plan", "code", "verify", "critique", "gate")


async def run_smart_code_job(request: dict, context: JobContext) -> dict:
    payload = SmartCodeRequest.model_validate(request)
    run = run_ledger.start(
        "smart-code",
        metadata={
            "mode": payload.mode, "risk": payload.risk, "targets": len(payload.target_paths),
        },
    )
    progress_queue: asyncio.Queue[dict] = asyncio.Queue()

    def progress(event: dict):
        progress_queue.put_nowait(event)

    task = asyncio.create_task(smart_code_service.preview(payload, progress))
    elapsed = 0
    emitted: set[str] = {"classify"}
    stages: dict[str, str] = {"classify": "completed"}
    try:
        while not task.done() or not progress_queue.empty():
            try:
                event = await asyncio.wait_for(progress_queue.get(), timeout=2)
                stage = str(event.get("stage", "generate"))
                status = str(event.get("status", "running"))
                run.event(
                    stage, status, str(event.get("label", "Agent workflow update")),
                    detail=event.get("detail"), evidence=event.get("evidence"),
                )
                await context.event(
                    stage, status, str(event.get("label", "Agent workflow update")),
                    detail=event.get("detail"), evidence=event.get("evidence"),
                )
                # Forward pipeline stages the moment the service reaches them, so a
                # multi-minute CPU generation shows real movement instead of a frozen
                # checklist that fills in all at once at the end.
                if stage in SMART_CODE_STAGES and status != "running":
                    emitted.add(stage)
                    stages[stage] = status
            except TimeoutError:
                elapsed += 2
                await context.progress(
                    f"Devvy is planning and coding locally ({elapsed}s)"
                    if "plan" not in emitted
                    else f"Devvy is verifying the proposed change ({elapsed}s)"
                )
        result = await task
        for stage in SMART_CODE_STAGES:
            stages.setdefault(stage, "completed")
        await context.event(
            "gate",
            "waiting" if result.get("can_apply") else "completed",
            "Waiting for explicit human approval" if result.get("can_apply")
            else "No write action available",
            evidence={
                "can_apply": result.get("can_apply"), "edits": len(result.get("edits", []))
            },
        )
        run.finish(
            "completed",
            summary={"can_apply": result.get("can_apply"), "edits": len(result.get("edits", []))},
        )
        return {"preview": result, "stages": stages}
    except asyncio.CancelledError:
        task.cancel()
        run.finish("cancelled")
        raise
    except ValueError as exc:
        logger.warning("Smart Code preview rejected: %s", exc)
        run.event("error", "failed", "Preview rejected safely", detail=str(exc)[:500])
        run.finish("failed", summary={"error_type": type(exc).__name__})
        raise
    except Exception as exc:
        logger.exception("Smart Code preview failed")
        run.event("error", "failed", "Preview failed safely", detail=str(exc)[:500])
        run.finish("failed", summary={"error_type": type(exc).__name__})
        raise


job_runner.register("smart-code", run_smart_code_job)


@app.post("/api/smart-code/jobs", status_code=202)
def submit_smart_code(payload: SmartCodeRequest):
    job = job_runner.submit(
        "smart-code", payload.objective[:120], payload.model_dump(mode="json")
    )
    return {"job_id": str(job.id)}


@app.post("/api/smart-code/apply")
def smart_code_apply(payload: SmartCodeApplyRequest):
    try:
        return smart_code_service.apply(payload)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/estimate-code/config")
def estimate_code_config():
    """Everything the estimation UI needs to render the framework without hardcoding it.

    The factor rubric, maturity taxonomy, and Fibonacci bands are served from the same
    definitions the calculation uses, so the screen can never drift from the engine.
    """
    return {
        "model": settings.model_id,
        "jira_configured": bool(
            settings.jira_base_url and settings.jira_email and settings.jira_api_token
        ),
        "jira_write_enabled": settings.jira_write_enabled,
        "framework": {
            "name": "Agile Story Point Estimation Framework",
            "version": FRAMEWORK_VERSION,
            "document": FRAMEWORK_DOCUMENT,
            "fibonacci": list(FIBONACCI_POINTS),
        },
        "factors": [factor.model_dump() for factor in FACTORS],
        "maturity_levels": [
            {"level": level, **{key: value for key, value in data.items()}}
            for level, data in sorted(MATURITY_TAXONOMY.items(), reverse=True)
        ],
        "stacks": {
            "frontend": [
                {"id": "none", "label": "None"},
                {"id": "react", "label": "ReactJS"},
                {"id": "angular", "label": "Angular"},
                {"id": "other", "label": "Other"},
            ],
            "backend": [
                {"id": "none", "label": "None"},
                {"id": "spring_boot", "label": "Spring Boot"},
                {"id": "fastapi", "label": "FastAPI"},
                {"id": "flask", "label": "Flask"},
                {"id": "other", "label": "Other"},
            ],
            "scenarios": [
                {"id": "standard", "label": "Existing framework"},
                {"id": "new_framework", "label": "New framework (first 3 sprints)"},
                {"id": "framework_upgrade", "label": "Major framework upgrade"},
                {"id": "framework_migration", "label": "Framework migration"},
            ],
        },
    }


#: Which pipeline checkpoints each service progress stage completes. Emitting these as the
#: work happens — rather than in a burst at the end — is what makes the several-minute CPU
#: generation legible: the user watches context and calibration tick off immediately, then
#: sees the arithmetic land the moment the model returns.
ESTIMATE_NODES: dict[str, tuple[str, ...]] = {
    "normalize": ("normalize",),
    "readiness": ("readiness",),
    "assemble_context": ("assemble_context",),
    "declare_stack": ("declare_stack",),
    "specialist_routing": ("specialist_routing",),
    "specialist_analysis": ("specialist_analysis",),
    "primary_estimate": ("primary_estimate",),
    "blind_review": ("blind_review",),
    "disagreement": ("disagreement",),
    "critic": ("critic",),
    "arbitration": ("arbitration",),
    "score_factors": ("score_factors",),
    "calculate": ("apply_base_adjustments", "apply_stack_adjustments", "map_to_fibonacci"),
    "policy_gate": ("evaluate_gates", "decide"),
    "consistency_audit": ("consistency_audit",),
    "human_review": ("human_review",),
}


ESTIMATE_NODE_ORDER = (
    "normalize", "readiness", "assemble_context", "declare_stack", "specialist_routing",
    "primary_estimate", "specialist_analysis", "blind_review", "disagreement", "critic", "arbitration",
    "score_factors", "apply_base_adjustments", "apply_stack_adjustments",
    "map_to_fibonacci", "evaluate_gates", "decide", "consistency_audit", "human_review",
)


async def estimate_one(story, context: JobContext, index: int, total: int) -> dict:
    """Estimate a single story, reporting progress against the shared job context."""
    run = run_ledger.start(
        "estimate-code", metadata={"source": story.source, "model": settings.model_id}
    )
    progress_queue: asyncio.Queue[dict] = asyncio.Queue()

    def progress(event: dict):
        progress_queue.put_nowait(event)

    prefix = f"[{index + 1}/{total}] " if total > 1 else ""
    task = asyncio.create_task(estimate_service.estimate(story, progress))
    elapsed = 0
    emitted: set[str] = set()
    try:
        while not task.done() or not progress_queue.empty():
            try:
                event = await asyncio.wait_for(progress_queue.get(), timeout=2)
                stage = str(event.get("stage", "estimate"))
                status = str(event.get("status", "running"))
                label = str(event.get("label", "Estimation workflow update"))
                run.event(
                    stage, status, label,
                    detail=event.get("detail"), evidence=event.get("evidence"),
                )
                await context.event(
                    stage, status, prefix + label,
                    detail=event.get("detail"),
                    evidence={**(event.get("evidence") or {}), "story_index": index},
                )
                if status in {"completed", "validated"}:
                    emitted.update(ESTIMATE_NODES.get(stage, ()))
            except TimeoutError:
                elapsed += 2
                await context.progress(
                    prefix
                    + (
                        "Devvy is running independent evidence assessments on CPU"
                        if "blind_review" not in emitted
                        else "Devvy is applying the framework arithmetic"
                    )
                    + f" ({elapsed}s)"
                )
        result = await task
        run.finish(
            "completed",
            summary={
                "points": result.get("points"),
                "source": story.source,
                "recommendation": result.get("recommendation"),
                "confidence": result.get("confidence"),
            },
        )
        try:
            record = await asyncio.to_thread(
                save_estimate, engine, result, context.job_id
            )
            result["history_id"] = str(record.id)
        except Exception:
            # History is a side effect of estimating. Losing it must never cost the user
            # the estimate they are waiting for.
            logger.exception("Could not record the estimate in history")
        return result
    except asyncio.CancelledError:
        task.cancel()
        run.finish("cancelled")
        raise
    except Exception as exc:
        logger.exception("Estimate Code failed")
        run.event("error", "failed", "Estimate failed validation", detail=str(exc)[:500])
        run.finish("failed", summary={"error_type": type(exc).__name__})
        raise


async def run_estimate_job(request: dict, context: JobContext) -> dict:
    """One job covers a single story or a whole batch; stories run sequentially."""
    stories = [Story.model_validate(item) for item in request["stories"]]
    total = len(stories)
    results: list[dict] = []
    failures: list[dict] = []
    for index, story in enumerate(stories):
        try:
            results.append(await estimate_one(story, context, index, total))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A batch must not lose the stories that already succeeded.
            failures.append({"index": index, "title": story.title, "error": str(exc)[:500]})
            await context.event(
                "estimate", "failed", f"{story.title} could not be estimated",
                detail=str(exc)[:500], evidence={"story_index": index},
            )
            if total == 1:
                raise
    return {
        "results": results,
        "failures": failures,
        "nodes": list(ESTIMATE_NODE_ORDER),
        "count": total,
    }


job_runner.register("estimate", run_estimate_job)


def submit_estimate_job(stories: list) -> dict:
    title = stories[0].title if len(stories) == 1 else f"{len(stories)} stories"
    job = job_runner.submit(
        "estimate", title, {"stories": [item.model_dump(mode="json") for item in stories]}
    )
    return {"job_id": str(job.id), "count": len(stories)}


@app.post("/api/estimate-code/jobs", status_code=202)
def submit_estimate(payload: EstimateRequest):
    return submit_estimate_job([payload.story])


@app.post("/api/estimate-code/batch-jobs", status_code=202)
def submit_estimate_batch(payload: BatchEstimateRequest):
    return submit_estimate_job(list(payload.stories))


@app.get("/api/estimate-code/history")
def estimate_history(
    query: str = Query(default="", max_length=200),
    source: str | None = Query(default=None, max_length=20),
    points: int | None = Query(default=None, ge=1, le=34),
    recommendation: str | None = Query(default=None, max_length=40),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    """Searchable record of every completed estimate, newest first."""
    return list_estimates(
        engine, query=query, source=source, points=points,
        recommendation=recommendation, limit=limit, offset=offset,
    )


@app.get("/api/estimate-code/history/stats")
def estimate_history_stats():
    """Aggregates that let a team see how they estimate, not just what they estimated."""
    return estimate_stats(engine)


@app.get("/api/estimate-code/history/{record_id}")
def estimate_history_detail(record_id: UUID):
    record = get_estimate(engine, record_id)
    if record is None:
        raise HTTPException(404, "That estimate is no longer in history.")
    return record


@app.delete("/api/estimate-code/history/{record_id}", status_code=204)
def estimate_history_delete(record_id: UUID):
    if not delete_estimate(engine, record_id):
        raise HTTPException(404, "That estimate is no longer in history.")


@app.post("/api/estimate-code/history/clear")
def estimate_history_clear(payload: dict = Body(default={})):
    if not payload.get("confirm"):
        raise HTTPException(400, "Explicit confirmation is required to clear history.")
    return {"deleted": clear_estimates(engine)}


@app.post("/api/estimate-code/upload/parse")
async def estimate_upload(file: UploadFile = File(...)):
    try:
        content = bytearray()
        while chunk := await file.read(1024 * 1024):
            content.extend(chunk)
            if len(content) > 15 * 1024 * 1024:
                raise ValueError("File exceeds the 15 MB upload limit.")
        return parse_estimate_upload(bytes(content), file.filename or "upload")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/estimate-code/upload/estimate", status_code=202)
def estimate_upload_rows(payload: dict = Body(...)):
    try:
        stack = StackProfile.model_validate(payload.get("stack") or {})
        stories = rows_to_stories(payload.get("rows") or [], payload.get("mapping") or {}, stack)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return submit_estimate_job(stories)


@app.get("/api/estimate-code/jira/issues")
async def estimate_jira_issues(
    project: str = Query(min_length=1, max_length=50),
    query: str = Query(default="", max_length=200),
):
    try:
        return await jira_issues(settings, project, query)
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/estimate-code/jira/{issue_key}/points")
async def estimate_jira_write(issue_key: str, payload: JiraWriteRequest):
    if not payload.confirm:
        raise HTTPException(400, "Explicit confirmation is required for Jira write-back.")
    try:
        await write_jira_points(settings, issue_key, payload.points)
    except ValueError as exc:
        raise HTTPException(403, str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"status": "updated", "issue_key": issue_key, "points": payload.points}
