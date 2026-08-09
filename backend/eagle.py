"""EAGLE — Evidence-Augmented Governed Layered Estimation.

The governance layer around the deterministic v2 calculator. Its operating rule is the one
the architecture opens with:

    Agents discover and reason. Code calculates. Reviewers challenge. Evidence decides.
    History calibrates.

Everything in this module is deterministic. That is the point, not a limitation: EAGLE's
reproducibility target is *same story + same snapshot + same calibration data + same harness
= same estimate*, and a rule implemented in a prompt cannot make that promise. The model's job
stays where `estimate_code.py` already puts it — reading a story and proposing a 1-5 score per
factor with a reason. Everything here consumes those proposals and never asks for a number.

Two honest notes about how this differs from the document, both forced by the runtime:

*   §9 asks for three or more independent estimators and a per-factor median. `median_scores`
    implements exactly that for any number of assessments, but the local runtime serialises a
    CPU-bound 1B model, so a third full pass costs minutes of wall clock for a signal the
    second pass already provides. The pipeline supplies two model passes and the median of two
    is their midpoint; `aggregate` therefore reports `estimator_count` so a reader can see how
    much independence actually backed the number. Wire in a third pass and the same function
    starts returning true medians with no other change.

*   §12 and §13 describe the adversarial and optimistic reviewers as agents. Here they are
    deterministic checklist evaluators over the scorecard, the stack profile and the evidence
    blackboard. A reviewer whose findings vary between runs cannot be part of a reproducible
    pipeline, and the checks these two perform — double counting, unsupported scores, missing
    rollback, platform work already solved — are all decidable from the evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from statistics import median
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.estimation_framework import (
    FACTOR_BY_ID,
    FACTORS,
    FRAMEWORK_VERSION,
    Calculation,
    FactorScore,
    StackProfile,
)

#: Bumped whenever the agent graph, its rules, or its thresholds change. Part of the snapshot
#: so two runs that differ can be told apart by harness version rather than by guesswork.
EAGLE_VERSION = "EAGLE-1.0"
RUBRIC_VERSION = "3.2"

#: §15. The debate is bounded, and an unresolved dispute escalates rather than looping.
MAX_DEBATE_ROUNDS = 2
MAX_RETRIEVAL_ROUNDS = 2

#: §14. Spread is `max(scores) - min(scores)` per factor.
DISPUTE_SPREAD = 2

#: §7. One specialist owns the primary evidence for each factor. Secondary opinions are
#: allowed; ownership decides who must produce evidence when a factor is challenged.
FACTOR_OWNER: dict[str, str] = {
    "requirements_clarity": "Requirements Analyst",
    "uncertainty": "Requirements Analyst",
    "technical_complexity": "Architecture Analyst",
    "integration_surface": "Architecture Analyst",
    "reversibility": "Architecture Analyst",
    "data_model_change": "Data Agent",
    "frontend_effort": "Frontend Agent",
    "backend_effort": "Backend Agent",
    "test_effort": "Test Engineering Agent",
    "regulatory_compliance": "Compliance Agent",
    "security_review": "Security Agent",
    "observability_operations": "SRE / Observability Agent",
    "cross_team_dependency": "Dependency Agent",
    "performance_scalability": "Performance Agent",
    "documentation_knowledge_transfer": "Documentation Agent",
    "dod_overhead": "Delivery / DoD Agent",
}

#: §29. Failures are attributed to an architectural layer, because "retry with a bigger prompt"
#: is the wrong response to eleven of these twelve.
FAILURE_LAYERS = (
    "task_contract", "context", "retrieval", "repository_understanding", "reference_story",
    "agent_reasoning", "reviewer", "judge", "rule_engine", "model", "tool", "calibration",
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _digest(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


# ---------------------------------------------------------------------------------------
# §2  Estimation Contract
# ---------------------------------------------------------------------------------------

class ContractCompletion(BaseModel):
    """What must be true before an estimate may be published."""

    require_code_evidence: bool = False
    require_reference_story: bool = True
    require_independent_review: bool = True


class ContractStopConditions(BaseModel):
    """§2. Named exits, so the pipeline can refuse rather than manufacture a number."""

    uncertainty_5: str = "SPIKE"
    insufficient_context: str = "CLARIFY"
    unresolved_disagreement: str = "HUMAN_REVIEW"


class EstimationContract(BaseModel):
    """The immutable agreement every downstream stage works against.

    Frozen on purpose: §21 makes reproducibility depend on the story version, the snapshot and
    the framework version all being fixed for the duration of a run. A contract that a later
    stage can edit is not a contract, and a pipeline whose inputs move cannot explain why two
    runs disagreed.
    """

    model_config = {"frozen": True}

    story_id: str
    title: str
    objective: str
    acceptance_criteria: tuple[str, ...] = ()
    affected_application: str = ""
    repository_commit: str | None = None
    constraints: dict[str, bool] = Field(default_factory=dict)
    expected_stacks: dict[str, str] = Field(default_factory=dict)

    methodology: str = f"estimation-framework-v{FRAMEWORK_VERSION}"
    factor_count: int = len(FACTORS)
    score_range: str = "1-5"
    fibonacci: bool = True

    completion: ContractCompletion = Field(default_factory=ContractCompletion)
    stop_conditions: ContractStopConditions = Field(default_factory=ContractStopConditions)

    #: §2 budget and retry limits, so a run cannot spend unboundedly on a story it cannot read.
    max_debate_rounds: int = MAX_DEBATE_ROUNDS
    max_retrieval_rounds: int = MAX_RETRIEVAL_ROUNDS

    contract_version: int = 1
    contract_hash: str = ""

    def required_evidence(self) -> tuple[str, ...]:
        """Evidence the contract will not publish an estimate without."""
        required = ["story objective", "acceptance criteria"]
        if self.completion.require_code_evidence:
            required.append("repository evidence")
        if self.completion.require_reference_story:
            required.append("reference story")
        if self.completion.require_independent_review:
            required.append("independent review")
        return tuple(required)


def build_contract(story: Any, *, repository_commit: str | None = None) -> EstimationContract:
    """Convert an incoming story into the versioned contract the run is bound to."""
    stack = story.stack
    body = {
        "story_id": (getattr(story, "issue_key", "") or story.title).strip(),
        "title": story.title.strip(),
        "objective": (story.user_story or story.title).strip(),
        "acceptance_criteria": tuple(item.strip() for item in story.acceptance_criteria),
        "affected_application": ", ".join(sorted(set(story.components))) or "unspecified",
        "repository_commit": repository_commit,
        "constraints": {
            # Read from the story text rather than assumed: §6 forbids silently filling gaps.
            "backward_compatible": "backward compat" in " ".join(story.acceptance_criteria).lower(),
            "production_change": stack.scenario != "standard",
        },
        "expected_stacks": {
            key: value
            for key, value in (
                ("frontend", stack.frontend), ("backend", stack.backend),
                ("database", stack.database),
            )
            if value and value != "none"
        },
        "completion": ContractCompletion(
            # Repository evidence is only required when the story claims a repository.
            require_code_evidence=bool(repository_commit),
        ).model_dump(),
        "stop_conditions": ContractStopConditions().model_dump(),
    }
    return EstimationContract(**body, contract_hash=_digest(body))


# ---------------------------------------------------------------------------------------
# §5  Evidence Blackboard
# ---------------------------------------------------------------------------------------

SourceType = Literal["story", "acceptance_criteria", "repository", "history", "architecture",
                     "team_profile", "heuristic"]


class Evidence(BaseModel):
    """One citable claim.

    §5 requires every important score to answer: what was scored, why, on which evidence, from
    which source, at what confidence. An evidence record that cannot answer all five is not
    evidence, it is an assertion, so every field here is required except the factor — a claim
    may be general to the story.
    """

    evidence_id: str
    factor: str | None = None
    claim: str
    source_type: SourceType
    source: str
    location: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    #: §23. Story text is third-party input; repository and history are ours. A trusted record
    #: may carry instructions, an untrusted one is data only.
    trusted: bool = False


class Blackboard(BaseModel):
    """Structured shared state. Agents communicate through this, not conversational history."""

    records: list[Evidence] = Field(default_factory=list)

    def add(self, record: Evidence) -> Evidence:
        self.records.append(record)
        return record

    def by_id(self, evidence_id: str) -> Evidence | None:
        return next((item for item in self.records if item.evidence_id == evidence_id), None)

    def for_factor(self, factor: str) -> list[Evidence]:
        return [item for item in self.records if item.factor in (factor, None)]

    def ids_for(self, factor: str) -> list[str]:
        return [item.evidence_id for item in self.for_factor(factor)]

    def sources(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.records:
            counts[item.source_type] = counts.get(item.source_type, 0) + 1
        return counts


def build_blackboard(story: Any, scorecard: list[FactorScore]) -> Blackboard:
    """Seed the blackboard from the story and from what each scorer actually cited."""
    board = Blackboard()
    board.add(Evidence(
        evidence_id="EV-001", claim=story.title.strip() or "Untitled story",
        source_type="story", source="story.title", confidence=1.0,
    ))
    if story.user_story.strip():
        board.add(Evidence(
            evidence_id="EV-002", claim=story.user_story.strip()[:400],
            source_type="story", source="story.description", confidence=0.95,
        ))
    for index, criterion in enumerate(story.acceptance_criteria):
        board.add(Evidence(
            evidence_id=f"EV-AC-{index + 1}", claim=criterion.strip()[:400],
            source_type="acceptance_criteria", source="story.acceptance_criteria",
            location=f"criterion {index + 1}", confidence=0.98,
        ))
    if story.technical_breakdown:
        board.add(Evidence(
            evidence_id="EV-TECH", claim=story.technical_breakdown.strip()[:400],
            source_type="story", source="story.technical_breakdown", confidence=0.9,
        ))
    # One record per factor, carrying the scorer's own reason and its provenance. A heuristic
    # fill is recorded at low confidence rather than presented as if it were read from the story.
    for index, item in enumerate(scorecard):
        board.add(Evidence(
            evidence_id=f"EV-F{FACTOR_BY_ID[item.factor].number:02d}",
            factor=item.factor,
            claim=item.reason,
            source_type="story" if item.provenance == "model" else "heuristic",
            source=f"{FACTOR_OWNER.get(item.factor, 'Estimator')} · {item.provenance}",
            location=f"factor {FACTOR_BY_ID[item.factor].number}",
            confidence=0.85 if item.provenance == "model" else 0.35,
        ))
    return board


# ---------------------------------------------------------------------------------------
# §9 / §14  Independent scoring, median aggregation, conflict detection
# ---------------------------------------------------------------------------------------

class FactorAggregate(BaseModel):
    factor: str
    label: str
    owner: str
    scores: list[int]
    spread: int
    median_score: int
    #: §14: 0 accept, 1 accept median, >=2 dispute, missing evidence dispute.
    status: Literal["accept", "accept_median", "dispute"]
    reason: str
    evidence_ids: list[str]


def median_scores(assessments: list[dict[str, int]]) -> dict[str, int]:
    """Per-factor median across independent estimators (§9).

    Median rather than mean: a single outlier estimator should not drag the number, which is
    the whole reason for scoring blind in the first place. With an even count the median falls
    between two values, and the result is rounded up — an estimate that splits the difference
    should not round *towards* optimism.
    """
    if not assessments:
        return {}
    factors = {factor for item in assessments for factor in item}
    result: dict[str, int] = {}
    for factor in factors:
        values = [item[factor] for item in assessments if factor in item]
        if not values:
            continue
        result[factor] = int(-(-median(values) // 1))  # ceil, keeping ints exact
    return result


def aggregate(
    assessments: list[dict[str, int]],
    board: Blackboard,
) -> tuple[dict[str, int], list[FactorAggregate]]:
    """Combine independent proposals and mark which factors are in dispute."""
    medians = median_scores(assessments)
    rows: list[FactorAggregate] = []
    for definition in FACTORS:
        values = [item[definition.id] for item in assessments if definition.id in item]
        if not values:
            continue
        spread = max(values) - min(values)
        # §6: a score with no supporting evidence is disputed, never quietly accepted. This is
        # the rule that stops "missing information → assume medium → 3".
        supported = [
            item for item in board.for_factor(definition.id)
            if item.factor == definition.id and item.confidence >= 0.5
        ]
        if spread >= DISPUTE_SPREAD:
            status, reason = "dispute", f"spread of {spread} across {len(values)} estimators"
        elif not supported and medians[definition.id] >= 4:
            status, reason = "dispute", "elevated score carries no supporting evidence"
        elif spread == 1:
            status, reason = "accept_median", "one point apart; median accepted"
        else:
            status, reason = "accept", "estimators agree"
        rows.append(FactorAggregate(
            factor=definition.id, label=definition.label,
            owner=FACTOR_OWNER.get(definition.id, "Estimator"),
            scores=values, spread=spread, median_score=medians[definition.id],
            status=status, reason=reason, evidence_ids=board.ids_for(definition.id),
        ))
    return medians, rows


# ---------------------------------------------------------------------------------------
# §11 / §12 / §13  Critic, adversarial and optimistic review
# ---------------------------------------------------------------------------------------

class ReviewFinding(BaseModel):
    """§11 requires all six fields. A finding without a suggested correction is a complaint."""

    reviewer: Literal["critic", "adversarial", "optimistic"]
    finding: str
    severity: Literal["blocker", "material", "advisory"]
    factor: str | None
    evidence_ids: list[str]
    suggested_correction: str
    confidence: float = Field(ge=0.0, le=1.0)


#: §13. Complexity the platform already solves should not be paid for twice. Each entry is a
#: pair of factors that commonly double-count the same work, plus what absorbs it.
_DOUBLE_COUNTED = (
    ("security_review", "backend_effort", "shared authentication and authorization libraries"),
    ("observability_operations", "dod_overhead", "the platform's standard logging and metrics"),
    ("regulatory_compliance", "security_review", "one control set satisfying both reviews"),
    ("documentation_knowledge_transfer", "dod_overhead", "the team's definition-of-done template"),
)


def critic_review(
    rows: list[FactorAggregate], scores: dict[str, int], stack: StackProfile, board: Blackboard,
) -> list[ReviewFinding]:
    """§11. The Critic does not estimate; it attacks the estimate that exists."""
    findings: list[ReviewFinding] = []

    for row in rows:
        if row.status == "dispute":
            findings.append(ReviewFinding(
                reviewer="critic", factor=row.factor, severity="material",
                finding=(
                    f"{row.label} is disputed: {row.reason}. Neither proposal should win on "
                    f"presentation quality."
                ),
                evidence_ids=row.evidence_ids,
                suggested_correction=(
                    f"{row.owner} must produce direct evidence for {row.label.lower()} before "
                    f"the score is fixed."
                ),
                confidence=0.9,
            ))

    # "Every factor >= 4 has evidence" is a §17 validation rule; the Critic raises it first so
    # the correction can be made before validation has to fail the run.
    for factor, score in scores.items():
        if score >= 4:
            supporting = [
                item for item in board.for_factor(factor)
                if item.factor == factor and item.confidence >= 0.5
            ]
            if not supporting:
                findings.append(ReviewFinding(
                    reviewer="critic", factor=factor, severity="blocker",
                    finding=(
                        f"{FACTOR_BY_ID[factor].label} scored {score}/5 with no evidence above "
                        f"low confidence."
                    ),
                    evidence_ids=board.ids_for(factor),
                    suggested_correction=(
                        f"Cite the story, repository, or history that supports {score}/5, or "
                        f"lower the score."
                    ),
                    confidence=0.95,
                ))

    # §11: "Are calendar delays being incorrectly converted to points?"
    if scores.get("cross_team_dependency", 1) >= 4 and scores.get("integration_surface", 1) <= 2:
        findings.append(ReviewFinding(
            reviewer="critic", factor="cross_team_dependency", severity="material",
            finding=(
                "Cross-team dependency is high while the integration surface is small, which is "
                "the shape of waiting time rather than of work."
            ),
            evidence_ids=board.ids_for("cross_team_dependency"),
            suggested_correction=(
                "Points measure effort, not calendar. Track the wait as a blocker and score the "
                "coordination work only."
            ),
            confidence=0.7,
        ))
    return findings


def adversarial_review(
    scores: dict[str, int], stack: StackProfile, board: Blackboard, story_text: str,
) -> list[ReviewFinding]:
    """§12. Assume the estimate is wrong, and find the most credible under-estimate."""
    findings: list[ReviewFinding] = []
    text = story_text.lower()

    def raise_finding(
        factor: str, condition: bool, finding: str, correction: str, confidence: float,
    ) -> None:
        if condition:
            findings.append(ReviewFinding(
                reviewer="adversarial", factor=factor, severity="material", finding=finding,
                evidence_ids=board.ids_for(factor), suggested_correction=correction,
                confidence=confidence,
            ))

    raise_finding(
        "data_model_change",
        any(term in text for term in ("schema", "column", "table", "migration", "backfill"))
        and scores.get("data_model_change", 1) <= 2,
        "The story describes schema work but the data-model score treats it as incidental. "
        "Migrations carry backfill, rollback and compatibility work that is rarely visible in "
        "the story text.",
        "Re-score data model change with the migration and its reversal in scope.",
        0.8,
    )
    raise_finding(
        "reversibility",
        scores.get("data_model_change", 1) >= 3 and scores.get("reversibility", 1) <= 2,
        "A data-model change is scored without a matching reversibility cost. A migration with "
        "no rollback path is not cheap, it is unbounded.",
        "State the rollback plan, or raise reversibility to reflect that there is not one.",
        0.75,
    )
    raise_finding(
        "integration_surface",
        any(term in text for term in ("api", "webhook", "event", "queue", "downstream", "consumer"))
        and scores.get("integration_surface", 1) <= 2,
        "External consumers are mentioned but the integration surface is scored as local. "
        "Undocumented consumers are the classic source of a doubled estimate.",
        "Enumerate the consumers before fixing this score.",
        0.75,
    )
    raise_finding(
        "test_effort",
        scores.get("technical_complexity", 1) >= 4 and scores.get("test_effort", 1) <= 2,
        "High technical complexity with low test effort. Complex changes are not cheap to "
        "verify; this pairing usually means the test strategy has not been thought through.",
        "Describe the test layers this change needs, then re-score.",
        0.85,
    )
    raise_finding(
        "observability_operations",
        stack.new_observability_signal and scores.get("observability_operations", 1) <= 2,
        "A new observability signal is declared on the stack profile but operations work is "
        "scored as routine.",
        "Score the dashboard, alert and runbook work the new signal requires.",
        0.7,
    )
    raise_finding(
        "uncertainty",
        stack.maturity_level >= 4 and scores.get("uncertainty", 1) <= 2,
        f"Framework maturity is {stack.maturity_level}/5 — unfamiliar ground — while "
        f"uncertainty is scored as low. Unknown unknowns concentrate exactly here.",
        "Raise uncertainty to match the maturity of the framework being used.",
        0.8,
    )
    return findings


def optimistic_review(
    scores: dict[str, int], stack: StackProfile, board: Blackboard,
) -> list[ReviewFinding]:
    """§13. The counterweight: find complexity counted twice or already solved."""
    findings: list[ReviewFinding] = []
    for first, second, absorbed_by in _DOUBLE_COUNTED:
        if scores.get(first, 1) >= 4 and scores.get(second, 1) >= 4:
            findings.append(ReviewFinding(
                reviewer="optimistic", factor=second, severity="advisory",
                finding=(
                    f"{FACTOR_BY_ID[first].label} and {FACTOR_BY_ID[second].label} are both "
                    f"{scores[first]}/5 and {scores[second]}/5 for what is likely the same work; "
                    f"much of it is absorbed by {absorbed_by}."
                ),
                evidence_ids=board.ids_for(second),
                suggested_correction=(
                    f"Keep {FACTOR_BY_ID[first].label} where it is and consider "
                    f"{FACTOR_BY_ID[second].label} at {max(1, scores[second] - 1)} unless the "
                    f"platform work is genuinely bespoke here."
                ),
                confidence=0.6,
            ))
    if stack.maturity_level <= 2 and scores.get("technical_complexity", 1) >= 4:
        findings.append(ReviewFinding(
            reviewer="optimistic", factor="technical_complexity", severity="advisory",
            finding=(
                "Technical complexity is high on a framework the team knows well "
                f"(maturity {stack.maturity_level}/5). Familiar ground makes complex work "
                "predictable, which is not the same as easy but is cheaper than novel."
            ),
            evidence_ids=board.ids_for("technical_complexity"),
            suggested_correction="Confirm the complexity is inherent, not unfamiliarity.",
            confidence=0.55,
        ))
    return findings


# ---------------------------------------------------------------------------------------
# §15  Targeted debate
# ---------------------------------------------------------------------------------------

class DebateRound(BaseModel):
    round: int
    factor: str
    label: str
    proponent: str
    challenge: str
    resolution: str
    resolved: bool
    selected_score: int


class DebateOutcome(BaseModel):
    rounds: list[DebateRound]
    unresolved: list[str]
    escalation: Literal["NONE", "HUMAN_REVIEW"]
    #: Which factors were re-examined. §14: never rerun the whole pipeline for one disputed row.
    factors_debated: list[str]


def debate(
    rows: list[FactorAggregate],
    findings: list[ReviewFinding],
    contract: EstimationContract,
) -> tuple[dict[str, int], DebateOutcome]:
    """Re-examine only the disputed factors, for a bounded number of rounds.

    The resolution policy is deliberately blunt and published: a protected-risk factor settles
    to the higher proposal, everything else to the median. A debate that could land anywhere
    would reintroduce exactly the variance the blind scoring removed.
    """
    protected = {"uncertainty", "security_review", "regulatory_compliance", "data_model_change",
                 "reversibility", "test_effort"}
    disputed = [row for row in rows if row.status == "dispute"]
    corrections = {
        item.factor: item for item in findings
        if item.factor and item.severity in {"blocker", "material"}
    }

    resolved_scores: dict[str, int] = {row.factor: row.median_score for row in rows}
    rounds: list[DebateRound] = []
    unresolved: list[str] = []

    for row in disputed:
        settled = False
        for round_number in range(1, contract.max_debate_rounds + 1):
            finding = corrections.get(row.factor)
            if row.factor in protected:
                selected = max(row.scores)
                resolution = (
                    "Protected risk dimension: the run settles to the more conservative "
                    "proposal rather than the midpoint."
                )
                settled = True
            elif row.spread >= DISPUTE_SPREAD and round_number < contract.max_debate_rounds:
                # First round is the challenge itself; a second round is allowed to settle.
                rounds.append(DebateRound(
                    round=round_number, factor=row.factor, label=row.label,
                    proponent=row.owner,
                    challenge=(finding.finding if finding else row.reason),
                    resolution="Additional evidence requested before settling.",
                    resolved=False, selected_score=row.median_score,
                ))
                continue
            else:
                selected = row.median_score
                resolution = "No further evidence produced; the median stands."
                settled = True
            rounds.append(DebateRound(
                round=round_number, factor=row.factor, label=row.label, proponent=row.owner,
                challenge=(finding.finding if finding else row.reason),
                resolution=resolution, resolved=True, selected_score=selected,
            ))
            resolved_scores[row.factor] = selected
            break
        if not settled:
            unresolved.append(row.factor)

    # A blocker finding on an undisputed factor still has to go somewhere: it is not a scoring
    # disagreement, so it escalates rather than being silently absorbed into a median.
    blockers = [item for item in findings if item.severity == "blocker"]
    escalation = "HUMAN_REVIEW" if unresolved or blockers else "NONE"
    return resolved_scores, DebateOutcome(
        rounds=rounds, unresolved=unresolved, escalation=escalation,
        factors_debated=[row.factor for row in disputed],
    )


# ---------------------------------------------------------------------------------------
# §17  Deterministic Validation Engine
# ---------------------------------------------------------------------------------------

class ValidationRule(BaseModel):
    rule: str
    passed: bool
    detail: str


class ValidationResult(BaseModel):
    passed: bool
    rules: list[ValidationRule]

    def failures(self) -> list[ValidationRule]:
        return [item for item in self.rules if not item.passed]


def validate(
    scores: dict[str, int],
    stack: StackProfile,
    board: Blackboard,
    calculation: Calculation,
) -> ValidationResult:
    """The objective rules, enforced in code (§17).

    AI handles semantic interpretation; this function handles what is decidable. Every rule
    reports whether it fired, so a passing run is as auditable as a failing one.
    """
    rules: list[ValidationRule] = []

    def rule(name: str, passed: bool, detail: str) -> None:
        rules.append(ValidationRule(rule=name, passed=passed, detail=detail))

    rule(
        "exactly 16 factors", len(scores) == len(FACTORS),
        f"{len(scores)} of {len(FACTORS)} factors scored",
    )
    out_of_range = {
        factor: value for factor, value in scores.items()
        if not isinstance(value, int) or not 1 <= value <= 5
    }
    rule(
        "each score is an integer 1..5", not out_of_range,
        "all scores in range" if not out_of_range else f"out of range: {sorted(out_of_range)}",
    )
    unevidenced = [
        factor for factor, value in scores.items()
        if value >= 4 and not any(
            item.factor == factor and item.confidence >= 0.5 for item in board.records
        )
    ]
    rule(
        "every factor >= 4 has evidence", not unevidenced,
        "elevated scores are all evidenced" if not unevidenced
        else f"unevidenced: {[FACTOR_BY_ID[f].label for f in unevidenced]}",
    )
    rule(
        "framework maturity supplied", 1 <= stack.maturity_level <= 5,
        f"maturity level {stack.maturity_level}",
    )
    rule(
        "team experience supplied", 1 <= stack.team_experience <= 5,
        f"team experience {stack.team_experience}",
    )
    stack_steps = [step for step in calculation.steps if step.rule.startswith("§8.2")]
    rule(
        "stack penalties validated", all(step.delta >= 0 for step in stack_steps),
        f"{sum(step.applied for step in stack_steps)} of {len(stack_steps)} stack rules applied",
    )
    applied = [step.rule for step in calculation.steps if step.applied]
    rule(
        "no duplicate penalty", len(applied) == len(set(applied)),
        "each adjustment applied at most once" if len(applied) == len(set(applied))
        else "a rule fired more than once",
    )
    # The applied deltas must accumulate to the adjusted score, or the scorecard cannot be
    # replayed by hand — which is the product's entire claim. `base_sum` is itself one of the
    # steps, so it is summed here rather than added to the total a second time.
    accumulated = sum(step.delta for step in calculation.steps if step.applied)
    reconciles = accumulated == calculation.adjusted_score
    rule(
        "final output schema valid", reconciles,
        f"applied steps accumulate to {accumulated} = adjusted score"
        if reconciles
        else f"applied steps accumulate to {accumulated}, adjusted score is "
             f"{calculation.adjusted_score}",
    )
    gate = spike_gate(scores, stack)
    rule("spike rules checked", True, gate.summary)
    rule(
        "decomposition rules checked", True,
        f"{calculation.points} points → {'decomposition advised' if calculation.points >= 13 else 'no decomposition rule fired'}",
    )
    return ValidationResult(passed=all(item.passed for item in rules), rules=rules)


# ---------------------------------------------------------------------------------------
# §20  Spike Gate
# ---------------------------------------------------------------------------------------

class SpikeGate(BaseModel):
    decision: Literal["PROCEED", "SPIKE", "SPIKE_OR_PAIR", "DECOMPOSE_OR_SPIKE"]
    triggered: list[str]
    summary: str


def spike_gate(scores: dict[str, int], stack: StackProfile) -> SpikeGate:
    """§20. The system must be able to say "do not estimate — spike first".

    A pipeline that always returns a number is less reliable than one that refuses, because a
    manufactured number is indistinguishable from an informed one once it reaches a board.
    """
    triggered: list[str] = []
    decision: str = "PROCEED"

    if scores.get("uncertainty", 1) == 5:
        triggered.append("Uncertainty is 5/5")
        decision = "SPIKE"
    if stack.maturity_level == 5:
        triggered.append("Framework maturity is 5/5 — the team has not used this before")
        decision = "SPIKE"
    if stack.team_experience <= 2 and scores.get("technical_complexity", 1) >= 4:
        triggered.append("Low team experience against high technical complexity")
        decision = "SPIKE" if decision == "SPIKE" else "SPIKE_OR_PAIR"
    maxed = [FACTOR_BY_ID[f].label for f, value in scores.items() if value == 5]
    if len(maxed) >= 2:
        triggered.append(f"Two or more factors at 5/5: {', '.join(sorted(maxed))}")
        decision = "DECOMPOSE_OR_SPIKE" if decision == "PROCEED" else decision
    if scores.get("test_effort", 1) == 5:
        triggered.append("No viable testing strategy is evidenced (test effort 5/5)")
        decision = "SPIKE"
    if scores.get("dod_overhead", 1) == 5 or scores.get("observability_operations", 1) == 5:
        triggered.append("Deployment or operational viability is unknown")
        decision = "SPIKE"

    summary = (
        "No spike rule fired." if not triggered
        else f"{len(triggered)} spike rule(s) fired → {decision.replace('_', ' ').lower()}"
    )
    return SpikeGate(decision=decision, triggered=triggered, summary=summary)


# ---------------------------------------------------------------------------------------
# §10  Reference Story Comparator
# ---------------------------------------------------------------------------------------

class ReferenceMatch(BaseModel):
    id: str
    title: str
    points: int
    similarity: float
    #: Which similarity components carried the match, so a reader can judge whether it is a
    #: real analogue or a coincidence of vocabulary.
    components: dict[str, float]
    differences: list[str]


class ReferenceComparison(BaseModel):
    matches: list[ReferenceMatch]
    closest: ReferenceMatch | None
    relative_assessment: Literal["smaller", "similar", "larger", "unknown"]
    implied_range: dict[str, int] | None
    anchors: dict[str, int]
    note: str


_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "when", "should", "must", "will",
         "add", "user", "story", "into", "have", "are", "was", "not", "但", "als"}


def _tokens(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _STOP}


def compare_references(
    story: Any,
    scores: dict[str, int],
    points: int,
    history: list[dict[str, Any]],
    limit: int = 3,
) -> ReferenceComparison:
    """Find historically similar stories and derive an implied range (§10).

    Similarity is three signals, not one. Semantic overlap alone matches stories that merely
    share vocabulary; structural similarity (the factor vector) matches stories that were the
    same *shape* of work; stack similarity keeps a React story from anchoring a database one.
    All three are reported so a weak match is visibly weak.
    """
    current_tokens = _tokens(
        " ".join([story.title, story.user_story, *story.acceptance_criteria])
    )
    current_vector = [scores.get(item.id, 3) for item in FACTORS]
    stack = story.stack

    matches: list[ReferenceMatch] = []
    for record in history:
        result = record.get("result") or {}
        record_scores = {
            item.get("factor"): int(item.get("score", 3))
            for item in (result.get("scorecard") or [])
            if item.get("factor")
        }
        if not record_scores:
            continue
        semantic = 0.0
        record_tokens = _tokens(str(record.get("title", "")) + " " + str(record.get("tldr", "")))
        if current_tokens and record_tokens:
            semantic = len(current_tokens & record_tokens) / len(current_tokens | record_tokens)
        record_vector = [record_scores.get(item.id, 3) for item in FACTORS]
        distance = sum(abs(a - b) for a, b in zip(current_vector, record_vector, strict=True))
        # 16 factors, at most 4 apart each: 64 is the maximum possible distance.
        structural = 1.0 - (distance / 64)
        stack_match = sum((
            record.get("frontend") == stack.frontend,
            record.get("backend") == stack.backend,
        )) / 2
        similarity = round(0.4 * semantic + 0.45 * structural + 0.15 * stack_match, 4)

        differences = []
        for definition in FACTORS:
            delta = current_vector[definition.number - 1] - record_scores.get(definition.id, 3)
            if abs(delta) >= 2:
                differences.append(
                    f"{definition.label} is {abs(delta)} point(s) "
                    f"{'higher' if delta > 0 else 'lower'} here"
                )
        matches.append(ReferenceMatch(
            id=str(record.get("id", "")), title=str(record.get("title", "")),
            points=int(record.get("points", 0)), similarity=similarity,
            components={
                "semantic": round(semantic, 4), "structural": round(structural, 4),
                "stack": round(stack_match, 4),
            },
            differences=differences[:4],
        ))

    matches.sort(key=lambda item: item.similarity, reverse=True)
    top = matches[:limit]
    closest = top[0] if top else None

    anchors = {str(item["points"]): int(item["points"]) for item in stack.anchors()}
    if closest is None:
        return ReferenceComparison(
            matches=[], closest=None, relative_assessment="unknown", implied_range=None,
            anchors=anchors,
            note=(
                "No comparable estimate exists in history yet. The first estimates for a stack "
                "carry no anchor, which is a reason to review them, not to trust them more."
            ),
        )
    if points > closest.points:
        relative = "larger"
    elif points < closest.points:
        relative = "smaller"
    else:
        relative = "similar"
    neighbours = sorted({item.points for item in top} | {points})
    return ReferenceComparison(
        matches=top, closest=closest, relative_assessment=relative,
        implied_range={
            "lower": neighbours[0], "likely": closest.points, "upper": neighbours[-1],
        },
        anchors=anchors,
        note=(
            f"Closest historical story is {closest.similarity:.0%} similar at "
            f"{closest.points} points; this story reads as {relative}."
            if closest.similarity >= 0.5
            else (
                f"The nearest historical story is only {closest.similarity:.0%} similar, which "
                f"is too weak to anchor against. Treat the range as indicative."
            )
        ),
    )


# ---------------------------------------------------------------------------------------
# §22  Estimation Snapshot   /   §29  Failure attribution
# ---------------------------------------------------------------------------------------

class EstimationSnapshot(BaseModel):
    """Everything needed to explain why two runs of the same story differed (§22)."""

    story_version: int
    input_hash: str
    repository_commit: str | None
    contract_hash: str
    estimation_framework_version: str = FRAMEWORK_VERSION
    factor_rubric_version: str = RUBRIC_VERSION
    agent_graph_version: str = EAGLE_VERSION
    reference_dataset_size: int = 0
    team_profile: str = ""
    models: dict[str, str] = Field(default_factory=dict)
    estimator_count: int = 0
    created_at: str = Field(default_factory=_now)


def build_snapshot(
    contract: EstimationContract,
    input_hash: str,
    stack: StackProfile,
    model_name: str,
    estimator_count: int,
    reference_dataset_size: int,
) -> EstimationSnapshot:
    return EstimationSnapshot(
        story_version=contract.contract_version,
        input_hash=input_hash,
        repository_commit=contract.repository_commit,
        contract_hash=contract.contract_hash,
        reference_dataset_size=reference_dataset_size,
        team_profile=(
            f"maturity-{stack.maturity_level}/experience-{stack.team_experience}/"
            f"{stack.scenario}"
        ),
        models={"estimator": model_name, "reviewer": model_name, "judge": "deterministic"},
        estimator_count=estimator_count,
    )


class FailureAttribution(BaseModel):
    layer: str
    detail: str
    remedy: str


def attribute_failure(
    validation: ValidationResult,
    debate_outcome: DebateOutcome,
    board: Blackboard,
    estimator_count: int,
) -> list[FailureAttribution]:
    """Classify what went wrong by architectural layer (§29).

    The point of the taxonomy is to stop the reflex of answering every failure with a longer
    prompt or another retry. A retrieval failure and a judge failure need opposite responses.
    """
    problems: list[FailureAttribution] = []
    for failure in validation.failures():
        if failure.rule.startswith(("exactly", "each score", "final output")):
            layer, remedy = "rule_engine", "Fix the scorecard contract, not the prompt."
        elif failure.rule.startswith("every factor"):
            layer, remedy = "retrieval", "Retrieve evidence for the elevated factor, or lower it."
        else:
            layer, remedy = "task_contract", "Supply the missing contract input."
        problems.append(FailureAttribution(layer=layer, detail=failure.detail, remedy=remedy))

    if debate_outcome.unresolved:
        problems.append(FailureAttribution(
            layer="judge",
            detail=f"{len(debate_outcome.unresolved)} factor(s) survived a bounded debate.",
            remedy="Escalate to a human specialist; more rounds will not converge.",
        ))
    heuristic = sum(item.source_type == "heuristic" for item in board.records)
    if heuristic > len(FACTORS) // 2:
        problems.append(FailureAttribution(
            layer="context",
            detail=f"{heuristic} of {len(FACTORS)} factors were filled from keyword heuristics.",
            remedy="The story is too thin to estimate. Clarify it rather than re-running.",
        ))
    if estimator_count < 2:
        problems.append(FailureAttribution(
            layer="reviewer",
            detail="Only one independent assessment backed this estimate.",
            remedy="Run the blind reviewer; a single pass cannot detect its own anchoring.",
        ))
    return problems
