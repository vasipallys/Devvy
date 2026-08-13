import asyncio
import json
import logging
import mimetypes
import shutil
import threading
import time
from importlib.util import find_spec
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from fastapi import (
    Body, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, HumanMessage
from sqlmodel import Session, col, select

from backend.agent import ChatAgent
from backend.agent_graph import TalkAgentGraph
from backend.animation_engine import AnimationEngine
from backend.auth import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    ArtifactRecord,
    ResourceShare,
    UploadRecord,
    User,
    aware,
    authenticate,
    create_invitation,
    create_session,
    create_share,
    create_user,
    csrf_valid,
    list_shares,
    hash_password,
    normalize_email,
    permission_for,
    public_user,
    record_artifact,
    resolve_session,
    revoke_all_user_sessions,
    revoke_session,
    share_dict,
    sweep_auth_records,
    user_count,
    validate_password,
    verify_password,
)
from backend.config import deployment_problems, get_settings
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
    EstimateRecord, clear_estimates,
    record_decision,
    delete_estimate,
    estimate_stats,
    get_estimate,
    list_estimates,
    reference_corpus,
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
from backend.harness import RunLedger, sweep_directory
from backend.jobs import FINISHED as FINISHED_JOB_STATES, Job, JobContext, JobRunner
from backend.model import GemmaRuntime
from backend.observability import configure_observability
from backend.schemas import ChatRequest, RenameRequest
from backend.smart_code import (
    SmartCodeApplyRequest, SmartCodeRequest, SmartCodeService, inspect_workspace,
)
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
    # Before anything is served, not after. A deployment that is unsafe for its reachability
    # must fail here, where somebody is watching, rather than work perfectly until it doesn't.
    fatal, warnings = deployment_problems(settings)
    for message in warnings:
        logger.warning("Deployment: %s", message)
    if fatal:
        for message in fatal:
            logger.critical("Refusing to start: %s", message)
        raise RuntimeError(fatal[0])
    init_db()
    if settings.app_host not in {"127.0.0.1", "localhost", "::1"}:
        if not settings.auth_enabled:
            raise RuntimeError("Authentication must be enabled for a non-loopback deployment.")
        if not settings.auth_secure_cookies:
            raise RuntimeError("Secure authentication cookies are required off loopback.")
    await asyncio.to_thread(sweep_auth_records, engine)
    configure_observability(settings, application)
    # Artefact retention runs alongside the job reconciliation below, off the event loop.
    # Doing it at startup rather than on a timer keeps the running application free of a
    # background sweeper for a job that only needs doing once a session.
    swept = await asyncio.to_thread(
        sweep_directory, settings.uploads_dir, settings.upload_retention_days, ("*",),
    )
    swept += await asyncio.to_thread(
        sweep_directory, settings.generated_dir, settings.upload_retention_days, ("*",),
    )
    if swept:
        logger.info("Removed %d expired upload/generated file(s)", swept)
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


PUBLIC_HTTP_PATHS = {
    "/api/health",
    "/api/auth/session",
    "/api/auth/login",
    "/api/auth/register",
}
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class LoginLimiter:
    """Small in-process sliding-window limiter for the local authentication boundary."""

    #: Hard ceiling on tracked keys. A failed attempt creates a key per client-and-email
    #: pair and only a *successful* sign-in removes one, so without a bound, spraying
    #: distinct addresses grows this map for the life of the process — the limiter
    #: protecting the sign-in endpoint becoming its own denial-of-service vector.
    MAX_KEYS = 2048

    def __init__(self) -> None:
        self._attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, cutoff: float) -> None:
        """Enforce the ceiling. Caller holds the lock.

        Expired keys go first. If a burst inside a single window still exceeds the ceiling,
        the *least recently active* keys are evicted — which is the safe order: an attack in
        progress is by definition recently active, so its lockout survives, while the keys
        being dropped are ones nobody has touched.
        """
        for key in [
            key for key, values in self._attempts.items() if not any(v >= cutoff for v in values)
        ]:
            del self._attempts[key]
        excess = len(self._attempts) - self.MAX_KEYS
        if excess <= 0:
            return
        stale_first = sorted(self._attempts, key=lambda key: max(self._attempts[key], default=0.0))
        for key in stale_first[:excess]:
            del self._attempts[key]

    def allow(self, key: str) -> bool:
        cutoff = time.monotonic() - settings.auth_login_window_minutes * 60
        with self._lock:
            if len(self._attempts) > self.MAX_KEYS:
                self._prune(cutoff)
            recent = [value for value in self._attempts.get(key, []) if value >= cutoff]
            if len(recent) >= settings.auth_login_attempts:
                self._attempts[key] = recent
                return False
            recent.append(time.monotonic())
            self._attempts[key] = recent
            return True

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


login_limiter = LoginLimiter()


def _auth_error(status: int, detail: str, code: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": detail, "code": code})


def _security_headers(response: Response, path: str) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=()")
    if path.startswith("/api/auth/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _origin_allowed(origin: str | None) -> bool:
    if not origin:
        return True  # Local CLI and TestClient requests do not necessarily send Origin.
    parsed = urlparse(origin)
    return origin in settings.cors_origins or (
        parsed.scheme in {"http", "https"} and parsed.hostname in {"localhost", "127.0.0.1"}
    )


@app.middleware("http")
async def authentication_boundary(request: Request, call_next):
    """Authenticate once, protect state changes with CSRF, and attach the actor to state."""
    request.state.user = None
    request.state.auth_session = None
    if not settings.auth_enabled or request.method == "OPTIONS":
        return _security_headers(await call_next(request), request.url.path)
    path = request.url.path
    if request.method in UNSAFE_METHODS and not _origin_allowed(request.headers.get("origin")):
        return _auth_error(403, "This origin is not allowed.", "origin_denied")
    protected = path.startswith("/api/") or path.startswith("/generated/")
    if not protected or path in PUBLIC_HTTP_PATHS:
        return _security_headers(await call_next(request), path)
    resolved = await asyncio.to_thread(
        resolve_session, engine, request.cookies.get(SESSION_COOKIE)
    )
    if resolved is None:
        return _auth_error(401, "Sign in to continue.", "authentication_required")
    user, auth_session = resolved
    request.state.user = user
    request.state.auth_session = auth_session
    if path.startswith("/generated/"):
        filename = path.removeprefix("/generated/")
        with Session(engine) as session:
            artifact = session.exec(
                select(ArtifactRecord).where(ArtifactRecord.filename == filename)
            ).first()
        allowed = bool(artifact and artifact.owner_id == user.id)
        if artifact and not allowed and artifact.job_id:
            allowed = permission_for(engine, user.id, "job", str(artifact.job_id)) in {
                "viewer", "editor"
            }
        if artifact and not allowed and artifact.conversation_id:
            allowed = permission_for(
                engine, user.id, "conversation", str(artifact.conversation_id)
            ) in {"viewer", "editor"}
        # Pre-authentication artefacts have no ownership row. Only the workspace owner may
        # recover those; every newly generated artefact is registered before its URL returns.
        if not allowed and not (artifact is None and user.role == "owner"):
            return _auth_error(404, "Generated artefact not found.", "artifact_not_found")
    if request.method in UNSAFE_METHODS and not csrf_valid(
        auth_session,
        request.cookies.get(CSRF_COOKIE),
        request.headers.get("x-csrf-token"),
    ):
        return _auth_error(403, "Refresh the page and try again.", "csrf_failed")
    return _security_headers(await call_next(request), path)


def actor(request: Request) -> User | None:
    return getattr(request.state, "user", None)


def actor_id(request: Request) -> UUID | None:
    user = actor(request)
    return user.id if user else None


def ensure_job_capacity(owner_id: UUID | None) -> None:
    if owner_id is not None and job_runner.active_count(owner_id) >= settings.max_active_jobs_per_user:
        raise HTTPException(
            429,
            f"You already have {settings.max_active_jobs_per_user} active requests. "
            "Wait for one to finish or cancel it in Activity.",
        )


def require_role(request: Request, *roles: str) -> User:
    user = actor(request)
    if user is None or user.role not in roles:
        raise HTTPException(403, "You do not have permission to manage workspace access.")
    return user


def _set_auth_cookies(response: Response, token: str, csrf: str, remember: bool) -> None:
    max_age = (
        settings.auth_remember_days * 86400 if remember else settings.auth_session_hours * 3600
    )
    common = {
        "secure": settings.auth_secure_cookies,
        "samesite": "lax",
        "path": "/",
        "max_age": max_age,
    }
    response.set_cookie(SESSION_COOKIE, token, httponly=True, **common)
    response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **common)


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax")
    response.delete_cookie(CSRF_COOKIE, path="/", samesite="lax")


def _client_hint(request: Request) -> str:
    return request.client.host if request.client else ""


def _session_payload(user: User | None, *, authenticated: bool) -> dict[str, Any]:
    return {
        "authenticated": authenticated,
        "needs_setup": settings.auth_enabled and user_count(engine) == 0,
        "auth_enabled": settings.auth_enabled,
        "user": public_user(user) if user else None,
        "security": {
            "session_cookie": "HttpOnly",
            "csrf": "double-submit",
            "secure_cookie": settings.auth_secure_cookies,
        },
    }


@app.get("/api/auth/session")
def auth_session(request: Request):
    if not settings.auth_enabled:
        return _session_payload(None, authenticated=True)
    resolved = resolve_session(engine, request.cookies.get(SESSION_COOKIE))
    return _session_payload(resolved[0] if resolved else None, authenticated=bool(resolved))


@app.post("/api/auth/register", status_code=201)
def register_account(request: Request, response: Response, payload: dict = Body(...)):
    try:
        user = create_user(
            engine,
            email=str(payload.get("email", "")),
            display_name=str(payload.get("display_name", "")),
            password=str(payload.get("password", "")),
            invite_token=str(payload.get("invite_token") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    token, csrf, _ = create_session(
        engine,
        user.id,
        remember=bool(payload.get("remember", True)),
        user_agent=request.headers.get("user-agent", ""),
        ip_hint=_client_hint(request),
        remember_days=settings.auth_remember_days,
        session_hours=settings.auth_session_hours,
    )
    _set_auth_cookies(response, token, csrf, bool(payload.get("remember", True)))
    return _session_payload(user, authenticated=True)


@app.post("/api/auth/login")
def login(request: Request, response: Response, payload: dict = Body(...)):
    email = str(payload.get("email", ""))
    try:
        key = f"{_client_hint(request)}:{normalize_email(email)}"
    except ValueError:
        key = f"{_client_hint(request)}:invalid"
    if not login_limiter.allow(key):
        raise HTTPException(429, "Too many sign-in attempts. Wait a few minutes and try again.")
    user = authenticate(engine, email, str(payload.get("password", "")))
    if user is None:
        raise HTTPException(401, "Email or password is incorrect.")
    login_limiter.reset(key)
    remember = bool(payload.get("remember", True))
    token, csrf, _ = create_session(
        engine,
        user.id,
        remember=remember,
        user_agent=request.headers.get("user-agent", ""),
        ip_hint=_client_hint(request),
        remember_days=settings.auth_remember_days,
        session_hours=settings.auth_session_hours,
    )
    _set_auth_cookies(response, token, csrf, remember)
    return _session_payload(user, authenticated=True)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, payload: dict = Body(default={})):
    user = actor(request)
    action = str(payload.get("active_job_action", "keep"))
    if action not in {"keep", "cancel"}:
        raise HTTPException(400, "active_job_action must be keep or cancel.")
    cancelled = job_runner.cancel_all(user.id) if user and action == "cancel" else 0
    revoke_session(engine, request.cookies.get(SESSION_COOKIE))
    _clear_auth_cookies(response)
    return {"signed_out": True, "active_job_action": action, "cancelled_jobs": cancelled}


@app.get("/api/auth/users")
def access_users(request: Request):
    require_role(request, "owner", "admin")
    with Session(engine) as session:
        users = session.exec(select(User).order_by(User.created_at)).all()
    return [public_user(item) for item in users]


@app.patch("/api/auth/users/{user_id}")
def update_access_user(user_id: UUID, request: Request, payload: dict = Body(...)):
    manager = require_role(request, "owner", "admin")
    with Session(engine) as session:
        target = session.get(User, user_id)
        if target is None:
            raise HTTPException(404, "Workspace member not found.")
        if manager.role != "owner" and target.role in {"owner", "admin"}:
            raise HTTPException(403, "Only the workspace owner may manage administrators.")
        if target.id == manager.id and payload.get("active") is False:
            raise HTTPException(400, "You cannot deactivate your own account.")
        role = payload.get("role")
        if role is not None:
            if role not in {"admin", "member"} or target.role == "owner":
                raise HTTPException(400, "The workspace owner role cannot be reassigned.")
            if manager.role != "owner" and role != "member":
                raise HTTPException(403, "Only the workspace owner may appoint administrators.")
            target.role = role
        if "active" in payload:
            target.active = bool(payload["active"])
        target.updated_at = now()
        session.add(target)
        session.commit()
        session.refresh(target)
    if not target.active:
        revoke_all_user_sessions(engine, target.id)
    return public_user(target)


@app.patch("/api/auth/me")
def update_me(request: Request, payload: dict = Body(...)):
    user = actor(request)
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    allowed_preferences = {
        "default_workspace", "density", "evidence_expanded", "confirm_external_research"
    }
    with Session(engine) as session:
        item = session.get(User, user.id)
        if item is None:
            raise HTTPException(404, "Account not found.")
        if "display_name" in payload:
            name = str(payload["display_name"]).strip()
            if len(name) < 2 or len(name) > 100:
                raise HTTPException(400, "Display name must be between 2 and 100 characters.")
            item.display_name = name
        preferences = dict(item.preferences or {})
        for key, value in dict(payload.get("preferences") or {}).items():
            if key in allowed_preferences:
                preferences[key] = value
        if preferences.get("density") not in {"comfortable", "compact"}:
            raise HTTPException(400, "Density must be comfortable or compact.")
        item.preferences = preferences
        item.updated_at = now()
        session.add(item)
        session.commit()
        session.refresh(item)
        return public_user(item)


@app.post("/api/auth/me/password")
def change_password(request: Request, payload: dict = Body(...)):
    user = actor(request)
    auth_session_item = getattr(request.state, "auth_session", None)
    if user is None or auth_session_item is None:
        raise HTTPException(401, "Sign in to continue.")
    current_password = str(payload.get("current_password", ""))
    new_password = str(payload.get("new_password", ""))
    with Session(engine) as session:
        item = session.get(User, user.id)
        if item is None or not verify_password(current_password, item.password_hash):
            raise HTTPException(400, "Current password is incorrect.")
        try:
            validate_password(new_password, item.email)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if verify_password(new_password, item.password_hash):
            raise HTTPException(400, "Choose a password you have not already used here.")
        item.password_hash = hash_password(new_password)
        item.updated_at = now()
        session.add(item)
        session.commit()
    revoked = revoke_all_user_sessions(engine, user.id, except_session_id=auth_session_item.id)
    return {"changed": True, "other_sessions_revoked": revoked}


@app.post("/api/auth/invitations", status_code=201)
def invite_user(request: Request, payload: dict = Body(...)):
    manager = require_role(request, "owner", "admin")
    if manager.role != "owner" and str(payload.get("role", "member")) == "admin":
        raise HTTPException(403, "Only the workspace owner may invite an administrator.")
    try:
        invitation, token = create_invitation(
            engine,
            email=str(payload.get("email", "")),
            role=str(payload.get("role", "member")),
            invited_by=manager.id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "id": str(invitation.id),
        "email": invitation.email,
        "role": invitation.role,
        "expires_at": aware(invitation.expires_at).isoformat(),
        "invite_token": token,
        "invite_route": f"#/register?invite={token}",
    }


def _resource_owner(resource_type: str, resource_id: str) -> UUID | None:
    try:
        identifier = UUID(resource_id)
    except ValueError:
        return None
    model = {"conversation": Conversation, "job": Job, "estimate": EstimateRecord}.get(resource_type)
    if model is None:
        return None
    with Session(engine) as session:
        item = session.get(model, identifier)
        return item.owner_id if item else None


def _resource_access(
    request: Request, resource_type: str, resource_id: UUID, *, edit: bool = False
) -> bool:
    if not settings.auth_enabled:
        return True
    user = actor(request)
    if user is None:
        return False
    owner_id = _resource_owner(resource_type, str(resource_id))
    if owner_id == user.id:
        return True
    permission = permission_for(engine, user.id, resource_type, str(resource_id))
    return permission == "editor" if edit else permission in {"viewer", "editor"}


@app.post("/api/access/shares", status_code=201)
def grant_share(request: Request, payload: dict = Body(...)):
    user = actor(request)
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    resource_type = str(payload.get("resource_type", ""))
    resource_id = str(payload.get("resource_id", ""))
    if _resource_owner(resource_type, resource_id) != user.id:
        raise HTTPException(403, "Only the owner can share this item.")
    try:
        item = create_share(
            engine,
            owner_id=user.id,
            recipient_email=str(payload.get("recipient_email", "")),
            resource_type=resource_type,
            resource_id=resource_id,
            permission=str(payload.get("permission", "viewer")),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return share_dict(engine, item)


@app.get("/api/access/shares")
def my_shares(request: Request, incoming: bool = Query(default=False)):
    user = actor(request)
    if user is None:
        raise HTTPException(401, "Sign in to continue.")
    return list_shares(engine, user.id, incoming=incoming)


@app.delete("/api/access/shares/{share_id}", status_code=204)
def revoke_share(share_id: UUID, request: Request):
    user = actor(request)
    with Session(engine) as session:
        item = session.get(ResourceShare, share_id)
        if item is None:
            raise HTTPException(404, "Share not found.")
        if user is None or item.owner_id != user.id:
            raise HTTPException(403, "Only the owner can revoke this share.")
        session.delete(item)
        session.commit()


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def author_names(session: Session, messages: list[Message]) -> dict[UUID, str]:
    """Resolve every author in one query.

    Looking the author up inside the per-message projection opened a fresh session and
    issued a query for each row, so opening a hundred-message conversation cost a hundred
    round trips — and nested a second session inside the one already iterating the rows.
    """
    ids = {item.author_id for item in messages if item.author_id}
    if not ids:
        return {}
    rows = session.exec(select(User.id, User.display_name).where(col(User.id).in_(ids))).all()
    return {row[0]: row[1] for row in rows}


def message_dict(item: Message, authors: dict[UUID, str] | None = None) -> dict:
    author_name = None
    if item.author_id:
        if authors is not None:
            author_name = authors.get(item.author_id)
        else:
            with Session(engine) as session:
                author = session.get(User, item.author_id)
                author_name = author.display_name if author else None
    return {
        "id": str(item.id), "role": item.role, "content": item.content,
        "created_at": utc_iso(item.created_at), "attachments": item.attachments,
        "metadata": item.message_metadata,
        "author_id": str(item.author_id) if item.author_id else None,
        "author_name": author_name,
    }


@app.get("/api/health")
def health():
    if settings.auth_enabled:
        return {"status": "ok"}
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
            "authentication": "Required" if settings.auth_enabled else "Disabled",
            "access_model": "Per-user ownership with explicit viewer/editor grants",
        },
        "limits": {
            "upload_mb": 25,
            "attachments": 10,
            "context_characters": settings.document_max_chars,
            "smart_code_context_characters": settings.smart_code_max_context_chars,
        },
    }


@app.get("/api/conversations")
def conversations(request: Request):
    with Session(engine) as session:
        statement = select(Conversation).order_by(Conversation.updated_at.desc())
        if actor_id(request) is not None:
            statement = statement.where(Conversation.owner_id == actor_id(request))
        items = session.exec(statement).all()
        return items


@app.post("/api/conversations")
def new_conversation(request: Request):
    with Session(engine) as session:
        return create_conversation(session, owner_id=actor_id(request))


@app.get("/api/conversations/{conversation_id}/messages")
def messages(conversation_id: UUID, request: Request):
    with Session(engine) as session:
        if not session.get(Conversation, conversation_id) or not _resource_access(
            request, "conversation", conversation_id
        ):
            raise HTTPException(404, "Conversation not found")
        items = list_messages(session, conversation_id)
        authors = author_names(session, items)
        return [message_dict(item, authors) for item in items]


@app.patch("/api/conversations/{conversation_id}")
def rename(conversation_id: UUID, payload: RenameRequest, request: Request):
    with Session(engine) as session:
        item = session.get(Conversation, conversation_id)
        if not item or not _resource_access(request, "conversation", conversation_id, edit=True):
            raise HTTPException(404, "Conversation not found")
        item.title = payload.title.strip()
        item.updated_at = now()
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


@app.delete("/api/conversations/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: UUID, request: Request):
    with Session(engine) as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None or (
            actor_id(request) is not None and conversation.owner_id != actor_id(request)
        ):
            raise HTTPException(404, "Conversation not found")
        for item in list_messages(session, conversation_id):
            session.delete(item)
        session.delete(conversation)
        session.commit()


@app.post("/api/uploads")
async def upload(request: Request, file: UploadFile = File(...)):
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
    content_type = (
        file.content_type or mimetypes.guess_type(file.filename)[0] or "application/octet-stream"
    )
    with Session(engine) as session:
        session.add(
            UploadRecord(
                id=UUID(upload_id),
                owner_id=actor_id(request),
                filename=file.filename,
                stored_name=destination.name,
                content_type=content_type,
                size=destination.stat().st_size,
            )
        )
        session.commit()
    return {
        "id": upload_id, "name": file.filename, "content_type": content_type,
        "size": destination.stat().st_size,
    }


def attachment_data(ids: list[str], owner_id: UUID | None = None) -> tuple[list[dict], str]:
    attachments, contexts = [], []
    for upload_id in ids:
        try:
            safe_id = str(UUID(str(upload_id)))
        except (TypeError, ValueError, AttributeError):
            continue
        if owner_id is not None:
            with Session(engine) as session:
                record = session.get(UploadRecord, UUID(safe_id))
                if record is None or record.owner_id != owner_id:
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
        completion = result.get("completion") or {}
        if completion.get("truncated"):
            # Silence here is the actual defect: a capped answer stops mid-word and is
            # indistinguishable from a finished one, in a product whose premise is that you
            # can tell what happened.
            await context.event(
                "generate", "failed", "Answer stopped at the output limit",
                detail=(
                    f"The reply reached the {completion.get('max_new_tokens')}-token ceiling "
                    "and was cut off. Ask for a shorter answer, or raise MAX_NEW_TOKENS."
                ),
                evidence={
                    "completion_tokens": completion.get("completion_tokens"),
                    "max_new_tokens": completion.get("max_new_tokens"),
                    "truncated": True,
                },
            )
        if not streamed:
            await context.token(answer)
        await asyncio.to_thread(
            record_artifact,
            engine,
            result.get("artifact_url"),
            owner_id=context.owner_id,
            job_id=context.job_id,
            conversation_id=conversation_id,
        )
        with Session(engine) as session:
            saved = Message(
                conversation_id=conversation_id, role="assistant", content=answer,
                author_id=context.owner_id,
                message_metadata={
                    "mode": result.get("mode"),
                    "artifact_url": result.get("artifact_url"),
                    # Persisted, not only streamed: a reader opening this conversation
                    # tomorrow must still see that the answer was cut short.
                    "truncated": bool(completion.get("truncated")),
                    "completion_tokens": completion.get("completion_tokens"),
                    "max_new_tokens": completion.get("max_new_tokens"),
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
        await run.finish_async(
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
        # Synchronous on purpose: awaiting inside a cancelled task raises immediately, which
        # would drop the very record that says the run was cancelled.
        run.finish("cancelled")
        raise
    except Exception as exc:
        run.event("error", "failed", "The run could not complete", detail=str(exc)[:500])
        await run.finish_async("failed", summary={"error_type": type(exc).__name__})
        await context.event("error", "failed", "The run could not complete", detail=str(exc)[:500])
        raise


job_runner.register("chat", run_chat_job)


@app.post("/api/chat/jobs", status_code=202)
def submit_chat(payload: ChatRequest, request: Request):
    """Accept a chat turn and return immediately with a job to follow.

    The user message and conversation are persisted before the response is sent, so the UI
    can render the turn instantly and the work is recoverable even if the client never
    reconnects.
    """
    owner = actor_id(request)
    ensure_job_capacity(owner)
    conversation_id = payload.conversation_id
    with Session(engine) as session:
        conversation = session.get(Conversation, conversation_id) if conversation_id else None
        if conversation and not _resource_access(
            request, "conversation", conversation.id, edit=True
        ):
            raise HTTPException(404, "Conversation not found")
        if not conversation:
            conversation = create_conversation(
                session, payload.message[:60], owner_id=owner
            )
        conversation_id = conversation.id
        attachments, attachment_context = attachment_data(
            payload.attachment_ids, owner
        )
        user_message = Message(
            conversation_id=conversation_id, role="user",
            content=payload.message, attachments=attachments,
            author_id=owner,
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
        owner_id=owner,
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
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    kind: str | None = Query(default=None, max_length=20),
):
    """Everything the activity view needs on reopening the browser."""
    owner = actor_id(request)
    return {
        "jobs": job_runner.list(limit=limit, kind=kind, owner_id=owner),
        "active": job_runner.active_count(owner),
    }


@app.get("/api/jobs/{job_id}")
def get_job(job_id: UUID, request: Request):
    if not _resource_access(request, "job", job_id):
        raise HTTPException(404, "Job not found")
    detail = job_runner.snapshot(job_id)
    if detail is None:
        raise HTTPException(404, "Job not found")
    detail["access"] = {
        "owner": not settings.auth_enabled or job_runner.owner(job_id) == actor_id(request),
        "permission": permission_for(engine, actor_id(request), "job", str(job_id))
        if actor_id(request) else None,
    }
    return detail


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: UUID, request: Request):
    if settings.auth_enabled and job_runner.owner(job_id) != actor_id(request):
        raise HTTPException(404, "Job not found")
    if not job_runner.cancel(job_id):
        raise HTTPException(409, "That request has already finished.")
    return {"status": "cancelling", "job_id": str(job_id)}


@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: UUID, request: Request):
    """Attach to a job: a full snapshot, then live deltas until it finishes.

    Subscribing happens *before* the snapshot is taken, so no event can fall between the
    two. Deltas already contained in the snapshot are filtered by the client using each
    token's offset, which makes attaching at any point safe and repeatable.
    """
    if not _resource_access(request, "job", job_id):
        raise HTTPException(404, "Job not found")
    queue = job_runner.subscribe(job_id)
    snapshot = job_runner.snapshot(job_id)
    if snapshot is None:
        job_runner.unsubscribe(job_id, queue)
        raise HTTPException(404, "Job not found")
    snapshot["access"] = {
        "owner": not settings.auth_enabled or job_runner.owner(job_id) == actor_id(request),
        "permission": permission_for(engine, actor_id(request), "job", str(job_id))
        if actor_id(request) else None,
    }

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
        talk_completion = result.get("completion") or {}
        await context.event(
            "generate",
            "failed" if talk_completion.get("truncated") else "completed",
            "Answer stopped at the output limit" if talk_completion.get("truncated")
            else "Response completed",
            evidence={
                "response_characters": len(response),
                "context_sources": result.get("context_manifest", []),
                "completion_tokens": talk_completion.get("completion_tokens"),
                "truncated": bool(talk_completion.get("truncated")),
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
        await run.finish_async("completed", summary={"mode": mode})
        for url in (
            job_result.get("artifact_url"), job_result.get("audio_url"), job_result.get("video_url")
        ):
            await asyncio.to_thread(
                record_artifact,
                engine,
                url,
                owner_id=context.owner_id,
                job_id=context.job_id,
            )
        return job_result
    except asyncio.CancelledError:
        run.finish("cancelled")  # see run_chat_job: no awaiting on the cancellation path
        raise
    except Exception as exc:
        run.event("error", "failed", "The Talk turn could not complete", detail=str(exc)[:500])
        await run.finish_async("failed", summary={"error_type": type(exc).__name__})
        raise


job_runner.register("talk", run_talk_job)


@app.websocket("/api/talk/ws")
async def talk_socket(websocket: WebSocket):
    if not _origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=4403, reason="Origin is not allowed")
        return
    owner: UUID | None = None
    if settings.auth_enabled:
        resolved = await asyncio.to_thread(
            resolve_session, engine, websocket.cookies.get(SESSION_COOKIE)
        )
        if resolved is None:
            await websocket.close(code=4401, reason="Authentication required")
            return
        owner = resolved[0].id
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
            # Off the loop: this reads files from disk and parses PDF/DOCX up to the context
            # budget. Inline, it stalls the single thread serving every other socket, SSE
            # stream, and request for as long as extraction takes.
            attachments, attachment_context = await asyncio.to_thread(
                attachment_data, requested_ids, owner
            )
            if len(attachments) != len(requested_ids):
                raise ValueError("One or more selected attachments are no longer available")

        await send("transcript", content=transcript)
        await send("state", value="thinking")
        if owner is not None and job_runner.active_count(owner) >= settings.max_active_jobs_per_user:
            await send(
                "error",
                message="Too many active requests. Wait for one to finish or cancel it in Activity.",
            )
            await send("state", value="idle")
            return
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
            owner_id=owner,
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

    task = asyncio.create_task(
        smart_code_service.preview(
            payload, progress, str(context.owner_id) if context.owner_id else None
        )
    )
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
        await run.finish_async(
            "completed",
            summary={"can_apply": result.get("can_apply"), "edits": len(result.get("edits", []))},
        )
        return {"preview": result, "stages": stages}
    except asyncio.CancelledError:
        task.cancel()
        run.finish("cancelled")  # see run_chat_job: no awaiting on the cancellation path
        raise
    except ValueError as exc:
        logger.warning("Smart Code preview rejected: %s", exc)
        run.event("error", "failed", "Preview rejected safely", detail=str(exc)[:500])
        await run.finish_async("failed", summary={"error_type": type(exc).__name__})
        raise
    except Exception as exc:
        logger.exception("Smart Code preview failed")
        run.event("error", "failed", "Preview failed safely", detail=str(exc)[:500])
        await run.finish_async("failed", summary={"error_type": type(exc).__name__})
        raise


job_runner.register("smart-code", run_smart_code_job)


@app.post("/api/smart-code/jobs", status_code=202)
def submit_smart_code(payload: SmartCodeRequest, request: Request):
    owner = actor_id(request)
    ensure_job_capacity(owner)
    job = job_runner.submit(
        "smart-code", payload.objective[:120], payload.model_dump(mode="json"), owner_id=owner
    )
    return {"job_id": str(job.id)}


@app.get("/api/smart-code/workspace")
def smart_code_workspace(path: str = Query(min_length=1, max_length=2000)):
    """Describe a folder so the UI can infer the kind of change without asking the user.

    Authenticated only, like every other Smart Code route, and deliberately thin: counts and
    languages, never file names. Smart Code already lets an authenticated user point a run at
    any path on this machine, so this adds no capability — it only lets the interface stop
    asking a question it can answer itself.
    """
    return inspect_workspace(path)


@app.post("/api/smart-code/jobs/{job_id}/fix", status_code=202)
def fix_smart_code(job_id: UUID, request: Request, payload: dict = Body(default={})):
    """Re-run a finished Smart Code job with its own failures as the brief.

    Re-running from the original objective repeats the original mistake — the model never
    learns what went wrong. This submits a fresh job whose objective carries the specific
    defects the last run produced, so the retry is a correction rather than another guess.
    """
    if not _resource_access(request, "job", job_id):
        raise HTTPException(404, "Job not found")
    detail = job_runner.get(job_id)
    if detail is None:
        raise HTTPException(404, "Job not found")
    if detail["status"] not in FINISHED_JOB_STATES:
        raise HTTPException(409, "That request is still running. Wait for it, or cancel it.")

    original = dict(detail.get("request") or {})
    if not original.get("objective"):
        raise HTTPException(409, "That request cannot be retried — its inputs were not recorded.")
    preview = (detail.get("result") or {}).get("preview") or {}
    brief = smart_code_service.correction_brief(preview, str(payload.get("instruction", "")))
    original["objective"] = (
        f"{original['objective']}\n\n---\nCORRECTION\n{brief}"
    )

    owner = actor_id(request)
    ensure_job_capacity(owner)
    job = job_runner.submit(
        "smart-code", f"Fix: {str(original['objective'])[:100]}", original, owner_id=owner
    )
    return {"job_id": str(job.id), "corrected_from": str(job_id)}


@app.post("/api/smart-code/apply")
def smart_code_apply(payload: SmartCodeApplyRequest, request: Request):
    try:
        owner = actor_id(request)
        return smart_code_service.apply(payload, str(owner) if owner else None)
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


async def estimate_one(
    story, context: JobContext, index: int, total: int, workspace_root: str = ""
) -> dict:
    """Estimate a single story, reporting progress against the shared job context."""
    run = run_ledger.start(
        "estimate-code", metadata={"source": story.source, "model": settings.model_id}
    )
    progress_queue: asyncio.Queue[dict] = asyncio.Queue()

    def progress(event: dict):
        progress_queue.put_nowait(event)

    prefix = f"[{index + 1}/{total}] " if total > 1 else ""
    # EAGLE §10: the estimate is anchored against this owner's own history. Read here rather
    # than inside the service so the service stays free of database concerns, and scoped to the
    # owner because another team's velocity is not evidence about this one.
    history = await asyncio.to_thread(reference_corpus, engine, context.owner_id)
    task = asyncio.create_task(
        estimate_service.estimate(story, progress, history, workspace_root)
    )
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
        await run.finish_async(
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
                save_estimate, engine, result, context.job_id, context.owner_id
            )
            result["history_id"] = str(record.id)
        except Exception:
            # History is a side effect of estimating. Losing it must never cost the user
            # the estimate they are waiting for.
            logger.exception("Could not record the estimate in history")
        return result
    except asyncio.CancelledError:
        task.cancel()
        run.finish("cancelled")  # see run_chat_job: no awaiting on the cancellation path
        raise
    except Exception as exc:
        logger.exception("Estimate Code failed")
        run.event("error", "failed", "Estimate failed validation", detail=str(exc)[:500])
        await run.finish_async("failed", summary={"error_type": type(exc).__name__})
        raise


async def run_estimate_job(request: dict, context: JobContext) -> dict:
    """One job covers a single story or a whole batch; stories run sequentially."""
    stories = [Story.model_validate(item) for item in request["stories"]]
    workspace_root = str(request.get("workspace_root") or "")
    total = len(stories)
    results: list[dict] = []
    failures: list[dict] = []
    for index, story in enumerate(stories):
        try:
            results.append(await estimate_one(story, context, index, total, workspace_root))
            # Publish after each story. Waiting for all of them means a long batch shows
            # nothing for half an hour despite having finished useful work minutes in.
            if total > 1:
                await context.partial(
                    {"results": results, "failures": failures, "count": total}
                )
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


def submit_estimate_job(
    stories: list, owner_id: UUID | None = None, workspace_root: str = ""
) -> dict:
    title = stories[0].title if len(stories) == 1 else f"{len(stories)} stories"
    job = job_runner.submit(
        "estimate", title,
        {
            "stories": [item.model_dump(mode="json") for item in stories],
            # Stored on the job so a reattaching client and a restored run see the same
            # repository the estimate was made against.
            "workspace_root": workspace_root,
        },
        owner_id=owner_id,
    )
    return {"job_id": str(job.id), "count": len(stories)}


@app.post("/api/estimate-code/jobs", status_code=202)
def submit_estimate(payload: EstimateRequest, request: Request):
    owner = actor_id(request)
    ensure_job_capacity(owner)
    return submit_estimate_job([payload.story], owner, payload.workspace_root)


@app.post("/api/estimate-code/batch-jobs", status_code=202)
def submit_estimate_batch(payload: BatchEstimateRequest, request: Request):
    owner = actor_id(request)
    ensure_job_capacity(owner)
    return submit_estimate_job(list(payload.stories), owner, payload.workspace_root)


@app.get("/api/estimate-code/history")
def estimate_history(
    request: Request,
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
        owner_id=actor_id(request),
    )


@app.get("/api/estimate-code/history/stats")
def estimate_history_stats(request: Request):
    """Aggregates that let a team see how they estimate, not just what they estimated."""
    return estimate_stats(engine, actor_id(request))


@app.get("/api/estimate-code/history/{record_id}")
def estimate_history_detail(record_id: UUID, request: Request):
    if not _resource_access(request, "estimate", record_id):
        raise HTTPException(404, "That estimate is no longer in history.")
    record = get_estimate(engine, record_id)
    if record is None:
        raise HTTPException(404, "That estimate is no longer in history.")
    record["access"] = {
        "owner": not settings.auth_enabled or _resource_owner("estimate", str(record_id)) == actor_id(request),
        "permission": permission_for(engine, actor_id(request), "estimate", str(record_id))
        if actor_id(request) else None,
    }
    return record


@app.post("/api/estimate-code/history/{record_id}/decision")
def estimate_history_decision(record_id: UUID, request: Request, payload: dict = Body(...)):
    """Record what the team decided. This is the step that closes the pipeline.

    Every estimate ends at "human decision required". Without somewhere to put the answer the
    recommendation is the last word, and calibration can only ever report what was estimated
    rather than whether the estimate held.
    """
    decision = str(payload.get("decision", "")).strip()
    if not _resource_access(request, "estimate", record_id, edit=True):
        raise HTTPException(404, "That estimate is no longer in history.")
    points = payload.get("points")
    actual = payload.get("actual_points")
    if decision == "override" and points is None:
        raise HTTPException(400, "An override must supply the points the team agreed on.")
    try:
        record = record_decision(
            engine, record_id, decision,
            points=int(points) if points is not None else None,
            note=str(payload.get("note", "")),
            actual_points=int(actual) if actual is not None else None,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if record is None:
        raise HTTPException(404, "That estimate is no longer in history.")
    return record


@app.delete("/api/estimate-code/history/{record_id}", status_code=204)
def estimate_history_delete(record_id: UUID, request: Request):
    if not delete_estimate(engine, record_id, actor_id(request)):
        raise HTTPException(404, "That estimate is no longer in history.")


@app.post("/api/estimate-code/history/clear")
def estimate_history_clear(request: Request, payload: dict = Body(default={})):
    if not payload.get("confirm"):
        raise HTTPException(400, "Explicit confirmation is required to clear history.")
    return {"deleted": clear_estimates(engine, actor_id(request))}


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
def estimate_upload_rows(request: Request, payload: dict = Body(...)):
    try:
        stack = StackProfile.model_validate(payload.get("stack") or {})
        stories = rows_to_stories(payload.get("rows") or [], payload.get("mapping") or {}, stack)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    owner = actor_id(request)
    ensure_job_capacity(owner)
    return submit_estimate_job(stories, owner)


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
