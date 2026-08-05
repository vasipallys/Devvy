"""Durable background jobs: work that outlives the browser tab that started it.

Every model-backed request is submitted as a *job* and executed by an in-process worker
that has no reference to the HTTP connection which created it. Closing the tab, losing the
network, or navigating away cannot cancel work or discard a result — the connection is only
a view onto a job, never the thing keeping it alive.

Two storage layers, deliberately split:

* **Durable** (SQLite): status, progress label, streamed output so far, the final result,
  and a bounded evidence trajectory. This is what a returning browser reads.
* **Live** (in-memory broadcast): token deltas and events for clients attached right now.

A reconnecting client is sent a ``snapshot`` built from the durable layer and is then joined
to the live broadcast, so it sees the whole run regardless of when it attached.

Concurrency is deliberately one job at a time: ``GemmaRuntime`` already serializes generation
behind a single lock, so a wider pool would only queue inside the model instead of here,
while making progress reporting dishonest.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from sqlalchemy import Column, JSON, update
from sqlmodel import Field, Session, SQLModel, select

logger = logging.getLogger(__name__)

JobKind = Literal["chat", "estimate", "smart-code", "talk"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "interrupted"]

#: Terminal states. A job in one of these will never change again.
FINISHED: frozenset[str] = frozenset({"succeeded", "failed", "cancelled", "interrupted"})

#: Streamed text is flushed to SQLite at most this often. Persisting every token would turn
#: a CPU generation into a write-per-token workload for no benefit: a returning client only
#: ever reads the latest snapshot.
FLUSH_INTERVAL_SECONDS = 0.75

#: Evidence events retained per job. Bounds table growth on long runs.
MAX_EVENTS_PER_JOB = 200

#: How often the idle worker looks for newly queued work. Submission happens on FastAPI's
#: threadpool, so a database poll is both simpler and safer than cross-thread signalling.
IDLE_POLL_SECONDS = 0.2


def now() -> datetime:
    return datetime.now(timezone.utc)


class Job(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    kind: str = Field(index=True)
    status: str = Field(default="queued", index=True)
    title: str = ""
    progress: str = ""
    created_at: datetime = Field(default_factory=now, index=True)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    #: The submitted payload, replayed to the handler by the worker.
    request: dict = Field(default_factory=dict, sa_column=Column(JSON))
    #: Assistant text accumulated so far. Present while running and after completion.
    output_text: str = ""
    result: dict | None = Field(default=None, sa_column=Column(JSON))
    error: str | None = None
    #: Set for chat jobs so the UI can jump straight to the conversation.
    conversation_id: UUID | None = Field(default=None, index=True)


class JobEvent(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    job_id: UUID = Field(index=True)
    seq: int
    stage: str
    status: str
    label: str
    detail: str | None = None
    evidence: dict | None = Field(default=None, sa_column=Column(JSON))
    elapsed_ms: int = 0
    created_at: datetime = Field(default_factory=now)


def job_summary(job: Job) -> dict[str, Any]:
    """List-view projection: everything the activity list needs, without the payloads."""
    return {
        "id": str(job.id),
        "kind": job.kind,
        "status": job.status,
        "title": job.title,
        "progress": job.progress,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "conversation_id": str(job.conversation_id) if job.conversation_id else None,
        "error": job.error,
        "has_result": job.result is not None,
        "output_preview": job.output_text[:280],
    }


def job_detail(job: Job, events: list[JobEvent]) -> dict[str, Any]:
    return {
        **job_summary(job),
        "request": job.request,
        "result": job.result,
        "output_text": job.output_text,
        "events": [
            {
                "run_id": str(job.id),
                "seq": item.seq,
                "stage": item.stage,
                "status": item.status,
                "label": item.label,
                "detail": item.detail,
                "evidence": item.evidence,
                "elapsed_ms": item.elapsed_ms,
            }
            for item in events
        ],
    }


class JobContext:
    """Handed to a handler so it can report progress without knowing about transports."""

    def __init__(self, runner: "JobRunner", job_id: UUID, started: float):
        self.runner = runner
        self.job_id = job_id
        self._started = started
        self._seq = 0
        self._buffer: list[str] = []
        self._flushed_at = 0.0

    @property
    def elapsed_ms(self) -> int:
        return round((asyncio.get_running_loop().time() - self._started) * 1000)

    async def token(self, text: str) -> None:
        """Stream a text delta to live viewers, flushing to storage on a timer.

        Each delta carries the character offset it starts at. A client that attaches
        mid-run receives a snapshot and then live deltas, and some of those deltas may
        already be inside the snapshot; the offset lets it drop those instead of
        duplicating text. Offsets are exact because the runner keeps the authoritative
        in-memory text for the running job, ahead of the throttled database flush.
        """
        offset = self.runner.extend_live_text(self.job_id, text)
        self._buffer.append(text)
        self.runner.publish(self.job_id, {"type": "token", "content": text, "offset": offset})
        loop = asyncio.get_running_loop()
        if loop.time() - self._flushed_at >= FLUSH_INTERVAL_SECONDS:
            await self.flush()

    async def flush(self) -> None:
        if not self._buffer:
            return
        chunk = "".join(self._buffer)
        self._buffer.clear()
        self._flushed_at = asyncio.get_running_loop().time()
        await asyncio.to_thread(self.runner.append_output, self.job_id, chunk)

    async def event(
        self,
        stage: str,
        status: str,
        label: str,
        *,
        detail: str | None = None,
        evidence: dict | None = None,
    ) -> dict[str, Any]:
        self._seq += 1
        payload = {
            "run_id": str(self.job_id),
            # Monotonic per job. A client that attaches mid-run gets these events in its
            # snapshot *and* on the live feed; the sequence lets it drop the overlap
            # instead of rendering each one twice.
            "seq": self._seq,
            "stage": stage,
            "status": status,
            "label": label,
            "detail": detail,
            "evidence": evidence,
            "elapsed_ms": self.elapsed_ms,
        }
        await asyncio.to_thread(
            self.runner.record_event, self.job_id, self._seq, payload
        )
        self.runner.publish(self.job_id, {"type": "agent_event", **payload})
        return payload

    async def progress(self, message: str) -> None:
        await asyncio.to_thread(self.runner.set_progress, self.job_id, message)
        self.runner.publish(self.job_id, {"type": "status", "message": message})


#: A handler receives the submitted request and a context, and returns the job result.
JobHandler = Callable[[dict[str, Any], JobContext], Awaitable[dict[str, Any]]]


class JobRunner:
    """Owns the job queue, the worker task, and the live broadcast fan-out."""

    def __init__(self, engine, retention_days: int = 7):
        self.engine = engine
        self.retention_days = max(1, retention_days)
        self.handlers: dict[str, JobHandler] = {}
        self._subscribers: dict[UUID, set[asyncio.Queue[dict]]] = {}
        self._worker: asyncio.Task | None = None
        self._current: tuple[UUID, asyncio.Task] | None = None
        #: Authoritative streamed text for the in-flight job. The database copy lags behind
        #: it by up to FLUSH_INTERVAL_SECONDS, so snapshots read from here while running.
        self._live_text: dict[UUID, str] = {}

    def extend_live_text(self, job_id: UUID, text: str) -> int:
        """Append a delta and return the offset it started at."""
        current = self._live_text.get(job_id, "")
        self._live_text[job_id] = current + text
        return len(current)

    def snapshot(self, job_id: UUID) -> dict[str, Any] | None:
        """Job detail with the freshest available output text."""
        detail = self.get(job_id)
        if detail is None:
            return None
        live = self._live_text.get(job_id)
        if live is not None and len(live) > len(detail["output_text"]):
            detail["output_text"] = live
        return detail

    # -- registration ------------------------------------------------------------------

    def register(self, kind: str, handler: JobHandler) -> None:
        self.handlers[kind] = handler

    # -- storage helpers (synchronous; always called via asyncio.to_thread) --------------

    def _load(self, session: Session, job_id: UUID) -> Job | None:
        return session.get(Job, job_id)

    def append_output(self, job_id: UUID, chunk: str) -> None:
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.output_text += chunk
            session.add(job)
            session.commit()

    def set_progress(self, job_id: UUID, message: str) -> None:
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.progress = message
            session.add(job)
            session.commit()

    def record_event(self, job_id: UUID, seq: int, payload: dict) -> None:
        with Session(self.engine) as session:
            existing = session.exec(
                select(JobEvent).where(JobEvent.job_id == job_id)
            ).all()
            if len(existing) >= MAX_EVENTS_PER_JOB:
                return
            session.add(
                JobEvent(
                    job_id=job_id,
                    seq=seq,
                    stage=str(payload.get("stage", "")),
                    status=str(payload.get("status", "")),
                    label=str(payload.get("label", "")),
                    detail=payload.get("detail"),
                    evidence=payload.get("evidence"),
                    elapsed_ms=int(payload.get("elapsed_ms", 0)),
                )
            )
            session.commit()

    def _finish(
        self, job_id: UUID, status: str, *, result: dict | None = None, error: str | None = None
    ) -> None:
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.status = status
            job.completed_at = now()
            job.result = result
            job.error = error
            session.add(job)
            session.commit()

    # -- public API --------------------------------------------------------------------

    def submit(self, kind: str, title: str, request: dict, **columns: Any) -> Job:
        """Persist a queued job and hand it to the worker. Returns immediately."""
        if kind not in self.handlers:
            raise ValueError(f"No handler is registered for job kind {kind!r}")
        with Session(self.engine) as session:
            job = Job(kind=kind, title=title[:200], request=request, **columns)
            session.add(job)
            session.commit()
            session.refresh(job)
        # The worker claims from the database rather than an in-memory queue. Submissions
        # arrive from FastAPI's threadpool, where signalling an asyncio primitive would not
        # be thread-safe, and a queued row already survives a restart.
        return job

    def get(self, job_id: UUID) -> dict[str, Any] | None:
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return None
            events = session.exec(
                select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.seq)
            ).all()
            return job_detail(job, list(events))

    def list(self, limit: int = 50, kind: str | None = None) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = select(Job).order_by(Job.created_at.desc()).limit(limit)
            if kind:
                statement = statement.where(Job.kind == kind)
            return [job_summary(item) for item in session.exec(statement).all()]

    def active_count(self) -> int:
        with Session(self.engine) as session:
            return len(
                session.exec(
                    select(Job).where(Job.status.in_(("queued", "running")))
                ).all()
            )

    def cancel(self, job_id: UUID) -> bool:
        """Cancel a queued or running job. Returns False when it is already finished."""
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job is None or job.status in FINISHED:
                return False
        if self._current and self._current[0] == job_id:
            self._current[1].cancel()
        else:
            self._finish(job_id, "cancelled", error="Cancelled before it started")
            self.publish(job_id, {"type": "done", "status": "cancelled"})
        return True

    # -- live broadcast ----------------------------------------------------------------

    def subscribe(self, job_id: UUID) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue()
        self._subscribers.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe(self, job_id: UUID, queue: asyncio.Queue[dict]) -> None:
        listeners = self._subscribers.get(job_id)
        if not listeners:
            return
        listeners.discard(queue)
        if not listeners:
            self._subscribers.pop(job_id, None)

    def publish(self, job_id: UUID, message: dict) -> None:
        for queue in self._subscribers.get(job_id, ()):
            queue.put_nowait(message)

    # -- lifecycle ---------------------------------------------------------------------

    def reconcile(self) -> int:
        """Fail over jobs left mid-flight by a previous process, and purge old ones.

        A generation cannot be resumed, so an orphan is marked ``interrupted`` rather than
        being silently retried or left claiming to be running forever.
        """
        cutoff = now() - timedelta(days=self.retention_days)
        orphaned = 0
        with Session(self.engine) as session:
            for job in session.exec(
                select(Job).where(Job.status.in_(("queued", "running")))
            ).all():
                job.status = "interrupted"
                job.completed_at = now()
                job.error = "The backend restarted before this request finished."
                session.add(job)
                orphaned += 1
            for job in session.exec(select(Job).where(Job.created_at < cutoff)).all():
                for event in session.exec(
                    select(JobEvent).where(JobEvent.job_id == job.id)
                ).all():
                    session.delete(event)
                session.delete(job)
            session.commit()
        if orphaned:
            logger.info("Marked %d unfinished job(s) as interrupted after restart", orphaned)
        return orphaned

    async def start(self) -> None:
        await asyncio.to_thread(self.reconcile)
        self._worker = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._current:
            self._current[1].cancel()
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker

    def claim_next(self) -> tuple[UUID, str, dict] | None:
        """Take the oldest queued job and mark it running, exactly once.

        The transition is a conditional UPDATE rather than a read-then-write, so two
        workers racing on the same database cannot both claim the same row: only the
        update whose ``WHERE status = 'queued'`` still matches reports a changed row.
        """
        with Session(self.engine) as session:
            candidate = session.exec(
                select(Job).where(Job.status == "queued").order_by(Job.created_at).limit(1)
            ).first()
            if candidate is None:
                return None
            claimed = session.execute(
                update(Job)
                .where(Job.id == candidate.id, Job.status == "queued")
                .values(status="running", started_at=now())
            )
            session.commit()
            if claimed.rowcount != 1:
                return None  # Another worker took it; the next poll picks up the rest.
            session.refresh(candidate)
            return candidate.id, candidate.kind, dict(candidate.request)

    async def _run_forever(self) -> None:
        while True:
            claimed = await asyncio.to_thread(self.claim_next)
            if claimed is None:
                await asyncio.sleep(IDLE_POLL_SECONDS)
                continue
            job_id, kind, request = claimed
            try:
                await self._execute(job_id, kind, request)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Job worker failed on %s", job_id)

    async def _execute(self, job_id: UUID, kind: str, request: dict) -> None:
        if kind not in self.handlers:
            await asyncio.to_thread(
                self._finish, job_id, "failed", error=f"No handler for job kind {kind!r}"
            )
            return

        self.publish(job_id, {"type": "status", "message": "Started"})
        self._live_text[job_id] = ""
        context = JobContext(self, job_id, asyncio.get_running_loop().time())
        task = asyncio.create_task(self.handlers[kind](request, context))
        self._current = (job_id, task)
        try:
            result = await task
            await context.flush()
            await asyncio.to_thread(self._finish, job_id, "succeeded", result=result)
            self.publish(job_id, {"type": "done", "status": "succeeded", "result": result})
        except asyncio.CancelledError:
            await context.flush()
            await asyncio.to_thread(
                self._finish, job_id, "cancelled", error="Cancelled by the user"
            )
            self.publish(job_id, {"type": "done", "status": "cancelled"})
        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            await context.flush()
            message = str(exc)[:1000] or exc.__class__.__name__
            await asyncio.to_thread(self._finish, job_id, "failed", error=message)
            self.publish(job_id, {"type": "done", "status": "failed", "error": message})
        finally:
            self._current = None
            self._live_text.pop(job_id, None)
