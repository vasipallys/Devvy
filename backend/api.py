import asyncio
import json
import logging
import mimetypes
import shutil
from importlib.util import find_spec
from contextlib import asynccontextmanager
from pathlib import Path
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
from backend.db import Conversation, Message, create_conversation, engine, init_db, list_messages, now
from backend.estimate_code import (
    BatchEstimateRequest,
    EstimateRequest,
    EstimateService,
    JiraWriteRequest,
    jira_issues,
    parse_upload as parse_estimate_upload,
    rows_to_stories,
    write_jira_points,
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


@asynccontextmanager
async def lifespan(application: FastAPI):
    init_db()
    configure_observability(settings, application)
    yield


app = FastAPI(title=f"{settings.app_name} API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_origin_regex=r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?|null)$",
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
        "created_at": item.created_at.isoformat(), "attachments": item.attachments,
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


@app.post("/api/chat/stream")
async def chat(payload: ChatRequest):
    conversation_id = payload.conversation_id
    with Session(engine) as session:
        conversation = session.get(Conversation, conversation_id) if conversation_id else None
        if not conversation:
            conversation = create_conversation(session, payload.message[:60])
            conversation_id = conversation.id
        attachments, context = attachment_data(payload.attachment_ids)
        user_message = Message(conversation_id=conversation_id, role="user", content=payload.message, attachments=attachments)
        session.add(user_message)
        conversation.updated_at = now()
        session.add(conversation)
        session.commit()
        prior = list_messages(session, conversation_id)[:-1]
    history = [AIMessage(content=x.content) if x.role == "assistant" else HumanMessage(content=x.content) for x in prior[-20:]]
    run = run_ledger.start(
        "chat",
        metadata={"requested_mode": payload.mode, "attachments": len(attachments), "model": settings.model_id},
    )

    async def events():
        yield f"data: {json.dumps({'type': 'start', 'run_id': run.id, 'conversation_id': str(conversation_id), 'message_id': str(user_message.id), 'model': settings.model_id, 'local': True})}\n\n"
        try:
            context_event = run.event(
                "context",
                "completed",
                "Context assembled",
                evidence={
                    "history_messages": len(history),
                    "attachments": len(attachments),
                    "characters": len(context),
                    "budget": settings.document_max_chars,
                },
            )
            yield f"data: {json.dumps({'type': 'agent_event', **context_event})}\n\n"
            route_event = run.event(
                "route", "running", "Selecting the safest workflow", detail=f"Requested mode: {payload.mode}"
            )
            yield f"data: {json.dumps({'type': 'agent_event', **route_event})}\n\n"
            token_queue: asyncio.Queue[str] = asyncio.Queue()
            generation = asyncio.create_task(
                agent.invoke(history, payload.message, payload.mode, context, token_queue)
            )
            elapsed = 0
            streamed = False
            while not generation.done() or not token_queue.empty():
                try:
                    token = await asyncio.wait_for(token_queue.get(), timeout=2)
                    streamed = True
                    yield f"data: {json.dumps({'type': 'token', 'content': token})}\n\n"
                except TimeoutError:
                    elapsed += 2
                    detail = (
                        "Preparing the local model…"
                        if elapsed < 10
                        else f"Generating on CPU… {elapsed}s"
                    )
                    yield f"data: {json.dumps({'type': 'status', 'content': detail})}\n\n"
            result = await generation
            completed_route = run.event(
                "route",
                "completed",
                f"{str(result.get('mode') or payload.mode).title()} workflow selected",
                detail=result.get("route_reason") or None,
                evidence={"mode": result.get("mode") or payload.mode},
            )
            yield f"data: {json.dumps({'type': 'agent_event', **completed_route})}\n\n"
            # Research sources are the citable basis for the answer, so they are reported
            # individually rather than collapsed into a character count.
            sources = result.get("sources") or []
            if sources or result.get("research_failed"):
                research_event = run.event(
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
                yield f"data: {json.dumps({'type': 'agent_event', **research_event})}\n\n"
            answer = str(result["messages"][-1].content)
            # Tool-only responses (for example image errors) do not pass through the LLM stream.
            if not streamed:
                yield f"data: {json.dumps({'type': 'token', 'content': answer})}\n\n"
            with Session(engine) as session:
                saved = Message(conversation_id=conversation_id, role="assistant", content=answer, message_metadata={"mode": result.get("mode"), "artifact_url": result.get("artifact_url")})
                session.add(saved)
                session.commit()
                session.refresh(saved)
            final_event = run.event(
                "finalize",
                "completed",
                "Response persisted locally",
                evidence={
                    "response_characters": len(answer),
                    "context_sources": result.get("context_manifest", []),
                    "artifact": bool(result.get("artifact_url")),
                },
            )
            yield f"data: {json.dumps({'type': 'agent_event', **final_event})}\n\n"
            run.finish("completed", summary={"mode": result.get("mode"), "artifact": bool(result.get("artifact_url"))})
            yield f"data: {json.dumps({'type': 'done', 'run_id': run.id, 'message': message_dict(saved)})}\n\n"
        except Exception as exc:
            logging.exception("Chat failed")
            run.event("error", "failed", "The run could not complete", detail=str(exc)[:500])
            run.finish("failed", summary={"error_type": type(exc).__name__})
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.websocket("/api/talk/ws")
async def talk_socket(websocket: WebSocket):
    await websocket.accept()
    audio_buffer = bytearray()
    history: list = []
    preferences: dict[str, str] = {}
    active_run = None

    async def send(event_type: str, **data):
        await websocket.send_json({"type": event_type, **data})

    async def respond(
        transcript: str,
        mode: str = "chat",
        attachment_ids: list[str] | None = None,
    ):
        nonlocal history, active_run
        if not transcript.strip():
            await send("error", message="I could not hear any speech. Please try again.")
            await send("state", value="idle")
            return
        await send("transcript", content=transcript)
        await send("state", value="thinking")
        active_run = run_ledger.start(
            "talk",
            metadata={
                "mode": mode,
                "attachments": len(attachment_ids or []),
                "model": settings.model_id,
            },
        )
        await send(
            "agent_event",
            **active_run.event(
                "context",
                "completed",
                "Turn context prepared locally",
                evidence={
                    "history_messages": len(history),
                    "attachments": len(attachment_ids or []),
                },
            ),
        )
        await send(
            "agent_event",
            **active_run.event("generate", "running", "Devvy is composing a grounded response"),
        )
        token_queue: asyncio.Queue[str] = asyncio.Queue()
        if mode == "talk":
            generation = asyncio.create_task(
                talk_agent.invoke(history, transcript, preferences, token_queue)
            )
        else:
            if mode not in {"auto", "chat", "code", "research", "image", "document"}:
                raise ValueError("Unsupported Talk mode")
            requested_ids = attachment_ids or []
            if len(requested_ids) > 10:
                raise ValueError("Talk messages support up to 10 attachments")
            attachments, context = attachment_data(requested_ids)
            if len(attachments) != len(requested_ids):
                raise ValueError("One or more selected attachments are no longer available")
            generation = asyncio.create_task(
                agent.invoke(history, transcript, mode, context, token_queue)
            )
        while not generation.done() or not token_queue.empty():
            try:
                token = await asyncio.wait_for(token_queue.get(), timeout=1)
                await send("token", content=token)
            except TimeoutError:
                await send("heartbeat")
        result = await generation
        response = result["response"] if mode == "talk" else str(result["messages"][-1].content)
        history = list(result["messages"])[-settings.model_context_messages:]
        await send("text_complete", content=response)
        if result.get("route_reason"):
            await send(
                "agent_event",
                **active_run.event(
                    "route",
                    "completed",
                    "Turn routed",
                    detail=str(result["route_reason"]),
                    evidence={
                        "research": bool(result.get("requires_research")),
                        "animation": bool(result.get("requires_animation")),
                    },
                ),
            )
        talk_sources = result.get("sources") or []
        if talk_sources or result.get("research_failed"):
            await send(
                "agent_event",
                **active_run.event(
                    "research",
                    "failed" if result.get("research_failed") else "completed",
                    (
                        "Live research unavailable — the answer must say so"
                        if result.get("research_failed")
                        else f"{len(talk_sources)} public source(s) retrieved"
                    ),
                    evidence={
                        "sources": [item["url"] for item in talk_sources if item.get("url")],
                        "titles": [item["title"] for item in talk_sources],
                    },
                ),
            )
        await send(
            "agent_event",
            **active_run.event(
                "generate",
                "completed",
                "Response completed",
                evidence={
                    "response_characters": len(response),
                    "context_sources": result.get("context_manifest", []),
                },
            ),
        )
        if result.get("artifact_url"):
            await send("image_ready", url=result["artifact_url"])
        await send("state", value="speaking")

        tts_task = asyncio.create_task(voice_engine.synthesize(response))
        animation_task = None
        if mode == "talk" and result["requires_animation"]:
            await send("animation_state", value="rendering")
            animation_task = asyncio.create_task(
                animation_engine.render(transcript[:60], response)
            )
        try:
            audio_url = await tts_task
            await send("audio_ready", url=audio_url)
        except Exception as exc:
            logger.warning("Talk TTS failed: %s", exc)
            await send("media_warning", message=str(exc))
        if animation_task:
            try:
                video_url = await animation_task
                await send("video_ready", url=video_url)
            except Exception as exc:
                logger.warning("Talk animation failed: %s", exc)
                await send("media_warning", message=str(exc))
        await send("state", value="idle")
        active_run.event(
            "media",
            "completed",
            "Optional media processing finished",
            evidence={"audio_requested": True, "animation_requested": bool(animation_task)},
        )
        active_run.finish("completed", summary={"mode": mode})
        active_run = None

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
        logger.exception("Talk session failed")
        if active_run is not None:
            active_run.event("error", "failed", "The Talk turn could not complete", detail=str(exc)[:500])
            active_run.finish("failed", summary={"error_type": type(exc).__name__})
            active_run = None
        try:
            await send("error", message=str(exc))
            await send("state", value="error")
        except Exception:
            pass


#: Pipeline checkpoints the Smart Code screen renders, in order. `classify` is satisfied by
#: request validation and `gate` by the human-approval step, so neither comes from the service.
SMART_CODE_STAGES = ("classify", "retrieve", "plan", "code", "verify", "critique", "gate")


@app.post("/api/smart-code/preview")
async def smart_code_preview(payload: SmartCodeRequest):
    async def events():
        run = run_ledger.start(
            "smart-code",
            metadata={"mode": payload.mode, "risk": payload.risk, "targets": len(payload.target_paths)},
        )
        yield sse(
            "started",
            {
                "run_id": run.id,
                "stage": "classify",
                "message": "Task contract accepted; no files will be written during preview",
            },
        )
        progress_queue: asyncio.Queue[dict] = asyncio.Queue()

        def progress(event: dict):
            progress_queue.put_nowait(event)

        task = asyncio.create_task(smart_code_service.preview(payload, progress))
        elapsed = 0
        emitted: set[str] = {"classify"}
        try:
            while not task.done() or not progress_queue.empty():
                try:
                    event = await asyncio.wait_for(progress_queue.get(), timeout=2)
                    stage = str(event.get("stage", "generate"))
                    run_event = run.event(
                        stage,
                        str(event.get("status", "running")),
                        str(event.get("label", "Agent workflow update")),
                        detail=event.get("detail"),
                        evidence=event.get("evidence"),
                    )
                    yield sse("agent_event", run_event)
                    # Forward pipeline stages the moment the service reaches them, so a
                    # multi-minute CPU generation shows real movement instead of a frozen
                    # checklist that fills in all at once at the end.
                    if stage in SMART_CODE_STAGES and event.get("status") != "running":
                        yield sse("stage", {"stage": stage, "status": event.get("status")})
                        emitted.add(stage)
                    if stage == "structured_loop":
                        yield sse("loop", event)
                except TimeoutError:
                    elapsed += 2
                    yield sse(
                        "status",
                        {
                            "stage": "generate",
                            "message": (
                                f"Devvy is planning and coding locally ({elapsed}s)"
                                if "plan" not in emitted
                                else f"Devvy is verifying the proposed change ({elapsed}s)"
                            ),
                        },
                    )
            result = await task
            for stage in SMART_CODE_STAGES:
                if stage not in emitted:
                    yield sse("stage", {"stage": stage, "status": "completed"})
            gate_event = run.event(
                "gate",
                "waiting" if result.get("can_apply") else "completed",
                "Waiting for explicit human approval" if result.get("can_apply") else "No write action available",
                evidence={"can_apply": result.get("can_apply"), "edits": len(result.get("edits", []))},
            )
            yield sse("agent_event", gate_event)
            run.finish(
                "completed",
                summary={"can_apply": result.get("can_apply"), "edits": len(result.get("edits", []))},
            )
            yield sse("result", result)
        except ValueError as exc:
            logger.warning("Smart Code preview rejected: %s", exc)
            run.event("error", "failed", "Preview rejected safely", detail=str(exc)[:500])
            run.finish("failed", summary={"error_type": type(exc).__name__})
            yield sse("error", {"message": str(exc)})
        except Exception as exc:
            logger.exception("Smart Code preview failed")
            run.event("error", "failed", "Preview failed safely", detail=str(exc)[:500])
            run.finish("failed", summary={"error_type": type(exc).__name__})
            yield sse("error", {"message": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
    "assemble_context": ("assemble_context",),
    "declare_stack": ("declare_stack",),
    "score_factors": ("score_factors",),
    "calculate": ("apply_base_adjustments", "apply_stack_adjustments", "map_to_fibonacci"),
    "policy_gate": ("evaluate_gates", "decide"),
}


async def estimate_events(story):
    run = run_ledger.start(
        "estimate-code", metadata={"source": story.source, "model": settings.model_id}
    )
    yield sse("started", {"run_id": run.id, "title": story.title})
    progress_queue: asyncio.Queue[dict] = asyncio.Queue()

    def progress(event: dict):
        progress_queue.put_nowait(event)

    task = asyncio.create_task(estimate_service.estimate(story, progress))
    elapsed = 0
    emitted: set[str] = set()
    try:
        while not task.done() or not progress_queue.empty():
            try:
                event = await asyncio.wait_for(progress_queue.get(), timeout=2)
                stage = str(event.get("stage", "estimate"))
                status = str(event.get("status", "running"))
                run_event = run.event(
                    stage,
                    status,
                    str(event.get("label", "Estimation workflow update")),
                    detail=event.get("detail"),
                    evidence=event.get("evidence"),
                )
                yield sse("agent_event", run_event)
                if stage == "structured_loop":
                    yield sse("loop", event)
                if status in {"completed", "validated"}:
                    for node in ESTIMATE_NODES.get(stage, ()):
                        emitted.add(node)
                        yield sse("node", {"node": node, "status": "completed"})
            except TimeoutError:
                elapsed += 2
                yield sse(
                    "status",
                    {
                        "message": (
                            "Devvy is scoring the 16 factors on CPU"
                            if "score_factors" not in emitted
                            else "Devvy is applying the framework arithmetic"
                        )
                        + f" ({elapsed}s)"
                    },
                )
        result = await task
        # Backstop: any checkpoint the service did not report is still closed out, so the
        # checklist can never finish in a half-ticked state.
        for stage in (
            "assemble_context", "declare_stack", "score_factors", "apply_base_adjustments",
            "apply_stack_adjustments", "map_to_fibonacci", "evaluate_gates", "decide",
        ):
            if stage not in emitted:
                yield sse("node", {"node": stage, "status": "completed"})
        run.finish(
            "completed",
            summary={
                "points": result.get("points"),
                "source": story.source,
                "recommendation": result.get("recommendation"),
                "confidence": result.get("confidence"),
            },
        )
        yield sse("result", result)
    except Exception as exc:
        logger.exception("Estimate Code failed")
        run.event("error", "failed", "Estimate failed validation", detail=str(exc)[:500])
        run.finish("failed", summary={"error_type": type(exc).__name__})
        yield sse("error", {"message": str(exc), "retryable": True})


@app.post("/api/estimate-code/estimate")
async def estimate_code(payload: EstimateRequest):
    return StreamingResponse(
        estimate_events(payload.story),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/estimate-code/batch")
async def estimate_code_batch(payload: BatchEstimateRequest):
    async def events():
        yield sse("batch_started", {"count": len(payload.stories)})
        results = []
        for index, story in enumerate(payload.stories):
            yield sse("item_started", {"index": index, "title": story.title})
            async for chunk in estimate_events(story):
                if chunk.startswith("event: node"):
                    data = json.loads(chunk.split("data: ", 1)[1])
                    yield sse("item_node", {"index": index, **data})
                elif chunk.startswith("event: result"):
                    data = json.loads(chunk.split("data: ", 1)[1])
                    results.append(data)
                    yield sse("item_result", {"index": index, "result": data})
                elif chunk.startswith("event: status"):
                    data = json.loads(chunk.split("data: ", 1)[1])
                    yield sse("status", {"index": index, **data})
                elif chunk.startswith("event: agent_event"):
                    data = json.loads(chunk.split("data: ", 1)[1])
                    yield sse("agent_event", {"index": index, **data})
                elif chunk.startswith("event: loop"):
                    data = json.loads(chunk.split("data: ", 1)[1])
                    yield sse("loop", {"index": index, **data})
                elif chunk.startswith("event: error"):
                    data = json.loads(chunk.split("data: ", 1)[1])
                    yield sse("item_error", {"index": index, **data})
        yield sse("batch_result", {"results": results})

    return StreamingResponse(events(), media_type="text/event-stream")


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


@app.post("/api/estimate-code/upload/estimate")
async def estimate_upload_rows(payload: dict = Body(...)):
    try:
        stack = StackProfile.model_validate(payload.get("stack") or {})
        stories = rows_to_stories(payload.get("rows") or [], payload.get("mapping") or {}, stack)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return await estimate_code_batch(BatchEstimateRequest(stories=stories))


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
