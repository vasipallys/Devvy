"""Controlled agentic estimation stages around the deterministic v2 calculator.

The local runtime is intentionally serialized and CPU-bound.  This module therefore uses
two independent model assessments (primary and blind reviewer) and implements routing,
comparison, criticism, arbitration, and consistency checks as deterministic controls.
It never calculates story points in a prompt and never persists hidden chain-of-thought.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.estimation_framework import (
    FACTOR_BY_ID,
    Calculation,
    FactorScore,
    StackProfile,
    calculate,
    decide,
    policy_checks,
)

PIPELINE_VERSION = "agentic-local-1.0"
PROTECTED_FACTORS = {
    "uncertainty",
    "security_review",
    "regulatory_compliance",
    "data_model_change",
    "reversibility",
    "test_effort",
}


class ReadinessCheck(BaseModel):
    area: str
    status: Literal["ready", "assumption", "blocked"]
    detail: str
    evidence_ids: list[str] = Field(default_factory=list)
    question: str | None = None


class ReadinessResult(BaseModel):
    decision: Literal[
        "ESTIMATE", "ESTIMATE_WITH_ASSUMPTIONS", "SPIKE_REQUIRED", "DECOMPOSE_REQUIRED"
    ]
    checks: list[ReadinessCheck]
    assumptions: list[str]
    targeted_questions: list[str]


class SpecialistRoute(BaseModel):
    role: str
    label: str
    reason: str
    dimensions: list[str]
    mandatory: bool = True


class SpecialistFinding(BaseModel):
    role: str
    label: str
    summary: str
    assessed_dimensions: list[str]
    evidence_ids: list[str]
    material_risks: list[str]
    open_questions: list[str]


class DimensionAssessment(BaseModel):
    factor: str
    label: str
    score_min: int = Field(ge=1, le=5)
    score_most_likely: int = Field(ge=1, le=5)
    score_max: int = Field(ge=1, le=5)
    rationale: str
    evidence_ids: list[str]
    assumptions: list[str]
    included_lifecycle_work: list[str]
    why_not_lower: str
    why_not_higher: str
    confidence: Literal["High", "Medium", "Low"]
    provenance: Literal["model", "heuristic"]


class AgentAssessment(BaseModel):
    role: Literal["PRIMARY_ESTIMATOR", "BLIND_REVIEWER"]
    blind: bool
    dimensions: list[DimensionAssessment]
    point_cross_check: int
    recommendation_cross_check: str
    model_scored: int
    heuristic_filled: int


class Disagreement(BaseModel):
    factor: str
    label: str
    primary_score: int
    reviewer_score: int
    delta: int
    material: bool
    protected: bool
    point_boundary_impact: bool
    reasons: list[str]


class CriticChallenge(BaseModel):
    factor: str
    severity: Literal["material", "advisory"]
    challenge: str
    evidence_needed: str
    possible_impact: str


class ArbitrationDecision(BaseModel):
    factor: str
    primary_score: int
    reviewer_score: int
    selected_score: int
    policy: str
    rationale: str
    human_approval_required: bool


def _story_text(story: Any) -> str:
    return " ".join(
        [
            story.title,
            story.user_story,
            " ".join(story.acceptance_criteria),
            story.technical_breakdown or "",
            " ".join(story.labels),
            " ".join(story.components),
        ]
    ).lower()


def canonical_story(story: Any) -> dict[str, Any]:
    """Create the stable input shared by both independent model passes."""
    evidence = {
        "EV-TITLE": story.title,
        "EV-DESCRIPTION": story.user_story,
        **{
            f"EV-AC-{index + 1}": criterion
            for index, criterion in enumerate(story.acceptance_criteria)
        },
    }
    if story.technical_breakdown:
        evidence["EV-TECHNICAL"] = story.technical_breakdown
    canonical = {
        "title": story.title.strip(),
        "description": story.user_story.strip(),
        "acceptance_criteria": [item.strip() for item in story.acceptance_criteria],
        "technical_breakdown": (story.technical_breakdown or "").strip(),
        "labels": sorted(set(story.labels)),
        "components": sorted(set(story.components)),
        "source": story.source,
        "stack": story.stack.model_dump(mode="json"),
        "evidence": evidence,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return {
        **canonical,
        "input_hash": "sha256:" + hashlib.sha256(encoded.encode()).hexdigest(),
        "missing_fields": [
            label
            for label, missing in (
                ("description", not story.user_story.strip()),
                ("acceptance_criteria", not story.acceptance_criteria),
                ("technical_breakdown", not story.technical_breakdown),
            )
            if missing
        ],
        "untrusted_instructions_detected": bool(
            re.search(r"ignore (all|the|previous)|system prompt|developer message", _story_text(story))
        ),
    }


def evaluate_readiness(story: Any, canonical: dict[str, Any]) -> ReadinessResult:
    text = _story_text(story)
    checks: list[ReadinessCheck] = []

    def check(
        area: str,
        ready: bool,
        detail: str,
        question: str,
        evidence_ids: list[str],
        blocked: bool = False,
    ) -> None:
        checks.append(
            ReadinessCheck(
                area=area,
                status="ready" if ready else "blocked" if blocked else "assumption",
                detail=detail,
                question=None if ready else question,
                evidence_ids=evidence_ids,
            )
        )

    check(
        "Product outcome",
        bool(story.user_story.strip()),
        "The intended outcome is described." if story.user_story.strip() else "Outcome is absent.",
        "What user or business outcome must change?",
        ["EV-DESCRIPTION"],
    )
    check(
        "Acceptance criteria",
        bool(story.acceptance_criteria),
        (
            f"{len(story.acceptance_criteria)} testable criterion/criteria supplied."
            if story.acceptance_criteria
            else "No acceptance criteria were supplied."
        ),
        "Which observable success, failure, and permission cases must pass?",
        [key for key in canonical["evidence"] if key.startswith("EV-AC-")],
    )
    check(
        "Technical feasibility",
        bool(story.technical_breakdown) or len(text) >= 140,
        "Implementation context is present." if story.technical_breakdown else "Technical path is inferred.",
        "Which components, integrations, and data changes are expected?",
        ["EV-TECHNICAL"] if story.technical_breakdown else ["EV-DESCRIPTION"],
    )
    check(
        "Security and data",
        not any(term in text for term in ("auth", "pii", "payment", "biometric"))
        or any(term in text for term in ("security", "permission", "audit", "encrypt")),
        "No unexplained protected-data boundary was detected.",
        "What authorization, sensitive-data, audit, and threat-model controls apply?",
        ["EV-DESCRIPTION"],
    )
    check(
        "Testing and deployment",
        any(term in text for term in ("test", "deploy", "release", "rollout", "acceptance")),
        "Delivery evidence mentions verification or release work.",
        "What test layers, rollout verification, and rollback evidence are required?",
        ["EV-DESCRIPTION", *[key for key in canonical["evidence"] if key.startswith("EV-AC-")]],
    )
    blocked = [item for item in checks if item.status == "blocked"]
    gaps = [item for item in checks if item.status == "assumption"]
    sparse = len(text.strip()) < 80 and not story.acceptance_criteria
    if sparse or len(blocked) >= 2:
        decision = "SPIKE_REQUIRED"
    elif story.stack.scenario == "framework_migration":
        decision = "DECOMPOSE_REQUIRED"
    elif gaps:
        decision = "ESTIMATE_WITH_ASSUMPTIONS"
    else:
        decision = "ESTIMATE"
    return ReadinessResult(
        decision=decision,
        checks=checks,
        assumptions=[item.detail for item in gaps],
        targeted_questions=[item.question for item in gaps if item.question],
    )


_ROUTES = (
    ("PRODUCT_DOMAIN", "Product / domain", ("requirements_clarity",), ("story", "criteria", "user")),
    ("ARCHITECTURE", "Architecture", ("technical_complexity", "integration_surface", "reversibility"), ("architect", "integration", "api", "kafka", "migration")),
    ("FRONTEND", "Frontend", ("frontend_effort",), ("react", "angular", "ui", "screen", "accessib")),
    ("BACKEND", "Backend", ("backend_effort", "integration_surface"), ("backend", "service", "endpoint", "api", "spring", "fastapi", "flask")),
    ("DATA_MIGRATION", "Data / migration", ("data_model_change", "reversibility"), ("schema", "database", "migration", "backfill", "table")),
    ("TEST_QUALITY", "Test / quality", ("test_effort",), ("test", "acceptance", "qa", "regression")),
    ("SECURITY_COMPLIANCE", "Security / compliance", ("security_review", "regulatory_compliance"), ("auth", "security", "pii", "payment", "audit", "compliance")),
    ("DEVOPS_SRE", "DevOps / SRE", ("observability_operations", "dod_overhead"), ("deploy", "release", "metric", "alert", "terraform", "helm")),
    ("DEPENDENCY_RISK", "Dependency / delivery risk", ("cross_team_dependency", "uncertainty"), ("vendor", "other team", "dependency", "external", "unknown")),
)


def route_specialists(story: Any) -> tuple[str, list[SpecialistRoute]]:
    text = _story_text(story)
    routes = []
    for role, label, dimensions, triggers in _ROUTES:
        matched = [trigger for trigger in triggers if trigger in text]
        mandatory = role in {"PRODUCT_DOMAIN", "TEST_QUALITY"} or bool(matched)
        if mandatory:
            routes.append(
                SpecialistRoute(
                    role=role,
                    label=label,
                    reason=(
                        "Mandatory whole-lifecycle coverage"
                        if not matched
                        else "Matched: " + ", ".join(matched[:4])
                    ),
                    dimensions=list(dimensions),
                )
            )
    high_risk = any(
        route.role in {"SECURITY_COMPLIANCE", "DATA_MIGRATION", "DEPENDENCY_RISK"}
        for route in routes
    ) or story.stack.scenario != "standard"
    mode = "HIGH_RISK" if high_risk else "STANDARD" if len(routes) > 3 else "COMPACT"
    return mode, routes[:8]


_LIFECYCLE = {
    "scope": ["scope confirmation", "acceptance examples"],
    "delivery": ["implementation", "code review", "integration verification"],
    "assurance": ["test evidence", "security/quality review", "release verification"],
    "risk": ["assumption validation", "dependency coordination", "rollback planning"],
}


def assessment(
    role: Literal["PRIMARY_ESTIMATOR", "BLIND_REVIEWER"],
    scorecard: list[FactorScore],
    story: Any,
) -> AgentAssessment:
    scores = {item.factor: int(item.score) for item in scorecard}
    calculation = calculate(scores, story.stack)
    checks = policy_checks(scores, story.stack, calculation)
    recommendation, _ = decide(checks, story.stack, calculation, scores)
    evidence_ids = ["EV-DESCRIPTION"] + [
        f"EV-AC-{index + 1}" for index, _ in enumerate(story.acceptance_criteria)
    ]
    dimensions = []
    for item in scorecard:
        spread = 1 if item.provenance == "heuristic" or item.factor == "uncertainty" else 0
        dimensions.append(
            DimensionAssessment(
                factor=item.factor,
                label=item.label,
                score_min=max(1, item.score - spread),
                score_most_likely=item.score,
                score_max=min(5, item.score + spread),
                rationale=item.reason,
                evidence_ids=evidence_ids[:4],
                assumptions=([] if item.provenance == "model" else ["Score inferred from supplied text"]),
                included_lifecycle_work=_LIFECYCLE[item.group],
                why_not_lower=(
                    f"The supplied evidence supports {item.reason.rstrip('.').lower()}; "
                    f"a lower score would require a more bounded pattern."
                ),
                why_not_higher=(
                    "This is already the maximum score and requires refinement or a spike."
                    if item.score == 5
                    else f"The story does not evidence the factor's extreme anchor: "
                    f"{FACTOR_BY_ID[item.factor].high_anchor}"
                ),
                confidence="Low" if spread else "Medium" if item.score >= 4 else "High",
                provenance=item.provenance,
            )
        )
    return AgentAssessment(
        role=role,
        blind=role == "BLIND_REVIEWER",
        dimensions=dimensions,
        point_cross_check=calculation.points,
        recommendation_cross_check=recommendation,
        model_scored=sum(item.provenance == "model" for item in scorecard),
        heuristic_filled=sum(item.provenance == "heuristic" for item in scorecard),
    )


def specialist_analysis(
    routes: list[SpecialistRoute], primary: AgentAssessment
) -> list[SpecialistFinding]:
    """Project the scored evidence through each routed specialist lens.

    This is deliberately a deterministic projection rather than extra pretend model agents. It
    keeps the local CPU workflow bounded while making specialist ownership and evidence visible.
    """
    by_factor = {item.factor: item for item in primary.dimensions}
    findings = []
    for route in routes:
        dimensions = [by_factor[item] for item in route.dimensions if item in by_factor]
        if not dimensions:
            continue
        elevated = [item for item in dimensions if item.score_most_likely >= 4]
        inferred = [item for item in dimensions if item.provenance == "heuristic"]
        findings.append(
            SpecialistFinding(
                role=route.role,
                label=route.label,
                summary=(
                    f"Reviewed {len(dimensions)} owned dimension(s); "
                    f"{len(elevated)} elevated and {len(inferred)} inferred from sparse evidence."
                ),
                assessed_dimensions=[item.factor for item in dimensions],
                evidence_ids=sorted(
                    {evidence_id for item in dimensions for evidence_id in item.evidence_ids}
                ),
                material_risks=[
                    f"{item.label} is {item.score_most_likely}/5: {item.rationale}"
                    for item in elevated
                ],
                open_questions=[
                    f"Provide direct evidence to replace the inferred {item.label.lower()} score."
                    for item in inferred
                ],
            )
        )
    return findings


def compare_assessments(
    primary: AgentAssessment,
    reviewer: AgentAssessment,
) -> list[Disagreement]:
    reviewer_by_factor = {item.factor: item for item in reviewer.dimensions}
    boundary = primary.point_cross_check != reviewer.point_cross_check
    result = []
    for item in primary.dimensions:
        other = reviewer_by_factor[item.factor]
        delta = abs(item.score_most_likely - other.score_most_likely)
        protected = item.factor in PROTECTED_FACTORS
        material = delta >= 2 or (
            protected and delta >= 1 and max(item.score_most_likely, other.score_most_likely) >= 4
        )
        reasons = []
        if delta >= 2:
            reasons.append("score delta is at least 2")
        if protected and material:
            reasons.append("protected risk dimension")
        if boundary:
            reasons.append("independent proposals cross a Fibonacci boundary")
        if material or delta:
            result.append(
                Disagreement(
                    factor=item.factor,
                    label=item.label,
                    primary_score=item.score_most_likely,
                    reviewer_score=other.score_most_likely,
                    delta=delta,
                    material=material,
                    protected=protected,
                    point_boundary_impact=boundary,
                    reasons=reasons or ["minor score variance"],
                )
            )
    return result


def criticize(disagreements: list[Disagreement]) -> list[CriticChallenge]:
    challenges = []
    for item in disagreements:
        if not item.material:
            continue
        challenges.append(
            CriticChallenge(
                factor=item.factor,
                severity="material",
                challenge=(
                    f"Primary proposed {item.primary_score} while the blind reviewer proposed "
                    f"{item.reviewer_score}; neither value should win by presentation quality."
                ),
                evidence_needed=(
                    f"Provide direct story, contract, repository, or team-reference evidence for "
                    f"{item.label.lower()}."
                ),
                possible_impact=(
                    "May activate a protected floor or human approval."
                    if item.protected
                    else "May change the Fibonacci band."
                ),
            )
        )
    return challenges


def arbitrate(
    primary: AgentAssessment,
    reviewer: AgentAssessment,
    disagreements: list[Disagreement],
) -> tuple[dict[str, dict[str, Any]], list[ArbitrationDecision]]:
    reviewer_by_factor = {item.factor: item for item in reviewer.dimensions}
    disagreement_by_factor = {item.factor: item for item in disagreements}
    scores: dict[str, dict[str, Any]] = {}
    decisions = []
    for item in primary.dimensions:
        other = reviewer_by_factor[item.factor]
        disagreement = disagreement_by_factor.get(item.factor)
        if disagreement and disagreement.material and disagreement.protected:
            selected = max(item.score_most_likely, other.score_most_likely)
            policy = "conservative protected-risk arbitration"
        elif disagreement and disagreement.material:
            selected = (item.score_most_likely + other.score_most_likely + 1) // 2
            policy = "rounded independent-proposal midpoint"
        else:
            selected = item.score_most_likely
            policy = "primary retained within agreement threshold"
        scores[item.factor] = {
            "score": selected,
            "why": (
                f"Primary {item.score_most_likely}; blind reviewer "
                f"{other.score_most_likely}. {policy}."
            ),
        }
        decisions.append(
            ArbitrationDecision(
                factor=item.factor,
                primary_score=item.score_most_likely,
                reviewer_score=other.score_most_likely,
                selected_score=selected,
                policy=policy,
                rationale=scores[item.factor]["why"],
                human_approval_required=bool(
                    disagreement and disagreement.material and disagreement.protected
                ),
            )
        )
    return scores, decisions


def consistency_audit(
    primary: AgentAssessment,
    reviewer: AgentAssessment,
    disagreements: list[Disagreement],
    final_calculation: Calculation,
    final_scores: dict[str, int],
    stack: StackProfile,
    blind_review_executed: bool = True,
) -> dict[str, Any]:
    deltas = [
        abs(left.score_most_likely - right.score_most_likely)
        for left, right in zip(primary.dimensions, reviewer.dimensions, strict=True)
    ]
    stable = sum(delta <= 1 for delta in deltas) / len(deltas)
    replay = calculate(final_scores, stack)
    material = [item for item in disagreements if item.material]
    protected = [item for item in material if item.protected]
    status = (
        "HUMAN_REVIEW_REQUIRED"
        if protected
        else "PASS_WITH_WARNINGS" if material or stable < 0.9 else "PASS"
    )
    if not blind_review_executed:
        # The reviewer mirrors the primary when the second pass did not run, so a perfect
        # stability index would be an artefact of that mirroring rather than evidence of
        # two assessments agreeing. Say so instead of reporting agreement.
        status = "PASS_WITH_WARNINGS" if status == "PASS" else status
    return {
        "status": status,
        "blind_review_executed": blind_review_executed,
        "dimension_stability_index": round(stable, 4) if blind_review_executed else None,
        "point_stability": {
            "primary": primary.point_cross_check,
            "reviewer": reviewer.point_cross_check,
            "final": final_calculation.points,
            "same_boundary": primary.point_cross_check == reviewer.point_cross_check,
        },
        "risk_floor_stable": not protected,
        "material_disagreements": len(material),
        "protected_disagreements": len(protected),
        "calculation_replay_passed": replay.model_dump() == final_calculation.model_dump(),
        "warnings": [
            message
            for condition, message in (
                (stable < 0.9, "Independent dimension agreement is below 90%."),
                (bool(material), "Material disagreements required deterministic arbitration."),
                (bool(protected), "Protected disagreements require human specialist approval."),
                (
                    not blind_review_executed,
                    "An independent second pass was not run for this story, so cross-assessment "
                    "stability is not measured.",
                ),
            )
            if condition
        ],
    }
