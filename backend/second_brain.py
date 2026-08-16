"""The second brain: a local notes vault YUKTI can search, and a memory bank it can keep.

Two stores, deliberately separate, because they answer different questions and fail
differently.

The **vault** is the user's own files on their own disk — notes, project documents, saved
research. YUKTI reads it and never writes to it. Someone's notes directory is not a scratchpad
for an assistant, and a butler who edits your papers is not a butler.

The **memory bank** is YUKTI's own: preferences, decisions, recurring workflows. It is written
only when the user says something worth keeping, it is stored as text the user can read back,
and it is theirs to delete. Nothing is inferred into it — a memory the user did not state is
an assumption wearing a fact's clothes, and it will be recalled months later with all the
authority of something they actually said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import Column, JSON
from sqlmodel import Field as SQLField, Session, SQLModel, select

from backend.auth import User  # noqa: F401 — registers the foreign-key target in metadata

# ------------------------------------------------------------------------------------------
# The notes vault
# ------------------------------------------------------------------------------------------

#: Text formats worth reading aloud from. Deliberately narrow — the vault is for notes, and a
#: binary opened as text is noise that costs context and tells the user nothing.
NOTE_SUFFIXES = frozenset({".md", ".txt", ".markdown", ".rst", ".org", ".json", ".csv"})

#: Directories that are never anyone's notes.
SKIP_DIRECTORIES = frozenset({
    ".git", ".svn", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".next", ".cache", ".idea", ".vscode", "site-packages", ".pytest_cache", ".mypy_cache",
})

MAX_NOTE_BYTES = 512_000
MAX_VAULT_FILES = 4_000
#: How much note text one turn may put in front of the model. The spoken answer is ~150 words;
#: handing a 1B model 20k characters to find them in reliably produces a summary of the wrong
#: note.
VAULT_CONTEXT_BUDGET = 6_000
SNIPPET_CHARS = 700


@dataclass
class NoteHit:
    path: str
    title: str
    score: float
    snippet: str
    modified: str
    #: The query words this note actually contained, so a weak match reads as weak.
    matched: list[str] = field(default_factory=list)


@dataclass
class VaultResult:
    root: str
    reachable: bool
    total_notes: int
    hits: list[NoteHit] = field(default_factory=list)
    note: str = ""

    def summary(self) -> str:
        if not self.reachable:
            return self.note or "The notes vault could not be read."
        if not self.hits:
            return f"Searched {self.total_notes} note(s); nothing matched."
        best = self.hits[0]
        return (
            f"Searched {self.total_notes} note(s); {len(self.hits)} matched, "
            f"closest is {best.title}."
        )


_WORD = re.compile(r"[a-z0-9]{3,}")
#: Words too common to discriminate between notes. A query full of these matches everything,
#: which is the same as matching nothing but looks like a result.
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "what", "when", "where", "which", "that", "this", "from",
    "have", "has", "had", "was", "were", "are", "you", "your", "our", "his", "her", "their",
    "about", "into", "over", "under", "then", "than", "them", "they", "can", "could", "would",
    "should", "will", "did", "does", "done", "any", "all", "some", "get", "got", "let", "may",
    "yukti", "please", "tell", "show", "find", "give", "say", "said", "ask", "asked", "sir",
    "maam", "ma'am", "note", "notes",
})


def query_terms(query: str) -> list[str]:
    return [word for word in _WORD.findall(query.lower()) if word not in _STOPWORDS]


def _is_note(path: Path) -> bool:
    return path.suffix.lower() in NOTE_SUFFIXES


def _walk(root: Path) -> list[Path]:
    found: list[Path] = []
    for path in root.rglob("*"):
        if len(found) >= MAX_VAULT_FILES:
            break
        try:
            if any(part in SKIP_DIRECTORIES or part.startswith(".") for part in path.parts[
                len(root.parts):
            ][:-1]):
                continue
            if path.is_file() and _is_note(path) and path.stat().st_size <= MAX_NOTE_BYTES:
                found.append(path)
        except OSError:
            continue
    return found


def _snippet(text: str, terms: list[str]) -> str:
    """The part of the note that matched, not the part that happens to be first.

    A note's opening paragraph is almost never why it matched, and showing it makes every
    result look equally relevant. Centring on the first hit is what lets a reader — or a
    listener — tell a real match from a coincidence.
    """
    lowered = text.lower()
    positions = [lowered.find(term) for term in terms]
    hits = [p for p in positions if p >= 0]
    if not hits:
        return " ".join(text[:SNIPPET_CHARS].split())
    start = max(0, min(hits) - SNIPPET_CHARS // 3)
    return " ".join(text[start:start + SNIPPET_CHARS].split())


def search_notes(vault_root: str, query: str, limit: int = 5) -> VaultResult:
    """Rank the user's notes against the question. Read-only, deterministic, no model.

    Scoring is term frequency with the filename weighted heavily: a note *called*
    "funnel-pricing.md" is almost always a better answer to a question about funnel pricing
    than one that mentions the words in passing, and that is a judgement worth encoding rather
    than leaving to a 1B model's reading of five documents.
    """
    root_text = (vault_root or "").strip()
    if not root_text:
        return VaultResult("", False, 0, note="No notes vault is configured.")
    root = Path(root_text).expanduser()
    if not root.is_dir():
        return VaultResult(str(root), False, 0, note=f"No directory at {root}.")

    terms = query_terms(query)
    files = _walk(root)
    if not terms:
        return VaultResult(str(root), True, len(files), note="The question had no searchable terms.")

    hits: list[NoteHit] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered = text.lower()
        name = path.stem.lower().replace("-", " ").replace("_", " ")
        matched = [term for term in terms if term in lowered or term in name]
        if not matched:
            continue
        score = sum(
            lowered.count(term) * 1.0 + (6.0 if term in name else 0.0) for term in matched
        )
        # Coverage matters more than repetition: a note holding every term once beats one
        # repeating a single term forty times.
        score *= len(matched) / len(terms)
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
        except OSError:
            modified = ""
        hits.append(NoteHit(
            path=str(path.relative_to(root)).replace("\\", "/"),
            title=path.stem.replace("-", " ").replace("_", " "),
            score=round(score, 2),
            snippet=_snippet(text, matched),
            modified=modified,
            matched=matched,
        ))
    hits.sort(key=lambda item: (-item.score, item.path))
    return VaultResult(str(root), True, len(files), hits=hits[:limit])


def read_note(vault_root: str, relative_path: str) -> str:
    """Read one note by its vault-relative path.

    Traversal is refused rather than sanitised. Silently rewriting `../../.ssh/id_rsa` into
    something inside the vault would answer a question the user did not ask, from a file they
    did not mean; refusing says what happened.
    """
    root = Path((vault_root or "").strip()).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("No notes vault is configured.")
    target = (root / relative_path).resolve()
    if not target.is_relative_to(root):
        raise ValueError("That path is outside the notes vault.")
    if not target.is_file():
        raise ValueError(f"No note at {relative_path}.")
    if not _is_note(target):
        raise ValueError(f"{target.suffix} is not a readable note format.")
    if target.stat().st_size > MAX_NOTE_BYTES:
        raise ValueError("That note is too large to read in a spoken turn.")
    return target.read_text(encoding="utf-8", errors="replace")


def vault_context(result: VaultResult, budget: int = VAULT_CONTEXT_BUDGET) -> str:
    """The matched notes as labelled evidence, bounded."""
    if not result.hits:
        return ""
    blocks: list[str] = []
    remaining = budget
    for hit in result.hits:
        block = f"[note: {hit.path}]\n{hit.snippet}"
        if len(block) > remaining:
            break
        blocks.append(block)
        remaining -= len(block)
    return "\n\n".join(blocks)


# ------------------------------------------------------------------------------------------
# The memory bank
# ------------------------------------------------------------------------------------------

class Memory(SQLModel, table=True):
    """One thing the user asked YUKTI to remember.

    ``source_text`` keeps the sentence the memory came from. A recalled memory months later
    is only as trustworthy as its provenance, and "you said this, on this date" is the
    difference between a memory and an opinion the assistant formed about you.
    """

    id: UUID = SQLField(default_factory=uuid4, primary_key=True)
    owner_id: UUID | None = SQLField(default=None, foreign_key="user.id", index=True)
    kind: str = SQLField(default="fact", index=True)
    subject: str = ""
    content: str = ""
    source_text: str = ""
    created_at: datetime = SQLField(default_factory=lambda: datetime.now(UTC))
    #: Bumped whenever a recall surfaces it, so the bank can be pruned by usefulness rather
    #: than only by age.
    recalled: int = 0
    tags: list[str] = SQLField(default_factory=list, sa_column=Column(JSON))


#: What the user says when they want something kept. Explicit only — YUKTI never decides on
#: its own that a passing remark was worth filing.
_REMEMBER = (
    "remember that", "remember this", "remember:", "note that", "make a note",
    "keep in mind that", "don't forget that", "do not forget that", "for future reference",
    "from now on", "i prefer", "my preference is", "always ", "never ",
)
_ADDRESS = re.compile(
    r"\b(?:call me|address me as|refer to me as|my name is)\s+([A-Za-z][\w'’-]{0,40})", re.I
)


def detect_memory(text: str) -> tuple[str, str] | None:
    """(kind, content) if this turn asked for something to be remembered, else None."""
    stripped = text.strip()
    if not stripped:
        return None
    address = _ADDRESS.search(stripped)
    if address:
        return "address", address.group(1).strip(" .,!?")
    lowered = stripped.lower()
    for trigger in _REMEMBER:
        index = lowered.find(trigger)
        if index >= 0:
            content = stripped[index + len(trigger):].strip(" :,.-")
            # "always" and "never" appear inside ordinary sentences constantly; only treat
            # them as an instruction when what follows is substantial enough to be one.
            if len(content) >= 8:
                return ("preference" if trigger in {"i prefer", "my preference is", "always ",
                                                    "never "} else "fact"), content
    return None


def remember(
    session: Session,
    content: str,
    *,
    kind: str = "fact",
    subject: str = "",
    source_text: str = "",
    owner_id: UUID | None = None,
) -> Memory:
    """Store a memory, replacing any earlier one of the same kind and subject.

    An assistant that accumulates three contradictory answers to "what should I call you"
    will eventually recall the wrong one. Same kind and same subject means the newer statement
    supersedes rather than joins.
    """
    existing = session.exec(
        select(Memory).where(
            Memory.owner_id == owner_id,
            Memory.kind == kind,
            Memory.subject == subject,
        )
    ).first()
    if existing is not None:
        existing.content = content
        existing.source_text = source_text
        existing.created_at = datetime.now(UTC)
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    item = Memory(
        kind=kind, subject=subject, content=content,
        source_text=source_text[:2_000], owner_id=owner_id,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def recall(
    session: Session, query: str, *, owner_id: UUID | None = None, limit: int = 6
) -> list[Memory]:
    """Memories bearing on this question, plus the standing preferences that always apply.

    Preferences are returned whether or not they match the words. "Call me Vikram" is relevant
    to every single turn, and a term-overlap search would surface it only when the user
    happened to say their own name.
    """
    rows = session.exec(select(Memory).where(Memory.owner_id == owner_id)).all()
    terms = set(query_terms(query))
    standing = [item for item in rows if item.kind in {"address", "preference"}]
    scored: list[tuple[float, Memory]] = []
    for item in rows:
        if item in standing:
            continue
        haystack = f"{item.subject} {item.content}".lower()
        overlap = sum(1 for term in terms if term in haystack)
        if overlap:
            scored.append((overlap / max(1, len(terms)), item))
    scored.sort(key=lambda pair: (-pair[0], pair[1].created_at.isoformat()))
    chosen = standing + [item for _, item in scored[: max(0, limit - len(standing))]]
    for item in chosen:
        item.recalled += 1
        session.add(item)
    if chosen:
        session.commit()
    return chosen


def preferences(session: Session, *, owner_id: UUID | None = None) -> dict[str, str]:
    """Standing preferences as a flat map, for the persona's honorific and manner."""
    rows = session.exec(
        select(Memory).where(Memory.owner_id == owner_id, Memory.kind == "address")
    ).all()
    return {"address": rows[-1].content} if rows else {}


def memory_context(items: list[Memory]) -> str:
    if not items:
        return ""
    return "\n".join(
        f"- ({item.kind}) {item.content}"
        + (f" — you said: \"{item.source_text[:160]}\"" if item.source_text else "")
        for item in items
    )
