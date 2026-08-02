"""Production-safe Smart Code workflow backed by Devvy's shared Gemma runtime."""

from __future__ import annotations

import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.config import Settings
from backend.model import GemmaRuntime
from backend.structured_output import generate_structured

SOURCE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".rb",
    ".php", ".cs", ".cpp", ".c", ".h", ".hpp", ".json", ".toml", ".yaml",
    ".yml", ".md", ".html", ".css", ".sql", ".xml",
}
SKIP_DIRS = {
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "dist", "build",
    "target", "coverage", "__pycache__", ".smartcode", ".idea", ".vscode",
}


class SmartCodeRequest(BaseModel):
    objective: str = Field(min_length=3, max_length=20_000)
    workspace_root: str = Field(min_length=1, max_length=2_000)
    mode: Literal["generate", "modify", "review"] = "modify"
    target_paths: list[str] = Field(default_factory=list, max_length=20)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=20)
    language: str | None = Field(default=None, max_length=50)
    framework: str | None = Field(default=None, max_length=80)
    risk: Literal["low", "medium", "high"] = "medium"

    @field_validator("target_paths", "acceptance_criteria")
    @classmethod
    def clean_list(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value.strip()]


class ProposedEdit(BaseModel):
    action: Literal["create", "replace"]
    path: str
    content: str
    reason: str = "Generated to satisfy the requested objective."

    @model_validator(mode="before")
    @classmethod
    def normalize_small_model_output(cls, value: object) -> object:
        """Accept predictable schema synonyms emitted by compact local models."""
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "action" not in normalized:
            normalized["action"] = normalized.get("operation") or normalized.get("type")
        if "path" not in normalized:
            normalized["path"] = normalized.get("file") or normalized.get("filename")
        if "content" not in normalized:
            normalized["content"] = normalized.get("code") or normalized.get("new_content") or ""
        if "reason" not in normalized:
            normalized["reason"] = (
                normalized.get("summary")
                or normalized.get("rationale")
                or "Generated to satisfy the requested objective."
            )
        return normalized


class ReviewFinding(BaseModel):
    severity: Literal["blocker", "major", "minor", "nit"]
    message: str
    path: str | None = None
    suggestion: str | None = None


class SmartCodeModelOutput(BaseModel):
    summary: str
    plan: list[str] = Field(min_length=1, max_length=12)
    edits: list[ProposedEdit] = Field(default_factory=list, max_length=20)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def normalize_output_envelope(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "edits" not in normalized:
            normalized["edits"] = normalized.get("changes") or normalized.get("files") or []
        if "plan" not in normalized:
            normalized["plan"] = normalized.get("steps") or ["Implement the requested change"]
        if "summary" not in normalized:
            normalized["summary"] = normalized.get("notes") or "Prepared the requested change."
        return normalized


class SmartCodeApplyRequest(BaseModel):
    preview_token: str
    approved: bool


@dataclass
class StoredPreview:
    created_at: datetime
    root: Path
    output: SmartCodeModelOutput
    files: dict[Path, str]
    hashes: dict[Path, str | None]
    verification: list[dict]


def _hash(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(root: Path, candidate: Path) -> bool:
    resolved = candidate.resolve()
    return resolved == root or root in resolved.parents


def _safe_path(root: Path, value: str) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if not _inside(root, candidate):
        raise ValueError(f"Path is outside the selected workspace: {value}")
    if candidate.suffix.lower() not in SOURCE_EXTENSIONS:
        raise ValueError(f"Unsupported code file type: {value}")
    return candidate


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _words(value: str) -> set[str]:
    return {item.lower() for item in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", value)}


def _scan(root: Path, objective: str, limit: int = 40) -> list[Path]:
    goal = _words(objective)
    ranked: list[tuple[int, int, Path]] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > 512_000:
            continue
        rel = _relative(root, path)
        score = len(goal & _words(rel)) * 3
        ranked.append((score, -size, path.resolve()))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    relevant = [path for score, _, path in ranked if score > 0]
    return (relevant or [path for _, _, path in ranked])[:limit]


def _context(root: Path, paths: list[Path], max_chars: int) -> str:
    blocks: list[str] = []
    remaining = max_chars
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        header = f"\n--- FILE {_relative(root, path)} ---\n"
        chunk = header + content[: max(0, remaining - len(header))]
        blocks.append(chunk)
        remaining -= len(chunk)
        if remaining <= 0:
            break
    return "".join(blocks)


def _verify(path: Path, content: str) -> dict:
    if not content.strip():
        return {"path": str(path), "passed": False, "detail": "File is empty"}
    suffix = path.suffix.lower()
    try:
        if suffix == ".py":
            ast.parse(content, filename=str(path))
        elif suffix == ".json":
            json.loads(content)
        else:
            pairs = {"(": ")", "[": "]", "{": "}"}
            stack: list[str] = []
            for char in content:
                if char in pairs:
                    stack.append(char)
                elif char in pairs.values():
                    if not stack or pairs[stack.pop()] != char:
                        raise ValueError("Unbalanced brackets")
            if stack:
                raise ValueError("Unbalanced brackets")
        return {"path": str(path), "passed": True, "detail": "Structural checks passed"}
    except (SyntaxError, ValueError, json.JSONDecodeError) as exc:
        return {"path": str(path), "passed": False, "detail": str(exc)}


class SmartCodeService:
    def __init__(self, runtime: GemmaRuntime, settings: Settings):
        self.runtime = runtime
        self.settings = settings
        self._previews: dict[str, StoredPreview] = {}
        self._lock = threading.Lock()

    def _purge(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
        with self._lock:
            stale = [key for key, value in self._previews.items() if value.created_at < cutoff]
            for key in stale:
                self._previews.pop(key, None)

    async def preview(self, request: SmartCodeRequest) -> dict:
        self._purge()
        root = Path(request.workspace_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Select an existing workspace folder.")
        targets = [_safe_path(root, value) for value in request.target_paths]
        for target in targets:
            if request.mode in {"modify", "review"} and not target.is_file():
                raise ValueError(f"Target file does not exist: {_relative(root, target)}")
        candidates = targets or _scan(root, request.objective)
        if request.mode == "review" and not candidates:
            raise ValueError("No source files were found in the selected workspace.")

        repo_map = "\n".join(f"- {_relative(root, path)}" for path in candidates)
        evidence = _context(root, candidates, self.settings.smart_code_max_context_chars)
        acceptance = request.acceptance_criteria or [
            "The requested behavior is complete and production ready",
            "Existing behavior remains compatible",
            "The result is secure, maintainable, and testable",
        ]
        mode_rule = (
            "Review only. Return findings and no edits."
            if request.mode == "review"
            else "Return the smallest complete set of whole-file create/replace edits."
        )
        prompt = f"""You are the Smart Code planning, coding, and review pipeline.
Objective: {request.objective}
Mode: {request.mode}
Language: {request.language or 'infer from repository'}
Framework: {request.framework or 'infer from repository'}
Risk tier: {request.risk}
Acceptance criteria: {json.dumps(acceptance)}

{mode_rule}
Use only paths under the workspace. Existing files available to modify are listed below.
If no existing files are listed, create the first required source file even in Modify mode.
For replace, content must be the COMPLETE new file. For create, include all runnable code.
Do not use placeholders, ellipses, or markdown fences. Never modify generated/vendor files.

REPOSITORY MAP:
{repo_map}

RETRIEVED EVIDENCE:
{evidence}
"""
        output = await generate_structured(
            self.runtime,
            SmartCodeModelOutput,
            "You are a cautious senior software engineer. Never claim unperformed verification.",
            prompt,
            max_new_tokens=self.settings.smart_code_max_output_tokens,
        )
        if request.mode == "review" and output.edits:
            raise ValueError("Review mode attempted to produce file edits.")
        if request.mode != "review" and not output.edits:
            raise ValueError("The model returned no code edits for this change request.")

        materialized: dict[Path, str] = {}
        hashes: dict[Path, str | None] = {}
        normalized_edits: list[ProposedEdit] = []
        explicit = set(targets)
        for edit in output.edits:
            path = _safe_path(root, edit.path)
            if targets and path not in explicit:
                raise ValueError(f"The model attempted an unapproved target: {_relative(root, path)}")
            if edit.action == "create" and path.exists():
                raise ValueError(f"Create target already exists: {_relative(root, path)}")
            if edit.action == "replace" and not path.is_file():
                raise ValueError(f"Replace target does not exist: {_relative(root, path)}")
            materialized[path] = edit.content
            hashes[path] = _hash(path)
            normalized_edits.append(edit.model_copy(update={"path": _relative(root, path)}))
        output = output.model_copy(update={"edits": normalized_edits})
        verification = [_verify(path, content) for path, content in materialized.items()]
        diffs: dict[str, str] = {}
        for path, content in materialized.items():
            old = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
            diffs[_relative(root, path)] = "".join(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                    fromfile=f"a/{_relative(root, path)}",
                    tofile=f"b/{_relative(root, path)}",
                    n=3,
                )
            )
        token = str(uuid4())
        with self._lock:
            self._previews[token] = StoredPreview(
                created_at=datetime.now(timezone.utc), root=root, output=output,
                files=materialized, hashes=hashes, verification=verification,
            )
        return {
            "preview_token": token,
            "summary": output.summary,
            "plan": output.plan,
            "edits": [item.model_dump() for item in output.edits],
            "findings": [item.model_dump() for item in output.findings],
            "diffs": diffs,
            "verification": verification,
            "can_apply": bool(materialized) and all(item["passed"] for item in verification),
        }

    def apply(self, request: SmartCodeApplyRequest) -> dict:
        if not request.approved:
            raise ValueError("Explicit approval is required before files can be written.")
        with self._lock:
            preview = self._previews.pop(request.preview_token, None)
        if preview is None:
            raise ValueError("This preview is missing, expired, or was already applied.")
        if any(_hash(path) != expected for path, expected in preview.hashes.items()):
            raise ValueError("A target changed after preview. Generate a fresh diff before applying.")
        if not all(item["passed"] for item in preview.verification):
            raise ValueError("Structural verification failed; the proposal cannot be applied.")

        run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + f"-{uuid4().hex[:8]}"
        backup_root = self.settings.app_data_dir / "smart-code" / "backups" / run_id
        applied: list[dict] = []
        for path, content in preview.files.items():
            if not _inside(preview.root, path):
                raise ValueError("Target escaped the approved workspace.")
            existed = path.exists()
            if existed:
                backup = backup_root / path.relative_to(preview.root)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            applied.append({
                "path": _relative(preview.root, path),
                "action": "replace" if existed else "create",
                "bytes_written": len(content.encode("utf-8")),
            })
        evidence_dir = self.settings.app_data_dir / "smart-code" / "runs"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        evidence = {
            "run_id": run_id,
            "workspace_root": str(preview.root),
            "summary": preview.output.summary,
            "plan": preview.output.plan,
            "applied": applied,
            "verification": preview.verification,
            "backup_dir": str(backup_root) if backup_root.exists() else None,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        (evidence_dir / f"{run_id}.json").write_text(
            json.dumps(evidence, indent=2), encoding="utf-8"
        )
        return evidence
