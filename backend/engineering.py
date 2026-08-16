"""The engineering discipline the enterprise pipeline specification asks for.

The specification describes twenty-three agents. A 1B model on CPU cannot execute that as one
prompt — this codebase has already established, repeatedly, that asking a model this size for
sixteen scored objects in one response returns the shape without the content. Handing it a
twenty-three-agent brief would return a plausible-looking transcript of a pipeline that never
ran, which is precisely the failure the specification's own anti-hallucination rules exist to
prevent.

So the discipline is implemented the way the rest of this application implements discipline:
as deterministic gates in code, with the model asked only the small questions it can answer.
Each piece here corresponds to a numbered agent, and each is decidable from evidence:

*   **Agent 6 — Requirement Analyst.** `RequirementSpec` gives every functional and
    non-functional requirement an id, so a change can be traced to the thing that asked for it.
*   **Agent 10 — Change Necessity Validator.** `assess_necessity` is the "prove before modify"
    gate. A proposed edit that cannot say which requirement it serves is dropped, not shipped.
*   **Agent 23 — Final Judge.** `traceability` and `final_decision` decide from evidence, and
    refuse to approve on absent evidence rather than on bad evidence.

Two rules from the specification are load-bearing throughout and are worth stating once:

**Prove before modify.** A file is changed because a requirement needs it, not because it is
related to the subject. Relatedness is how a two-line change becomes a forty-file diff nobody
can review.

**Never claim what was not observed.** Nothing here reports a build or a test as passing. The
generated code is never executed, so the only honest words are the ones the specification
supplies: `NOT EXECUTED` and `NOT VERIFIED`.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

#: The exact words for evidence that does not exist. Fixed strings, because "unknown" and
#: "n/a" and "could not determine" all mean the same thing to a writer and different things to
#: a reader — and because a fixed phrase is something a test can assert.
NOT_EXECUTED = "NOT EXECUTED — execution environment unavailable."
NOT_VERIFIED = "NOT VERIFIED — no evidence was available to confirm this."


# ---------------------------------------------------------------------------------------
# Agent 6 — Requirement Analyst
# ---------------------------------------------------------------------------------------

#: Non-functional concerns, and the words that evidence each in a requirement. Matching is on
#: the requirement's own text: a concern is recorded when the requirement raises it, never
#: because it is a concern most systems have.
_NFR_SIGNALS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("security", "Authentication, authorization and protection of the data involved",
     ("auth", "login", "permission", "role", "token", "encrypt", "pii", "sensitive", "secure")),
    ("privacy", "Handling of personal data and consent",
     ("gdpr", "consent", "personal data", "pii", "retention", "anonymi", "redact")),
    ("audit", "A durable record of who did what and when",
     ("audit", "trail", "history", "who changed", "traceab", "log of")),
    ("performance", "Response time, throughput and resource cost under load",
     ("performance", "latency", "throughput", "fast", "slow", "load", "scale", "concurrent")),
    ("availability", "Behaviour when a dependency or the service itself is unavailable",
     ("availability", "uptime", "failover", "retry", "outage", "degrade", "resilien")),
    ("backward_compatibility", "Existing callers and existing data keep working",
     ("backward", "existing", "legacy", "migrat", "compatib", "without breaking", "keep working")),
    ("observability", "Whether an operator can tell what happened",
     ("monitor", "metric", "alert", "dashboard", "trace", "observab", "logging")),
    ("data_consistency", "What is guaranteed when part of the operation fails",
     ("transaction", "atomic", "consistent", "rollback", "idempot", "partial failure")),
)

#: Edge cases the specification requires be considered. Each is a question, not an assertion,
#: because whether it applies is a judgement about the requirement rather than a fact about it.
EDGE_CASES: tuple[str, ...] = (
    "invalid input",
    "empty or null input",
    "duplicate or repeated operations",
    "retry behaviour",
    "partial failure",
    "authorization failure",
    "dependency failure",
    "concurrency",
    "idempotency",
    "compatibility with data that already exists",
)


class Requirement(BaseModel):
    """One numbered requirement, so a change can be traced to what asked for it."""

    id: str
    kind: Literal["functional", "non_functional"]
    statement: str
    #: Where in the supplied text this came from. A requirement with no source is an invention.
    source: str
    acceptance: list[str] = Field(default_factory=list)


class Assumption(BaseModel):
    """Something the requirement did not say, recorded rather than quietly decided.

    The specification is explicit that missing business behaviour must never be silently
    invented. An assumption is the honest alternative: it names what was not stated, says what
    was done about it, and remains visible in the output so a reader can overrule it.
    """

    id: str
    about: str
    assumed: str
    because: str = "The supplied text does not contain this information."


class RequirementSpec(BaseModel):
    functional: list[Requirement] = Field(default_factory=list)
    non_functional: list[Requirement] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [item.id for item in self.functional + self.non_functional]

    def summary(self) -> str:
        return (
            f"{len(self.functional)} functional and {len(self.non_functional)} non-functional "
            f"requirement(s), {len(self.assumptions)} recorded assumption(s)."
        )


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text or "")
    return [item.strip(" -•\t") for item in parts if len(item.strip(" -•\t")) >= 12]


def analyse_requirement(
    objective: str, acceptance_criteria: list[str] | None = None
) -> RequirementSpec:
    """Decompose the requirement into numbered, traceable statements.

    Deterministic. Every requirement produced here quotes the text it came from, so nothing in
    the specification is something the requirement did not say. Where the text is thin the spec
    is thin, and the assumptions say so rather than padding it out.
    """
    criteria = [item.strip() for item in (acceptance_criteria or []) if item.strip()]
    body = (objective or "").strip()

    functional: list[Requirement] = []
    for index, sentence in enumerate(_sentences(body)[:8], start=1):
        functional.append(Requirement(
            id=f"FR-{index:03d}", kind="functional", statement=sentence, source="objective",
        ))
    if not functional and body:
        functional.append(Requirement(
            id="FR-001", kind="functional", statement=body, source="objective",
        ))

    # Acceptance criteria attach to the requirement they verify where one exists, and stand as
    # their own requirement where none does — a criterion nobody implements is still a promise.
    for index, criterion in enumerate(criteria, start=1):
        target = next(
            (item for item in functional
             if set(_words(criterion)) & set(_words(item.statement))),
            functional[0] if functional else None,
        )
        if target is not None:
            target.acceptance.append(criterion)
        else:
            functional.append(Requirement(
                id=f"FR-{len(functional) + 1:03d}", kind="functional", statement=criterion,
                source=f"acceptance criterion {index}", acceptance=[criterion],
            ))

    haystack = " ".join([body, *criteria]).lower()
    non_functional: list[Requirement] = []
    for number, (name, description, terms) in enumerate(_NFR_SIGNALS, start=1):
        matched = [term for term in terms if term in haystack]
        if matched:
            non_functional.append(Requirement(
                id=f"NFR-{len(non_functional) + 1:03d}", kind="non_functional",
                statement=f"{description}.",
                source=f"the requirement mentions {', '.join(sorted(matched)[:3])}",
            ))

    assumptions: list[Assumption] = []
    if not criteria:
        assumptions.append(Assumption(
            id="ASSUMPTION-001", about="acceptance criteria",
            assumed="Success is whatever the objective describes, since no criteria were given.",
        ))
    if not any(item.id.startswith("NFR") and "compatib" in item.statement.lower()
               for item in non_functional):
        assumptions.append(Assumption(
            id=f"ASSUMPTION-{len(assumptions) + 1:03d}", about="backward compatibility",
            assumed="Existing callers and existing data must keep working; nothing in the "
                    "requirement asks for a breaking change.",
        ))

    open_questions = [
        f"Does this requirement have defined behaviour for {case}?"
        for case in EDGE_CASES
        if not any(word in haystack for word in case.split() if len(word) > 4)
    ][:6]

    return RequirementSpec(
        functional=functional, non_functional=non_functional,
        assumptions=assumptions, open_questions=open_questions,
    )


def _words(value: str) -> set[str]:
    return {item.lower() for item in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", value or "")}


# ---------------------------------------------------------------------------------------
# Agent 10 — Change Necessity Validator
# ---------------------------------------------------------------------------------------

class NecessityVerdict(BaseModel):
    path: str
    action: str
    necessary: bool
    requirement: str | None = None
    evidence: str
    reason: str
    alternative: str = ""


class NecessityReport(BaseModel):
    verdicts: list[NecessityVerdict] = Field(default_factory=list)
    dropped: list[str] = Field(default_factory=list)
    #: Files that were examined and deliberately left alone. The specification asks for these
    #: explicitly, and they are the clearest evidence that the change is minimal: a reviewer
    #: can see what was considered and rejected, not only what was touched.
    reviewed_unchanged: list[dict[str, str]] = Field(default_factory=list)

    @property
    def necessary(self) -> list[NecessityVerdict]:
        return [item for item in self.verdicts if item.necessary]


def assess_necessity(
    edits: list[Any],
    spec: RequirementSpec,
    candidates: list[str] | None = None,
) -> NecessityReport:
    """Prove before modify.

    A proposed edit survives when it can name the requirement it serves. One that cannot is not
    obviously wrong — it is unjustified, which is a different and more useful thing to report,
    because a diff full of unjustified files is how a two-line change becomes unreviewable.

    Matching is on the words the requirement itself uses, so the justification is checkable
    rather than asserted.
    """
    requirement_terms = {
        item.id: _words(item.statement) | {word for line in item.acceptance for word in _words(line)}
        for item in spec.functional + spec.non_functional
    }
    verdicts: list[NecessityVerdict] = []
    dropped: list[str] = []

    for edit in edits:
        path = str(getattr(edit, "path", "") or "")
        action = str(getattr(edit, "action", "") or "modify")
        reason = str(getattr(edit, "reason", "") or "")
        haystack = _words(path.replace("/", " ").replace("_", " ")) | _words(reason)

        best_id, best_overlap = None, 0
        for requirement_id, terms in requirement_terms.items():
            overlap = len(haystack & terms)
            if overlap > best_overlap:
                best_id, best_overlap = requirement_id, overlap

        if best_id is not None:
            verdicts.append(NecessityVerdict(
                path=path, action=action, necessary=True, requirement=best_id,
                evidence=f"The path and stated reason share {best_overlap} term(s) with {best_id}.",
                reason=reason or "Named by the implementation pass.",
            ))
        elif action == "create":
            # A new file cannot be matched against a path that does not exist yet, and creating
            # one is a positive act with a stated purpose. It is kept, and flagged as unmatched.
            verdicts.append(NecessityVerdict(
                path=path, action=action, necessary=True, requirement=None,
                evidence=NOT_VERIFIED,
                reason=reason or "New file; no requirement id could be matched to it.",
                alternative="Confirm this file is not duplicating something that already exists.",
            ))
        else:
            dropped.append(path)
            verdicts.append(NecessityVerdict(
                path=path, action=action, necessary=False, requirement=None,
                evidence="No requirement's own words appear in this path or its stated reason.",
                reason="Related to the subject is not the same as required by the requirement.",
                alternative="Leave the file unchanged, or state which requirement needs it.",
            ))

    changed = {item.path for item in verdicts}
    reviewed_unchanged = [
        {"path": path,
         "reason": "Ranked as part of the change surface, but no proposed change needed it."}
        for path in (candidates or []) if path not in changed
    ][:12]

    return NecessityReport(
        verdicts=verdicts, dropped=dropped, reviewed_unchanged=reviewed_unchanged,
    )


# ---------------------------------------------------------------------------------------
# Agent 23 — Final Judge
# ---------------------------------------------------------------------------------------

class TraceRow(BaseModel):
    requirement: str
    statement: str
    implementation: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    evidence: str = NOT_EXECUTED
    status: Literal["covered", "untested", "uncovered"] = "uncovered"


def traceability(spec: RequirementSpec, report: NecessityReport) -> list[TraceRow]:
    """Every requirement, and what claims to satisfy it.

    A requirement with no implementation is the finding this table exists to surface. It is
    listed as uncovered rather than omitted, because a matrix that only shows what was done
    cannot show what was forgotten.
    """
    rows: list[TraceRow] = []
    for item in spec.functional + spec.non_functional:
        files = [v.path for v in report.necessary if v.requirement == item.id]
        tests = [path for path in files if _is_test(path)]
        code = [path for path in files if not _is_test(path)]
        rows.append(TraceRow(
            requirement=item.id, statement=item.statement, implementation=code, tests=tests,
            status="covered" if code and tests else "untested" if code else "uncovered",
        ))
    return rows


def _is_test(path: str) -> bool:
    lowered = path.lower()
    stem = lowered.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return (
        any(part in {"test", "tests", "spec", "specs", "__tests__", "e2e"}
            for part in lowered.split("/")[:-1])
        or stem.startswith(("test_", "test-"))
        or stem.endswith(("_test", "-test", ".test", ".spec"))
    )


class FinalDecision(BaseModel):
    decision: Literal["APPROVED", "NEEDS_FIX", "BLOCKED"]
    requirement_coverage: int
    build_status: str = NOT_EXECUTED
    test_status: str = NOT_EXECUTED
    critical_issues: list[str] = Field(default_factory=list)
    remaining_assumptions: list[str] = Field(default_factory=list)
    confidence: int = 0
    ready_for_pull_request: bool = False
    reasoning: str = ""


def final_decision(
    spec: RequirementSpec,
    rows: list[TraceRow],
    report: NecessityReport,
    structural_failures: list[str],
) -> FinalDecision:
    """Decide from evidence, and refuse to approve on absent evidence.

    The specification forbids approving when the build fails, when tests fail, when critical
    findings remain, or when requirement coverage is incomplete. Nothing here executes the
    generated code, so the build and test status are permanently `NOT EXECUTED` — and a
    decision that cannot see a green build cannot be `APPROVED`. Saying so is the point.
    """
    covered = [row for row in rows if row.status == "covered"]
    coverage = round(100 * len(covered) / len(rows)) if rows else 0

    critical = list(structural_failures)
    uncovered = [row.requirement for row in rows if row.status == "uncovered"]
    if uncovered:
        critical.append(f"{len(uncovered)} requirement(s) have no implementation: "
                        f"{', '.join(uncovered[:4])}")

    if structural_failures:
        decision, ready = "BLOCKED", False
        reasoning = ("Structural checks failed, so nothing here can be applied. A change that "
                     "does not parse is not a change.")
    elif critical or coverage < 100:
        decision, ready = "NEEDS_FIX", False
        reasoning = ("Requirement coverage is incomplete or a finding remains open. The "
                     "specification forbids approving on either.")
    else:
        # Everything decidable passed. It is still not APPROVED, and that is not pessimism:
        # approval requires a green build and green tests, and neither was run.
        decision, ready = "NEEDS_FIX", False
        reasoning = (
            "Every requirement traces to an implementation and a test, and every structural "
            f"check passed. Approval still requires evidence this system cannot produce: "
            f"the build and the tests were {NOT_EXECUTED.split(' — ')[0]}. Run them before "
            "opening a pull request."
        )

    return FinalDecision(
        decision=decision,
        requirement_coverage=coverage,
        critical_issues=critical[:6],
        remaining_assumptions=[f"{item.id}: {item.assumed}" for item in spec.assumptions],
        confidence=max(0, min(100, coverage - 15 * len(critical))),
        ready_for_pull_request=ready,
        reasoning=reasoning,
    )


__all__ = [
    "EDGE_CASES",
    "NOT_EXECUTED",
    "NOT_VERIFIED",
    "Assumption",
    "FinalDecision",
    "NecessityReport",
    "NecessityVerdict",
    "Requirement",
    "RequirementSpec",
    "TraceRow",
    "analyse_requirement",
    "assess_necessity",
    "final_decision",
    "traceability",
]
