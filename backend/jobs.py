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

from sqlalchemy import Column, JSON, delete, func, update
from sqlmodel import Field, Session, SQLModel, col, select

from backend.auth import User  # noqa: F401 — registers the foreign-key target in metadata
from backend.db import utc_iso

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

#: Live messages held for one attached viewer before its backlog is trimmed. Generous enough
#: that no healthy client is ever affected, small enough that a stalled one cannot grow the
#: worker's memory for the length of a run.
MAX_PENDING_MESSAGES = 2048

#: Longest shutdown will wait for the worker before abandoning it. Shutdown must be bounded:
#: an unbounded await here means a stuck database call stops the process from exiting at all.
STOP_TIMEOUT_SECONDS = 5.0

#: Backstop poll for queued work. Submissions wake the worker directly, so this only has to
#: catch a row this process did not insert itself — nothing in a single-process install. It is
#: deliberately long: at the old 0.2s the idle application ran five SQLite queries every
#: second forever, which on a laptop is a wakeup cost paid for nothing.
IDLE_POLL_SECONDS = 5.0


def now() -> datetime:
    return datetime.now(timezone.utc)


class Job(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_id: UUID | None = Field(default=None, foreign_key="user.id", index=True)
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
        "created_at": utc_iso(job.created_at),
        "started_at": utc_iso(job.started_at),
        "completed_at": utc_iso(job.completed_at),
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

    def __init__(
        self, runner: "JobRunner", job_id: UUID, started: float, owner_id: UUID | None = None
    ):
        self.runner = runner
        self.job_id = job_id
        self.owner_id = owner_id
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

    async def partial(self, result: dict[str, Any]) -> None:
        """Publish a result the job has produced but not finished with.

        A ten-story batch takes half an hour on CPU, and story one is done in the first
        three minutes. Persisting the partial result means a reattaching client sees it in
        its snapshot too, not only clients that happened to be watching when it landed.
        """
        await asyncio.to_thread(self.runner.merge_result, self.job_id, result)
        self.runner.publish(self.job_id, {"type": "partial", "result": result})

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
        #: Cancellations that arrived while a job was claimed but its task not yet registered.
        #: Without this the request is silently dropped and the job runs to completion while
        #: the user is told it was cancelled.
        self._cancel_requested: set[UUID] = set()
        #: Set when work is submitted, so the worker starts immediately instead of waiting
        #: out a poll interval. Captured at start() because submission runs on FastAPI's
        #: threadpool and cannot touch loop primitives directly.
        self._loop: asyncio.AbstractEventLoop | None = None
        #: Created in start(), not here. An asyncio.Event binds to the first loop that
        #: awaits it and refuses every later one, and this runner is a module-level
        #: singleton that outlives any single loop — each test client, and any in-place
        #: restart, brings a new one.
        self._wakeup: asyncio.Event | None = None
        self._stopping = False

    def _wake(self) -> None:
        """Signal the worker from any thread. Failure is harmless — the poll still fires."""
        loop, wakeup = self._loop, self._wakeup
        if loop is None or wakeup is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(wakeup.set)
        except RuntimeError:
            pass

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

    def merge_result(self, job_id: UUID, partial: dict) -> None:
        """Merge an in-flight result into the job so a snapshot carries it."""
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job is None:
                return
            job.result = {**(job.result or {}), **partial}
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
            # COUNT in SQL. Loading the rows to call len() on them re-read the whole
            # trajectory on every event, so a run emitting n events did O(n²) row loads —
            # and an estimate emits one per pipeline stage, per story.
            existing = session.exec(
                select(func.count()).select_from(JobEvent).where(JobEvent.job_id == job_id)
            ).one()
            if int(existing) >= MAX_EVENTS_PER_JOB:
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
    ) -> bool:
        """Move a job to a terminal state, once. Returns whether this call is the one that did.

        The guard is a conditional UPDATE, not a read-then-write, because two paths race
        here for real. A job whose handler has just returned is written as ``succeeded``
        from a database thread; if shutdown cancels the worker before that await resumes,
        cancellation is delivered *after* the row already says succeeded, and the handler
        for it would rewrite the row as ``cancelled`` — throwing away a result the user had
        already earned and would see disappear on reopening the tab. Terminal is terminal.
        """
        with Session(self.engine) as session:
            changed = session.execute(
                update(Job)
                .where(col(Job.id) == job_id, col(Job.status).not_in(tuple(FINISHED)))
                .values(status=status, completed_at=now(), result=result, error=error)
            ).rowcount
            session.commit()
            return bool(changed)

    # -- public API --------------------------------------------------------------------

    def submit(
        self,
        kind: str,
        title: str,
        request: dict,
        *,
        owner_id: UUID | None = None,
        **columns: Any,
    ) -> Job:
        """Persist a queued job and hand it to the worker. Returns immediately."""
        if kind not in self.handlers:
            raise ValueError(f"No handler is registered for job kind {kind!r}")
        with Session(self.engine) as session:
            job = Job(
                kind=kind, title=title[:200], request=request, owner_id=owner_id, **columns
            )
            session.add(job)
            session.commit()
            session.refresh(job)
        # The worker claims from the database rather than an in-memory queue, so a queued
        # row survives a restart. The wake is only an optimisation on top of that: it saves
        # the poll interval, and losing it costs latency, never work.
        self._wake()
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

    def list(
        self, limit: int = 50, kind: str | None = None, owner_id: UUID | None = None
    ) -> list[dict[str, Any]]:
        with Session(self.engine) as session:
            statement = select(Job).order_by(Job.created_at.desc()).limit(limit)
            if kind:
                statement = statement.where(Job.kind == kind)
            if owner_id is not None:
                statement = statement.where(Job.owner_id == owner_id)
            return [job_summary(item) for item in session.exec(statement).all()]

    def active_count(self, owner_id: UUID | None = None) -> int:
        with Session(self.engine) as session:
            statement = (
                select(func.count()).select_from(Job)
                .where(col(Job.status).in_(("queued", "running")))
            )
            if owner_id is not None:
                statement = statement.where(Job.owner_id == owner_id)
            return int(
                session.exec(statement).one()
            )

    def owner(self, job_id: UUID) -> UUID | None:
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            return job.owner_id if job else None

    def cancel_all(self, owner_id: UUID) -> int:
        with Session(self.engine) as session:
            ids = session.exec(
                select(Job.id).where(
                    Job.owner_id == owner_id, col(Job.status).in_(("queued", "running"))
                )
            ).all()
        return sum(1 for job_id in ids if self.cancel(job_id))

    def cancel(self, job_id: UUID) -> bool:
        """Cancel a queued or running job. Returns False when it is already finished."""
        with Session(self.engine) as session:
            job = session.get(Job, job_id)
            if job is None or job.status in FINISHED:
                return False
            status = job.status
        if self._current and self._current[0] == job_id:
            task = self._current[1]
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(task.cancel)
            else:
                task.cancel()
            return True
        if status == "running":
            # Claimed, but its task is not registered yet: the worker marks a job running in a
            # database thread and only registers the task once control returns to the loop.
            # Recording the request here means the worker cancels it the moment it can, rather
            # than running the whole job while the user is told it stopped.
            self._cancel_requested.add(job_id)
            return True
        if self._finish(job_id, "cancelled", error="Cancelled before it started"):
            self.publish(job_id, {"type": "done", "status": "cancelled"})
        return True

    # -- live broadcast ----------------------------------------------------------------

    def subscribe(self, job_id: UUID) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=MAX_PENDING_MESSAGES)
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
        """Fan out to attached viewers, never blocking the run on a slow one.

        A viewer that stops reading — a backgrounded tab, a paused debugger, a dropped
        connection whose task has not been reaped yet — must not make the worker's memory
        grow without limit for the rest of a multi-minute generation. Its backlog is
        trimmed instead, and it recovers the lost text from the next snapshot, which is
        exactly what the offset protocol already exists to handle. Terminal messages are
        never dropped: they are what tell a viewer to stop waiting.
        """
        terminal = message.get("type") == "done"
        for queue in self._subscribers.get(job_id, ()):
            while queue.full():
                try:
                    dropped = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if dropped.get("type") == "done":
                    # Re-queueing is impossible here, so deliver it and let the trimmed
                    # message go instead; a viewer that misses "done" waits forever.
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(dropped)
                    break
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                if terminal:
                    logger.warning("Dropped a terminal message for job %s: viewer stalled", job_id)

    # -- lifecycle ---------------------------------------------------------------------

    def reconcile(self) -> int:
        """Fail over jobs left mid-flight by a previous process, and purge old ones.

        A generation cannot be resumed, so an orphan is marked ``interrupted`` rather than
        being silently retried or left claiming to be running forever.
        """
        cutoff = now() - timedelta(days=self.retention_days)
        with Session(self.engine) as session:
            # Set-based, so startup cost does not grow with history. The previous version
            # loaded every expired job and each of its events as ORM objects purely to
            # delete them, which made the first request after a long gap the slowest.
            orphaned = int(
                session.execute(
                    update(Job)
                    .where(col(Job.status).in_(("queued", "running")))
                    .values(
                        status="interrupted",
                        completed_at=now(),
                        error="The backend restarted before this request finished.",
                    )
                ).rowcount
            )
            expired = select(col(Job.id)).where(col(Job.created_at) < cutoff)
            session.execute(
                delete(JobEvent).where(col(JobEvent.job_id).in_(expired))
            )
            session.execute(delete(Job).where(col(Job.created_at) < cutoff))
            session.commit()
        if orphaned:
            logger.info("Marked %d unfinished job(s) as interrupted after restart", orphaned)
        return orphaned

    async def start(self) -> None:
        # A runner can be started again after stopping — every TestClient does exactly that,
        # and so does any process that restarts the app in place. Leaving the stop flag set
        # would give the new worker a loop condition that is already false, so it would exit
        # at once and every job submitted afterwards would sit queued forever.
        self._stopping = False
        self._wakeup = asyncio.Event()
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self.reconcile)
        self._worker = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        """Stop the worker without ever blocking shutdown indefinitely.

        Cancelling a task that is awaiting ``asyncio.to_thread`` does not interrupt the
        thread: the cancellation only lands once the thread returns. A database call that
        is waiting on a lock can therefore hold shutdown open for as long as SQLite's busy
        timeout — and if the thread's result can no longer be delivered to this loop, the
        await never finishes at all. Both of those turn "close the app" into a hang, so the
        wait is bounded and the worker is abandoned rather than waited on forever. Nothing
        is lost by giving up: the job row is durable, and the next start reconciles it.
        """
        self._stopping = True
        if self._wakeup is not None:
            self._wakeup.set()
        if self._current:
            self._current[1].cancel()
        if self._worker:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(asyncio.shield(self._worker), STOP_TIMEOUT_SECONDS)

    def _next_candidate(self, session: Session) -> Job | None:
        """Choose the next job to run, fairly across users.

        Concurrency is one job at a time — the model runtime serializes generation behind a
        single lock, so a wider pool would only queue inside the model. That makes the *order*
        of the queue the only lever there is, and strict FIFO is the wrong one as soon as a
        second person uses the application.

        A ten-story batch takes twenty minutes per story. Under FIFO the colleague who sends a
        ninety-second chat message one moment later waits more than three hours behind it,
        which does not read as a slow machine — it reads as a broken product. Neither user did
        anything unreasonable.

        So the queue is served round-robin by owner: among users with queued work, the one who
        has waited longest since a job of theirs last *started* goes next, and a user who has
        never run anything goes first. Within one user, their own jobs stay strictly FIFO.
        Everybody's first job therefore starts before anybody's second, and a long batch costs
        its owner throughput rather than costing everyone else availability.

        With authentication disabled every job has no owner, the group collapses to one, and
        this is exactly FIFO again.
        """
        queued = list(
            session.exec(select(Job).where(Job.status == "queued").order_by(col(Job.created_at)))
        )
        if not queued:
            return None
        # When did each owner last have a job start? Never-served owners sort first.
        last_started: dict[UUID | None, datetime] = {}
        for owner, started in session.exec(
            select(Job.owner_id, func.max(Job.started_at))
            .where(col(Job.started_at).is_not(None))
            .group_by(col(Job.owner_id))
        ).all():
            if started is not None:
                last_started[owner] = started

        epoch = datetime.min.replace(tzinfo=timezone.utc)

        def served_at(job: Job) -> datetime:
            when = last_started.get(job.owner_id)
            if when is None:
                return epoch
            return when if when.tzinfo else when.replace(tzinfo=timezone.utc)

        def created_at(job: Job) -> datetime:
            value = job.created_at
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

        # Least-recently-served owner first; their own oldest job within that.
        return min(queued, key=lambda job: (served_at(job), created_at(job)))

    def claim_next(self) -> tuple[UUID, str, dict, UUID | None] | None:
        """Take the next queued job and mark it running, exactly once.

        The transition is a conditional UPDATE rather than a read-then-write, so two
        workers racing on the same database cannot both claim the same row: only the
        update whose ``WHERE status = 'queued'`` still matches reports a changed row.
        """
        with Session(self.engine) as session:
            candidate = self._next_candidate(session)
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
            return candidate.id, candidate.kind, dict(candidate.request), candidate.owner_id

    async def _run_forever(self) -> None:
        # Bound once here: the attribute is replaced on the next start(), and this loop must
        # keep using the event belonging to the loop it is actually running on.
        wakeup = self._wakeup or asyncio.Event()
        while not self._stopping:
            wakeup.clear()
            claimed = await asyncio.to_thread(self.claim_next)
            if claimed is None:
                # Wait to be told, with a timeout so a row inserted by anything other than
                # this process is still picked up.
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wakeup.wait(), timeout=IDLE_POLL_SECONDS)
                continue
            job_id, kind, request, owner_id = claimed
            try:
                await self._execute(job_id, kind, request, owner_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Job worker failed on %s", job_id)

    async def _complete(self, context: JobContext, job_id: UUID, result: dict) -> None:
        """Persist a finished run's output and announce it."""
        await context.flush()
        if await asyncio.to_thread(self._finish, job_id, "succeeded", result=result):
            self.publish(job_id, {"type": "done", "status": "succeeded", "result": result})

    async def _execute(
        self, job_id: UUID, kind: str, request: dict, owner_id: UUID | None = None
    ) -> None:
        if kind not in self.handlers:
            await asyncio.to_thread(
                self._finish, job_id, "failed", error=f"No handler for job kind {kind!r}"
            )
            return

        self.publish(job_id, {"type": "status", "message": "Started"})
        self._live_text[job_id] = ""
        context = JobContext(self, job_id, asyncio.get_running_loop().time(), owner_id)
        task = asyncio.create_task(self.handlers[kind](request, context))
        self._current = (job_id, task)
        if job_id in self._cancel_requested:
            task.cancel()
        try:
            result = await task
            # Shielded: once the handler has returned, the result exists and must be stored.
            # Cancelling the worker at this instant — which is exactly what shutdown does —
            # must not discard completed work between producing it and recording it.
            await asyncio.shield(
                asyncio.create_task(self._complete(context, job_id, result))
            )
        except asyncio.CancelledError:
            await context.flush()
            if await asyncio.to_thread(
                self._finish, job_id, "cancelled", error="Cancelled by the user"
            ):
                self.publish(job_id, {"type": "done", "status": "cancelled"})
        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            await context.flush()
            message = str(exc)[:1000] or exc.__class__.__name__
            if await asyncio.to_thread(self._finish, job_id, "failed", error=message):
                self.publish(job_id, {"type": "done", "status": "failed", "error": message})
        finally:
            self._current = None
            self._live_text.pop(job_id, None)
            self._cancel_requested.discard(job_id)
