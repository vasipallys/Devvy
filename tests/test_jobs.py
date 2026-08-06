"""Durability guarantees for background jobs.

The product claim is that a request survives the browser tab that started it. These tests
exercise that directly: work continues with no subscriber attached, a late subscriber can
reconstruct the whole run, and a process restart never leaves a job claiming to be running.
"""

import asyncio

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from backend.jobs import STOP_TIMEOUT_SECONDS, Job, JobContext, JobRunner


@pytest.fixture
def runner(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'jobs.db').as_posix()}")
    SQLModel.metadata.create_all(engine)
    yield JobRunner(engine, retention_days=7)
    # Windows will not delete the temp directory while the pool still holds the file.
    engine.dispose()


async def drain(runner: JobRunner, job_id, timeout: float = 5.0) -> dict:
    """Wait for a job to reach a terminal state."""
    async with asyncio.timeout(timeout):
        while True:
            detail = runner.get(job_id)
            if detail and detail["status"] in {"succeeded", "failed", "cancelled", "interrupted"}:
                return detail
            await asyncio.sleep(0.02)


async def test_work_continues_with_no_subscriber_attached(runner):
    """The whole point: nobody is watching, and the job still finishes and keeps its result."""
    started = asyncio.Event()

    async def handler(request, context: JobContext):
        started.set()
        await context.event("work", "running", "Working")
        await context.token("hello ")
        await context.token("world")
        await asyncio.sleep(0.05)
        return {"answer": request["q"].upper()}

    runner.register("demo", handler)
    await runner.start()
    try:
        job = runner.submit("demo", "demo job", {"q": "ping"})
        await asyncio.wait_for(started.wait(), timeout=5)
        detail = await drain(runner, job.id)
    finally:
        await runner.stop()

    assert detail["status"] == "succeeded"
    assert detail["result"] == {"answer": "PING"}
    assert detail["output_text"] == "hello world", "streamed text is persisted, not just relayed"
    assert [event["label"] for event in detail["events"]] == ["Working"]


async def test_a_late_subscriber_can_reconstruct_the_run(runner):
    """Attaching mid-flight yields a snapshot plus only the deltas that follow it."""
    release = asyncio.Event()

    async def handler(_request, context: JobContext):
        await context.token("first ")
        await release.wait()
        await context.token("second")
        return {}

    runner.register("demo", handler)
    await runner.start()
    try:
        job = runner.submit("demo", "demo", {})
        # Wait until the first delta has been produced, then attach.
        async with asyncio.timeout(5):
            while runner.snapshot(job.id)["output_text"] != "first ":
                await asyncio.sleep(0.02)

        queue = runner.subscribe(job.id)
        snapshot = runner.snapshot(job.id)
        release.set()
        await drain(runner, job.id)

        deltas = []
        while not queue.empty():
            message = queue.get_nowait()
            if message.get("type") == "token":
                deltas.append(message)
    finally:
        await runner.stop()

    # The snapshot already contains "first "; replaying it plus later deltas must not
    # duplicate that text.
    baseline = len(snapshot["output_text"])
    text = snapshot["output_text"] + "".join(
        item["content"] for item in deltas if item["offset"] >= baseline
    )
    assert text == "first second"


async def test_events_carry_a_sequence_so_a_late_subscriber_can_deduplicate(runner):
    """A mid-run subscriber sees some events twice: once in the snapshot, once live."""
    release = asyncio.Event()

    async def handler(_request, context: JobContext):
        await context.event("one", "completed", "First")
        await release.wait()
        await context.event("two", "completed", "Second")
        return {}

    runner.register("demo", handler)
    await runner.start()
    try:
        job = runner.submit("demo", "demo", {})
        async with asyncio.timeout(5):
            while len(runner.get(job.id)["events"]) < 1:
                await asyncio.sleep(0.02)

        queue = runner.subscribe(job.id)
        snapshot = runner.snapshot(job.id)
        release.set()
        await drain(runner, job.id)

        live = []
        while not queue.empty():
            message = queue.get_nowait()
            if message.get("type") == "agent_event":
                live.append(message)
    finally:
        await runner.stop()

    assert [event["seq"] for event in snapshot["events"]] == [1]
    highest = max(event["seq"] for event in snapshot["events"])
    merged = snapshot["events"] + [item for item in live if item["seq"] > highest]
    assert [item["label"] for item in merged] == ["First", "Second"], "no event is shown twice"


async def test_failure_is_recorded_rather_than_lost(runner):
    async def handler(_request, _context):
        raise RuntimeError("model unavailable")

    runner.register("demo", handler)
    await runner.start()
    try:
        job = runner.submit("demo", "demo", {})
        detail = await drain(runner, job.id)
    finally:
        await runner.stop()

    assert detail["status"] == "failed"
    assert "model unavailable" in detail["error"]


async def test_cancelling_a_running_job_stops_it_and_keeps_partial_output(runner):
    running = asyncio.Event()

    async def handler(_request, context: JobContext):
        await context.token("partial")
        running.set()
        await asyncio.sleep(30)
        return {}

    runner.register("demo", handler)
    await runner.start()
    try:
        job = runner.submit("demo", "demo", {})
        await asyncio.wait_for(running.wait(), timeout=5)
        assert runner.cancel(job.id) is True
        detail = await drain(runner, job.id)
    finally:
        await runner.stop()

    assert detail["status"] == "cancelled"
    assert detail["output_text"] == "partial", "work done before cancelling is still readable"
    assert runner.cancel(job.id) is False, "a finished job cannot be cancelled again"


async def test_cancelling_a_queued_job_never_runs_it(runner):
    ran = asyncio.Event()

    async def handler(_request, _context):
        ran.set()
        return {}

    runner.register("demo", handler)
    job = runner.submit("demo", "demo", {})
    assert runner.cancel(job.id) is True

    await runner.start()
    try:
        await asyncio.sleep(0.3)
    finally:
        await runner.stop()

    assert not ran.is_set()
    assert runner.get(job.id)["status"] == "cancelled"


async def test_restart_marks_orphaned_jobs_interrupted(runner):
    """A generation cannot be resumed, so an orphan must not claim to still be running."""
    with Session(runner.engine) as session:
        session.add(Job(kind="demo", status="running", title="was mid-flight"))
        session.add(Job(kind="demo", status="queued", title="never started"))
        session.commit()

    assert runner.reconcile() == 2

    with Session(runner.engine) as session:
        jobs = session.exec(select(Job)).all()
    assert {job.status for job in jobs} == {"interrupted"}
    assert all("restarted" in job.error for job in jobs)


async def test_active_count_and_listing_drive_the_close_prompt(runner):
    release = asyncio.Event()

    async def handler(_request, _context):
        await release.wait()
        return {}

    runner.register("demo", handler)
    await runner.start()
    try:
        job = runner.submit("demo", "one", {})
        # active_count covers queued *and* running: both mean "do not close the tab yet".
        assert runner.active_count() == 1
        async with asyncio.timeout(5):
            while runner.get(job.id)["status"] != "running":
                await asyncio.sleep(0.02)
        assert [item["status"] for item in runner.list()] == ["running"]
        assert runner.active_count() == 1
        release.set()
        async with asyncio.timeout(5):
            while runner.active_count() != 0:
                await asyncio.sleep(0.02)
    finally:
        await runner.stop()

    listed = runner.list()
    assert listed[0]["status"] == "succeeded"
    assert listed[0]["has_result"] is True


async def test_unknown_kind_fails_the_job_instead_of_the_worker(runner):
    async def handler(_request, _context):
        return {}

    runner.register("demo", handler)
    await runner.start()
    try:
        # A row persisted by a build that had a handler this process does not. Inserted
        # after start() because reconcile() clears anything queued from a previous process.
        orphan = Job(kind="retired-kind", status="queued", title="from an older build")
        with Session(runner.engine) as session:
            session.add(orphan)
            session.commit()
            session.refresh(orphan)
        detail = await drain(runner, orphan.id)
    finally:
        await runner.stop()

    assert detail["status"] == "failed"
    assert "retired-kind" in detail["error"]


def test_submitting_an_unregistered_kind_is_rejected_up_front(runner):
    with pytest.raises(ValueError, match="No handler"):
        runner.submit("nope", "demo", {})


def test_a_cancel_during_the_claim_window_is_not_dropped(runner):
    """Cancelling between claim and task registration must not be silently discarded.

    The worker marks a job running in a database thread and registers its task only once
    control returns to the event loop. A cancel arriving in that window finds no task: it
    used to fall through to the "queued" branch, mark the row cancelled, report success —
    and then let the job run to completion, telling the user it had stopped while it had not.

    Driven directly rather than by timing, because the window is a few microseconds wide.
    """
    async def handler(_request, _context):
        return {}

    runner.register("demo", handler)
    job = runner.submit("demo", "demo", {})

    # Reproduce the window exactly: claimed and running, no task registered yet.
    claimed = runner.claim_next()
    assert claimed is not None and claimed[0] == job.id
    assert runner._current is None

    assert runner.cancel(job.id) is True
    # The request is recorded for the worker rather than pretending the job already stopped.
    assert job.id in runner._cancel_requested
    assert runner.get(job.id)["status"] == "running", "not falsely reported as finished"


async def test_a_runner_restarted_after_stopping_still_executes_work(runner):
    """Stop then start must give a working runner, not a worker that exits immediately.

    Every in-process restart does this — a test client's lifespan, or an app restarted
    without a new process. When the stop flag survived into the next start, the new worker
    evaluated an already-false loop condition and returned, so nothing ever ran again and
    every submission sat queued forever with no error anywhere.
    """
    async def handler(request, _context):
        return {"echo": request["n"]}

    runner.register("demo", handler)

    await runner.start()
    first = runner.submit("demo", "first", {"n": 1})
    assert (await drain(runner, first.id))["status"] == "succeeded"
    await runner.stop()

    await runner.start()
    try:
        second = runner.submit("demo", "second", {"n": 2})
        detail = await drain(runner, second.id)
    finally:
        await runner.stop()

    assert detail["status"] == "succeeded"
    assert detail["result"] == {"echo": 2}


async def test_shutdown_is_bounded_when_the_worker_cannot_be_reaped(runner):
    """Stopping must not be able to hang the process.

    Cancelling a task that is inside `asyncio.to_thread` does not interrupt the thread, so
    an unbounded await here turns a slow or stuck database call into an application that
    will not exit. This drives the pathological case directly: a worker that never finishes.
    """
    await runner.start()
    never_finishes: asyncio.Future = asyncio.get_running_loop().create_future()
    runner._worker.cancel()
    runner._worker = asyncio.ensure_future(never_finishes)
    try:
        async with asyncio.timeout(STOP_TIMEOUT_SECONDS + 3):
            await runner.stop()
    finally:
        never_finishes.cancel()


async def test_a_finished_job_is_never_rewritten_by_a_later_cancel(runner):
    """Terminal is terminal — a result the user earned cannot be relabelled.

    The completion write and the cancellation write race for real. A handler returns, the
    row is written `succeeded` from a database thread, and only then does the cancellation
    from shutdown get delivered to the worker — which used to overwrite the row with
    `cancelled`, discarding the result. The user watched a run finish and found it marked
    cancelled with nothing in it on reopening the tab.
    """
    async def handler(_request, _context):
        return {"answer": 42}

    runner.register("demo", handler)
    await runner.start()
    try:
        job = runner.submit("demo", "demo", {})
        detail = await drain(runner, job.id)
        assert detail["status"] == "succeeded"

        # Whatever arrives afterwards, from any path, must not move it.
        assert runner._finish(job.id, "cancelled", error="too late") is False
        assert runner.cancel(job.id) is False
    finally:
        await runner.stop()

    final = runner.get(job.id)
    assert final["status"] == "succeeded"
    assert final["result"] == {"answer": 42}
