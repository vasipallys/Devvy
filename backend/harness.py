"""Small production harness primitives shared by every local agent workflow.

The harness intentionally records operational evidence, not hidden model reasoning or user
content. This makes runs inspectable without turning private prompts into a second data store.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class ContextSource:
    id: str
    label: str
    content: str
    priority: int = 50
    trusted: bool = False


def assemble_context(sources: list[ContextSource], max_chars: int) -> tuple[str, list[dict]]:
    """Build a priority-ordered, bounded prompt context with a public provenance manifest."""
    remaining = max(0, max_chars)
    blocks: list[str] = []
    manifest: list[dict] = []
    for source in sorted(sources, key=lambda item: item.priority, reverse=True):
        if not source.content.strip() or remaining <= 0:
            continue
        marker = "TRUSTED CONTEXT" if source.trusted else "UNTRUSTED EVIDENCE"
        header = f"\n<{marker} id={json.dumps(source.id)} label={json.dumps(source.label)}>\n"
        footer = f"\n</{marker}>"
        available = max(0, remaining - len(header) - len(footer))
        included = source.content[:available]
        if not included:
            continue
        block = header + included + footer
        blocks.append(block)
        remaining -= len(block)
        manifest.append(
            {
                "id": source.id,
                "label": source.label,
                "characters": len(included),
                "truncated": len(included) < len(source.content),
                "trusted": source.trusted,
            }
        )
    return "\n".join(blocks), manifest


def sweep_directory(directory: Path, retention_days: int, patterns: tuple[str, ...]) -> int:
    """Delete files older than the retention window. Best-effort, never raises.

    Retention has to cover artefacts as well as records. Jobs, their events, and the ledger
    are all pruned, but the uploads and generated media they refer to were not — so the only
    thing that grew forever was the part holding whole documents and rendered video.
    """
    if not directory.is_dir():
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))).timestamp()
    removed = 0
    for pattern in patterns:
        for path in directory.glob(pattern):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


class RunLedger:
    """Append privacy-preserving workflow summaries to a local JSONL evidence ledger."""

    def __init__(self, data_dir: Path, retention_days: int = 30):
        self.directory = data_dir / "agent-runs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, retention_days))
        for path in self.directory.glob("????-??-??.jsonl"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                    path.unlink()
            except OSError:
                # Retention is best-effort and must never prevent application startup.
                continue

    def start(self, workflow: str, *, metadata: dict[str, Any] | None = None) -> "AgentRun":
        return AgentRun(self, workflow, metadata or {})

    def _append(self, payload: dict[str, Any]) -> None:
        path = self.directory / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl"
        line = json.dumps(payload, default=str, separators=(",", ":")) + "\n"
        with self._lock, path.open("a", encoding="utf-8", newline="") as handle:
            handle.write(line)


class AgentRun:
    """One workflow's trajectory, appended to the ledger when it ends."""

    def __init__(self, ledger: RunLedger, workflow: str, metadata: dict[str, Any]):
        self.ledger = ledger
        self.id = str(uuid4())
        self.workflow = workflow
        self.metadata = metadata
        self.started_at = datetime.now(timezone.utc)
        self.started_clock = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self._finished = False

    def event(
        self,
        stage: str,
        status: str,
        label: str,
        *,
        detail: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "run_id": self.id,
            "stage": stage,
            "status": status,
            "label": label,
            "elapsed_ms": round((time.monotonic() - self.started_clock) * 1000),
        }
        if detail:
            item["detail"] = detail
        if evidence:
            item["evidence"] = evidence
        self.events.append(item)
        return item

    def finish(self, status: str, *, summary: dict[str, Any] | None = None) -> None:
        """Write the trajectory. Safe to call from async code — see ``finish_async``."""
        if self._finished:
            return
        self._finished = True
        self.ledger._append(
            {
                "run_id": self.id,
                "workflow": self.workflow,
                "status": status,
                "started_at": self.started_at.isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "duration_ms": round((time.monotonic() - self.started_clock) * 1000),
                "metadata": self.metadata,
                "trajectory": self.events,
                "summary": summary or {},
                "privacy": "No prompt, source content, or model response is stored in this ledger.",
            }
        )

    async def finish_async(self, status: str, *, summary: dict[str, Any] | None = None) -> None:
        """Finish from async code without blocking the event loop on disk.

        Every workflow ends by writing its trajectory. Doing that inline stalls the single
        thread that is also streaming tokens to every attached viewer, for the length of a
        file append plus an fsync — small individually, and paid on the completion of every
        request in the application.
        """
        if self._finished:
            return
        await asyncio.to_thread(self.finish, status, summary=summary)
