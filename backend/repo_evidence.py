"""Repository intelligence for estimation — EAGLE §3 and §4.

Until now the estimator read a story and nothing else. That is the right design when a story is
all there is, but it makes the pipeline answer the wrong question whenever a repository exists:
"does this story mention a migration?" instead of "does this codebase have migrations, and does
this change touch them?"

The difference matters most exactly where the estimator is weakest. §6 of the architecture lays
out a ladder for missing information —

    Can the repository answer?  →  Can history answer?  →  Can architecture docs answer?
    →  Can team calibration answer?  →  NO  →  increase uncertainty / clarify / spike

— and without a repository the first and most decisive rung was missing. A story that fails to
mention testing scores 4 for "the story does not say"; a story in a repository with 340 test
files that mirror the module being changed has an answer, and the estimate should use it rather
than charging the team for the story's brevity.

Two rules govern everything here, and both exist because the alternative fabricates:

**Only real paths.** Every file this module reports is one it found on disk. The model is never
asked to name a file; it is shown a ranked candidate list and may only describe *what changes*
in files that already exist, or propose a new file in a directory that already exists. A plan
naming `src/services/RiskClassifier.java` in a repository that has no `src/services` is not a
plan, it is a sentence that looks like one.

**The repository answers, or it says it cannot.** A signal is reported when it is found. Absence
of a migrations directory is reported as absence, and the scorer treats that as evidence that
there is no migration — which is a different and much stronger claim than the story not
mentioning one.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.smart_code import SKIP_DIRS, SOURCE_EXTENSIONS, _relative, _walk, _words

#: How many ranked candidate files reach the model. Beyond this the tail is noise: the ranking
#: is term overlap, and a file matching one incidental word is not evidence about the change.
MAX_CANDIDATES = 24

#: Cap on how much repository text is quoted into the estimation prompt. The estimate prompt is
#: already near its budget, and repository evidence must not crowd out the story it is about.
REPO_CONTEXT_BUDGET = 4000


# ---------------------------------------------------------------------------------------
# What the repository is
# ---------------------------------------------------------------------------------------

#: Manifest → the stack it proves. Presence of the file is the evidence; the contents are read
#: only to name frameworks, never to guess at them.
_MANIFESTS: tuple[tuple[str, str], ...] = (
    ("package.json", "javascript"),
    ("tsconfig.json", "typescript"),
    ("pyproject.toml", "python"),
    ("requirements.txt", "python"),
    ("setup.py", "python"),
    ("pom.xml", "java"),
    ("build.gradle", "java"),
    ("build.gradle.kts", "kotlin"),
    ("go.mod", "go"),
    ("Cargo.toml", "rust"),
    ("Gemfile", "ruby"),
    ("composer.json", "php"),
    ("*.csproj", "csharp"),
)

#: Framework fingerprints, matched against manifest text. Each is a substring that only appears
#: when the dependency is actually declared.
_FRAMEWORKS: tuple[tuple[str, str], ...] = (
    ("react", "React"), ("next", "Next.js"), ("@angular/core", "Angular"), ("vue", "Vue"),
    ("svelte", "Svelte"), ("fastapi", "FastAPI"), ("flask", "Flask"), ("django", "Django"),
    ("spring-boot", "Spring Boot"), ("express", "Express"), ("nestjs", "NestJS"),
    ("alembic", "Alembic"), ("sqlalchemy", "SQLAlchemy"), ("sqlmodel", "SQLModel"),
    ("prisma", "Prisma"), ("hibernate", "Hibernate"), ("pytest", "pytest"),
    ("jest", "Jest"), ("vitest", "Vitest"), ("junit", "JUnit"), ("playwright", "Playwright"),
    ("cypress", "Cypress"),
)

#: Directory-name fingerprints for structural signals. A signal is a fact about the repository,
#: not a guess: `migrations/` exists or it does not.
_SIGNAL_DIRS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("migrations", ("migrations", "migration", "alembic", "flyway", "liquibase")),
    ("tests", ("test", "tests", "spec", "__tests__", "e2e")),
    ("ci", (".github", ".gitlab", ".circleci", "jenkins")),
    ("containers", ("docker", "k8s", "kubernetes", "helm", "charts")),
    ("docs", ("docs", "doc", "adr", "architecture")),
    ("infra", ("terraform", "infra", "infrastructure", "ansible")),
)

#: Filename fingerprints, matched against the whole relative path.
_SIGNAL_FILES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("auth", ("auth", "login", "session", "oauth", "jwt", "permission", "rbac")),
    ("observability", ("logging", "logger", "metrics", "tracing", "telemetry", "sentry")),
    ("audit", ("audit", "consent", "retention", "gdpr", "compliance")),
    ("api_surface", ("routes", "router", "controller", "endpoint", "api", "resolver")),
    ("data_model", ("model", "entity", "schema", "repository", "dao")),
    ("feature_flags", ("feature_flag", "featureflag", "flags", "toggle", "unleash")),
    ("queues", ("kafka", "rabbit", "sqs", "pubsub", "celery", "queue")),
)


class RepoSignal(BaseModel):
    """One structural fact about the repository, present or absent, with what proved it."""

    name: str
    present: bool
    count: int = 0
    examples: list[str] = Field(default_factory=list)


class CandidateFile(BaseModel):
    """A file the change is likely to touch, and why it was ranked there."""

    path: str
    size: int
    score: int
    matched_terms: list[str]
    role: str


class RepositoryEvidence(BaseModel):
    root: str
    commit: str | None = None
    reachable: bool = True
    reason: str = ""

    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    manifests: list[str] = Field(default_factory=list)

    total_files: int = 0
    total_bytes: int = 0
    modules: list[str] = Field(default_factory=list)
    signals: list[RepoSignal] = Field(default_factory=list)

    candidates: list[CandidateFile] = Field(default_factory=list)
    related_tests: list[str] = Field(default_factory=list)

    def signal(self, name: str) -> RepoSignal:
        for item in self.signals:
            if item.name == name:
                return item
        return RepoSignal(name=name, present=False)

    def summary(self) -> str:
        if not self.reachable:
            return f"No repository was analysed: {self.reason}"
        present = [item.name for item in self.signals if item.present]
        return (
            f"{self.total_files} source files across {len(self.modules)} top-level modules; "
            f"{', '.join(self.languages) or 'no manifest-declared language'}"
            f"{'; ' + ', '.join(self.frameworks) if self.frameworks else ''}. "
            f"Structural signals present: {', '.join(present) or 'none'}. "
            f"{len(self.candidates)} file(s) rank as the likely change surface."
        )


def _git_commit(root: Path) -> str | None:
    """The HEAD sha, when this is a git checkout and git is on the path."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    sha = result.stdout.strip()
    return sha if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else None


def _read_head(path: Path, limit: int = 40_000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit].lower()
    except OSError:
        return ""


def _overlap(goal: set[str], candidate: set[str]) -> set[str]:
    """Story terms that match a path, allowing a shared stem.

    Exact intersection alone misses the way code is actually named: a story about "estimation"
    never matches `estimate_code.py`, and one about "migrating" never matches `migrations.py`.
    A shared prefix of four or more characters is a real match; three would start pairing
    "test" with "testimony".
    """
    matched: set[str] = set()
    for term in goal:
        for token in candidate:
            if term == token or (
                len(term) >= 4 and len(token) >= 4
                and (term.startswith(token[:4]) and (term.startswith(token) or token.startswith(term)))
            ):
                matched.add(term)
                break
    return matched


def _role(relative: str) -> str:
    """A coarse label for what a file is, used to explain why it was ranked."""
    lowered = relative.lower()
    for name, terms in _SIGNAL_FILES:
        if any(term in lowered for term in terms):
            return name
    parts = lowered.split("/")
    stem = Path(lowered).stem
    # A document called `..._spec.md` is not a test. Require either a test directory or a stem
    # that actually names itself one, and never a prose extension.
    if Path(lowered).suffix in {".md", ".txt", ".rst"}:
        return "docs"
    if any(part in {"test", "tests", "spec", "specs", "__tests__", "e2e"} for part in parts[:-1]):
        return "tests"
    if stem.startswith(("test_", "test-")) or stem.endswith(("_test", "-test", ".test", ".spec")):
        return "tests"
    return "source"


def analyse_repository(workspace_root: str, story_text: str) -> RepositoryEvidence:
    """Read the repository and rank the change surface for this story.

    Deterministic throughout: same repository plus same story text yields the same evidence,
    which is what lets the estimate be reproducible when a commit is pinned.
    """
    raw = (workspace_root or "").strip()
    if not raw:
        return RepositoryEvidence(root="", reachable=False, reason="No workspace path supplied.")
    root = Path(raw).expanduser()
    if not root.is_absolute():
        return RepositoryEvidence(
            root=raw, reachable=False, reason="The workspace path must be absolute."
        )
    if not root.is_dir():
        return RepositoryEvidence(
            root=str(root), reachable=False, reason="That path is not a directory on this machine."
        )
    root = root.resolve()

    files = _walk(root)
    if not files:
        return RepositoryEvidence(
            root=str(root), commit=_git_commit(root), reachable=True,
            reason="The folder holds no readable source files.",
        )

    relatives = [(path, size, _relative(root, path)) for path, size in files]

    # -- stack ---------------------------------------------------------------------------
    languages: set[str] = set()
    manifests: list[str] = []
    frameworks: set[str] = set()
    for pattern, language in _MANIFESTS:
        found = list(root.glob(pattern)) + list(root.glob(f"*/{pattern}"))
        for manifest in found[:3]:
            languages.add(language)
            manifests.append(_relative(root, manifest))
            text = _read_head(manifest)
            for needle, label in _FRAMEWORKS:
                if needle in text:
                    frameworks.add(label)

    # -- architecture --------------------------------------------------------------------
    modules = sorted({
        relative.split("/")[0] for _, _, relative in relatives
        if "/" in relative and relative.split("/")[0] not in SKIP_DIRS
    })

    # -- structural signals ---------------------------------------------------------------
    signals: list[RepoSignal] = []
    for name, terms in _SIGNAL_DIRS + _SIGNAL_FILES:
        # Directory *and* file names. Checking only folders missed `backend/migrations.py` —
        # migration tooling is migration tooling whether or not anyone made a folder for it.
        hits = [
            rel for _, _, rel in relatives
            if any(part in terms for part in rel.lower().split("/")[:-1])
            or any(term in Path(rel).stem.lower() for term in terms)
        ]
        signals.append(RepoSignal(
            name=name, present=bool(hits), count=len(hits), examples=sorted(hits)[:4],
        ))

    # -- change surface --------------------------------------------------------------------
    goal = _words(story_text)
    ranked: list[CandidateFile] = []
    for path, size, relative in relatives:
        terms = sorted(_overlap(goal, _words(relative)))
        if not terms:
            continue
        # Path segments are worth more than the extension or a directory everyone shares, so a
        # file whose *name* matches the story outranks one that merely lives under a matching
        # folder.
        stem_terms = sorted(goal & _words(Path(relative).stem))
        score = len(terms) * 2 + len(stem_terms) * 3
        ranked.append(CandidateFile(
            path=relative, size=size, score=score, matched_terms=terms, role=_role(relative),
        ))
    ranked.sort(key=lambda item: (-item.score, item.path))
    candidates = ranked[:MAX_CANDIDATES]

    # Tests that sit beside a candidate, by stem. A change to `orders.py` with `test_orders.py`
    # in the tree has a known verification cost; one without has an unknown one.
    stems = {Path(item.path).stem.lower().lstrip("test_") for item in candidates}
    related_tests = sorted({
        rel for _, _, rel in relatives
        if _role(rel) == "tests" and any(stem and stem in rel.lower() for stem in stems)
    })[:12]

    return RepositoryEvidence(
        root=str(root),
        commit=_git_commit(root),
        reachable=True,
        languages=sorted(languages),
        frameworks=sorted(frameworks),
        manifests=sorted(set(manifests))[:8],
        total_files=len(relatives),
        total_bytes=sum(size for _, size, _ in relatives),
        modules=modules[:16],
        signals=signals,
        candidates=candidates,
        related_tests=related_tests,
    )


# ---------------------------------------------------------------------------------------
# What the repository can answer that the story did not
# ---------------------------------------------------------------------------------------

class FactorFinding(BaseModel):
    """A repository-derived score for one factor, with the evidence that produced it."""

    factor: str
    score: int = Field(ge=1, le=5)
    reason: str
    evidence: list[str] = Field(default_factory=list)
    #: `answers` means the repository settled a question the story left open. `corroborates`
    #: means it agreed with what the story already said.
    kind: Literal["answers", "corroborates"] = "answers"


def factor_findings(evidence: RepositoryEvidence, story_text: str) -> list[FactorFinding]:
    """Score the factors the repository can genuinely speak to.

    This is the first rung of the §6 ladder. Every finding here replaces a "the story does not
    say" with a fact, and each one names the fact rather than asserting a number.
    """
    if not evidence.reachable or not evidence.total_files:
        return []

    # Every reason below rests on names — directories and filenames — not on file contents.
    # The wording must not exceed that: "no file is named for logging" is what was checked;
    # "no logging exists" is a claim about code nobody read.
    named = "no file or directory in the repository is named for"

    mentions = _words(story_text)
    findings: list[FactorFinding] = []

    def add(factor: str, score: int, reason: str, proof: list[str]) -> None:
        findings.append(FactorFinding(factor=factor, score=score, reason=reason, evidence=proof))

    migrations = evidence.signal("migrations")
    data_terms = mentions & {"schema", "migration", "table", "column", "backfill", "database",
                             "entity", "model", "persist"}
    if data_terms and migrations.present:
        add("data_model_change", 4,
            f"The story names {', '.join(sorted(data_terms)[:3])} and the repository already "
            f"carries {migrations.count} migration file(s), so this change joins an existing "
            f"migration chain rather than inventing one.",
            migrations.examples)
    elif data_terms and not migrations.present:
        add("data_model_change", 5,
            f"The story requires a data change and {named} migrations, so this change either "
            f"establishes that tooling or the repository keeps schema elsewhere.", [])
    elif not data_terms and not migrations.present:
        add("data_model_change", 1,
            f"The story names no data work and {named} migrations.", [])

    tests = evidence.signal("tests")
    if evidence.related_tests:
        add("test_effort", 3,
            f"{len(evidence.related_tests)} existing test file(s) sit beside the likely change "
            f"surface, so the verification pattern is established and reusable.",
            evidence.related_tests[:4])
    elif tests.present:
        add("test_effort", 4,
            f"The repository has {tests.count} test file(s) but none matching the files this "
            f"story is likely to touch. New coverage must be written from scratch.",
            tests.examples)
    else:
        add("test_effort", 5,
            f"{named.capitalize()} tests. There is no established verification pattern to extend."
            .capitalize(), [])

    auth = evidence.signal("auth")
    security_terms = mentions & {"auth", "login", "permission", "token", "security", "pii",
                                 "encrypt", "session", "role"}
    if security_terms and auth.present:
        add("security_review", 3,
            f"The story touches access control and the repository already has an auth surface "
            f"({auth.count} file(s)), so this extends an existing model rather than creating one.",
            auth.examples)
    elif security_terms and not auth.present:
        add("security_review", 5,
            f"The story requires access control and {named} authentication or authorization.", [])
    elif not security_terms and not auth.present:
        add("security_review", 1,
            f"The story raises no access control and {named} authentication.", [])

    observability = evidence.signal("observability")
    add("observability_operations",
        2 if observability.present else 4,
        (f"The repository already emits telemetry ({observability.count} file(s)); this change "
         f"follows the established pattern."
         if observability.present else
         f"{named.capitalize()} logging, metrics or tracing, so operational visibility for this "
         f"change may have to be established."),
        observability.examples)

    audit = evidence.signal("audit")
    compliance_terms = mentions & {"gdpr", "consent", "audit", "retention", "compliance",
                                   "regulatory", "pci", "hipaa"}
    if compliance_terms or audit.present:
        add("regulatory_compliance", 4 if compliance_terms and not audit.present else 3,
            (f"Compliance machinery already exists ({audit.count} file(s))."
             if audit.present else
             "The story raises a compliance obligation the repository has no code for."),
            audit.examples)
    else:
        add("regulatory_compliance", 1,
            "Neither the story nor the repository shows a regulatory obligation.", [])

    api = evidence.signal("api_surface")
    integration_terms = mentions & {"api", "endpoint", "webhook", "event", "queue", "downstream",
                                    "consumer", "integration", "publish"}
    queues = evidence.signal("queues")
    if integration_terms:
        add("integration_surface", 4 if queues.present else 3,
            (f"The story names integration work and the repository has {api.count} API-surface "
             f"file(s)"
             + (f" plus messaging infrastructure ({queues.count} file(s))" if queues.present else "")
             + "."),
            (api.examples + queues.examples)[:4])
    elif not api.present:
        add("integration_surface", 1,
            f"{named.capitalize()} routes, controllers or messaging, and the story names no "
            f"integration work.", [])

    ci = evidence.signal("ci")
    containers = evidence.signal("containers")
    add("dod_overhead",
        2 if ci.present else 4,
        (f"A CI pipeline already exists ({ci.count} file(s))"
         + (" alongside container or deployment manifests" if containers.present else "")
         + ", so release overhead is the established path."
         if ci.present else
         f"{named.capitalize()} a CI pipeline, so verification and release for this change may be "
         f"manual and belong in the estimate."),
        (ci.examples + containers.examples)[:4])

    docs = evidence.signal("docs")
    add("documentation_knowledge_transfer",
        2 if docs.present else 3,
        (f"A documentation tree exists ({docs.count} file(s)) and is the place this change is "
         f"recorded." if docs.present else
         f"{named.capitalize()} documentation, so knowledge transfer for this change has nowhere "
         f"established to live."),
        docs.examples)

    # Technical complexity from the measured shape of the change surface rather than adjectives.
    surface = len(evidence.candidates)
    spread = len({Path(item.path).parts[0] for item in evidence.candidates if "/" in item.path})
    if surface:
        score = 2 if surface <= 3 and spread <= 1 else 3 if surface <= 8 and spread <= 2 else 4
        add("technical_complexity", score,
            f"The story's terms match {surface} file(s) across {max(spread, 1)} module(s), which "
            f"is the measured breadth of the change surface rather than an impression of it.",
            [item.path for item in evidence.candidates[:4]])

    if not evidence.candidates:
        add("uncertainty", 4,
            "No file in the repository matches any word in the story. Either this work is "
            "entirely new here, or the story describes a different system — both are reasons "
            "to confirm scope before committing to a number.", [])

    flags = evidence.signal("feature_flags")
    add("reversibility",
        2 if flags.present else 4 if data_terms else 3,
        (f"Feature-flag machinery exists ({flags.count} file(s)), so this change can be turned "
         f"off without a deploy." if flags.present else
         f"{named.capitalize()} feature flags, and the change alters stored data, so reversing it "
         f"likely means another migration." if data_terms else
         f"{named.capitalize()} feature flags; reversing this change likely means a redeploy."),
        flags.examples)

    return findings


# ---------------------------------------------------------------------------------------
# What would change
# ---------------------------------------------------------------------------------------

class PlannedChange(BaseModel):
    path: str
    action: Literal["modify", "create"]
    reason: str
    detail: str
    evidence: list[str] = Field(default_factory=list)
    #: False when the model named a path that is not in the repository and not in an existing
    #: directory. Rejected rather than shown, but counted so the reader knows it happened.
    verified: bool = True


class ChangePlan(BaseModel):
    changes: list[PlannedChange] = Field(default_factory=list)
    rejected_paths: list[str] = Field(default_factory=list)
    note: str = ""

    @property
    def modified(self) -> int:
        return sum(item.action == "modify" for item in self.changes)

    @property
    def created(self) -> int:
        return sum(item.action == "create" for item in self.changes)


def validate_paths(root: Path, proposed: list[dict[str, Any]],
                   evidence: RepositoryEvidence) -> ChangePlan:
    """Keep only changes that name a real file, or a new file in a real directory.

    This is the grounding contract applied to file paths, and it is the difference between a
    plan and a plausible sentence. A model asked to name files in a repository it has only seen
    a listing of will confidently produce `src/services/RiskClassifier.java` for a repository
    with no `src/services`, and that path will then be read as a finding.
    """
    known = {item.path for item in evidence.candidates}
    directories = {str(Path(item.path).parent).replace("\\", "/") for item in evidence.candidates}
    directories |= set(evidence.modules)
    changes: list[PlannedChange] = []
    rejected: list[str] = []

    for entry in proposed:
        path = str(entry.get("path", "")).strip().replace("\\", "/").lstrip("./")
        if not path or ".." in Path(path).parts:
            continue
        detail = str(entry.get("detail") or entry.get("change") or "").strip()
        reason = str(entry.get("reason") or "").strip()
        exists = path in known or (root / path).is_file()
        parent = str(Path(path).parent).replace("\\", "/")
        parent_exists = parent in {".", ""} or parent in directories or (root / parent).is_dir()

        if exists:
            action: Literal["modify", "create"] = "modify"
        elif parent_exists:
            action = "create"
        else:
            rejected.append(path)
            continue
        changes.append(PlannedChange(
            path=path, action=action,
            reason=reason or "Named by the estimation pass as part of this change.",
            detail=detail or "No detail supplied.",
            evidence=[path] if exists else [parent],
        ))

    note = (
        f"{len(changes)} change(s) verified against the repository."
        + (f" {len(rejected)} proposed path(s) named directories that do not exist and were "
           f"discarded rather than shown." if rejected else "")
    )
    return ChangePlan(changes=changes[:20], rejected_paths=rejected[:8], note=note)


def fallback_plan(evidence: RepositoryEvidence) -> ChangePlan:
    """The deterministic plan, used when the model supplies none that survive validation.

    The ranked candidates are already the answer to "which files does this story touch"; the
    model's contribution is *what changes inside them*. Without it there is still a defensible
    change surface to show, clearly labelled as ranked rather than reasoned.
    """
    changes = [
        PlannedChange(
            path=item.path, action="modify",
            reason=f"Ranked on the story's own terms: {', '.join(item.matched_terms[:3])}.",
            detail="Ranked by name overlap with the story; the specific change was not derived.",
            evidence=[item.path],
        )
        for item in evidence.candidates[:8]
    ]
    return ChangePlan(
        changes=changes,
        note=(
            "Change surface ranked from the repository. No per-file change detail was produced, "
            "so these are candidates rather than a plan."
            if changes else "No file in the repository matched the story's terms."
        ),
    )


def prompt_block(evidence: RepositoryEvidence) -> str:
    """Repository evidence rendered for the estimation prompt, within its budget."""
    if not evidence.reachable or not evidence.total_files:
        return ""
    present = [item for item in evidence.signals if item.present]
    absent = [item.name for item in evidence.signals if not item.present]
    lines = [
        f"Root: {evidence.root}",
        f"Commit: {evidence.commit or 'not a git checkout'}",
        f"Scale: {evidence.total_files} source files; modules: {', '.join(evidence.modules[:10])}",
        f"Stack: {', '.join(evidence.languages) or 'undeclared'}"
        + (f"; frameworks: {', '.join(evidence.frameworks)}" if evidence.frameworks else ""),
        "",
        "Structural signals PRESENT: "
        + ("; ".join(f"{item.name} ({item.count})" for item in present) or "none"),
        "Structural signals ABSENT: " + (", ".join(absent) or "none"),
        "",
        "Likely change surface, ranked by overlap with the story's own words:",
    ]
    for item in evidence.candidates[:14]:
        lines.append(f"  {item.path}  [{item.role}]  matched: {', '.join(item.matched_terms[:4])}")
    if evidence.related_tests:
        lines.append("")
        lines.append("Existing tests beside that surface: " + ", ".join(evidence.related_tests[:8]))
    return "\n".join(lines)[:REPO_CONTEXT_BUDGET]


def counts(evidence: RepositoryEvidence) -> dict[str, int]:
    """Small denormalised figures for the UI and the evidence record."""
    roles = Counter(item.role for item in evidence.candidates)
    return {
        "source_files": evidence.total_files,
        "modules": len(evidence.modules),
        "candidates": len(evidence.candidates),
        "related_tests": len(evidence.related_tests),
        "signals_present": sum(item.present for item in evidence.signals),
        "signals_absent": sum(not item.present for item in evidence.signals),
        **{f"candidates_{role}": count for role, count in roles.items()},
    }


__all__ = [
    "MAX_CANDIDATES",
    "CandidateFile",
    "ChangePlan",
    "FactorFinding",
    "PlannedChange",
    "RepoSignal",
    "RepositoryEvidence",
    "analyse_repository",
    "counts",
    "factor_findings",
    "fallback_plan",
    "prompt_block",
    "validate_paths",
    "SOURCE_EXTENSIONS",
]
