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


# ------------------------------------------------------------------------------------------
# Grounding contract
# ------------------------------------------------------------------------------------------

#: The rule every model-backed workflow in this application carries, verbatim.
#:
#: A local 1B model's failure mode is not refusing to answer — it is answering anyway. Asked
#: about a field the story never mentions, it will describe a plausible one, and the result is
#: indistinguishable from evidence: it lands in a scorecard, gets an evidence id, and is read by
#: someone who was not in the room. Fabrication that arrives inside an evidence-based product is
#: worse than no answer at all, because the whole surface is built to be trusted.
#:
#: So the contract is the same everywhere and says the same thing four ways, because a model
#: this size does not reliably generalise from one phrasing: use only what is written, do not
#: infer, say plainly when the text does not contain the answer, and — because a bare refusal is
#: not useful to the person who wrote the story — say what would need to be added.
#:
#: Kept here rather than copied into four prompts so the wording cannot drift between agents,
#: and so a change to the rule is a change to one string.
#: The exact sentence a workflow must return when the answer is not in the text. Kept as its
#: own constant, on one line, for two reasons: an instruction to "say exactly X" where X is
#: split across a line break is not an exact instruction, and a fixed phrase is something a
#: reader can recognise and a test can assert.
NO_INFORMATION = "The provided text does not contain this information."

GROUNDING_CONTRACT = f"""<grounding_contract>
Use only the facts directly stated in the context above. Do not use outside facts, prior
knowledge about similar systems, or assumptions about how this is "usually" done.

Do not guess, extrapolate, or add information that is not explicitly written. Do not invent
file names, endpoints, tables, screens, libraries, versions, or requirements. If two readings
of the text are possible, do not pick one.

If the information needed is missing, say exactly: "{NO_INFORMATION}"
Then say what could be added to the story to answer it — name the specific missing fact, not a
general request for more detail.

Act as a strict extractor. Process only the given words and numbers. Absence of a detail is a
finding to report, never a gap to fill.
</grounding_contract>"""

#: The same contract compressed to a single paragraph, for prompts that are already at their
#: character budget and would otherwise lose story evidence to make room for policy.
GROUNDING_CONTRACT_BRIEF = (
    "Use only the facts directly stated in the context above. Do not use outside facts or "
    "assumptions about how this is usually done. Do not guess, extrapolate, or add anything "
    "not explicitly written — no invented files, endpoints, tables, screens, or requirements. "
    f'If the information is missing, say exactly: "{NO_INFORMATION}" and name the specific fact '
    "that would have to be added to the story. Act as a strict extractor: process only the "
    "given words and numbers."
)

#: The same anti-fabrication rules, worded for a workflow that *writes* code rather than reads
#: it.
#:
#: The extraction wording cannot simply be pasted here. "Act as a strict extractor, process only
#: the given words and numbers" instructs a model not to produce anything that was not already
#: in its input — which is the correct stance for scoring a story and the wrong one for a
#: workflow whose entire job is to emit a file that did not exist a moment ago. Told that, a
#: code generator returns the objective back.
#:
#: What carries across is every rule about *invention*: use what was stated, do not add
#: requirements nobody asked for, say plainly when the information needed is missing rather
#: than filling the gap with a plausible default, and never name a path that is not real. Those
#: are the rules that stop fabrication; the extractor stance is not one of them.
GROUNDING_CONTRACT_BUILD = (
    "Use only the facts directly stated in the objective, the acceptance criteria and the "
    "retrieved evidence above. Do not use outside facts or assumptions about how this is "
    "usually done. Do not guess, extrapolate, or add requirements, endpoints, tables, screens, "
    "libraries, configuration or behaviour that were not asked for — a feature nobody requested "
    "is a defect, however well written. Every path you name must be a file listed in the "
    "repository map, or a new file inside a directory that appears there; do not invent a "
    f'location. If you need information the context does not contain, say exactly: '
    f'"{NO_INFORMATION}" in your summary and name the specific fact that would have to be '
    "specified, rather than choosing a plausible default and building on it."
)


#: The engineering discipline that governs a change to an existing repository.
#:
#: Distinct from the grounding contract, which is about not inventing facts. This is about not
#: making unnecessary changes — a different failure, and on a real codebase a more expensive
#: one. A model asked to satisfy a requirement will happily reformat, rename and "improve"
#: everything it reads on the way, and the resulting diff is unreviewable long before it is
#: wrong.
#:
#: The rule that does the work is the last one. Relatedness is not necessity: a file being
#: about the same subject as the requirement is not a reason to modify it.
ENGINEERING_CONTRACT = (
    "Work to these engineering rules.\n"
    "- Analyse before coding. Understand what already exists before proposing anything new.\n"
    "- Reuse before creating. Search the supplied code for existing equivalent functionality, "
    "and follow the patterns, naming and layering already in the repository.\n"
    "- Make the minimum necessary change. Do not redesign unrelated code, do not introduce "
    "abstractions nothing asks for, and do not reformat what you are not otherwise changing.\n"
    "- Preserve backward compatibility unless the requirement explicitly asks otherwise. "
    "Existing callers and existing data must keep working.\n"
    "- Every modification must state which requirement makes it necessary. A file being "
    "related to the subject is not a reason to modify it — prove before you modify.\n"
    "- Never claim a build or a test passed. Nothing here is executed; say \"NOT EXECUTED\" "
    "for anything you did not observe, and \"NOT VERIFIED\" for anything you could not confirm."
)
