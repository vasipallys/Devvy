"""EAGLE governance layer.

These tests pin the rules the architecture states in prose, because the reproducibility claim
("same story + same snapshot + same harness = same estimate") is only worth anything if the
rules are enforced somewhere a change would break a test.
"""

from __future__ import annotations

import os

os.environ["PHOENIX_ENABLED"] = "false"

import pytest

from backend.eagle import (
    DISPUTE_SPREAD,
    EAGLE_VERSION,
    Blackboard,
    Evidence,
    aggregate,
    adversarial_review,
    attribute_failure,
    build_blackboard,
    build_contract,
    build_snapshot,
    compare_references,
    critic_review,
    debate,
    median_scores,
    optimistic_review,
    spike_gate,
    validate,
)
from backend.estimate_code import Story
from backend.estimation_framework import FACTORS, StackProfile, calculate


def make_story(**overrides):
    data = {
        "title": "Add customer risk classification",
        "user_story": "As a risk officer I need customers classified so exposure is visible.",
        "acceptance_criteria": [
            "Risk category is persisted",
            "Audit events are generated",
            "Existing journeys remain backward compatible",
        ],
        "stack": StackProfile(frontend="react", backend="spring_boot", database="mariadb"),
    }
    data.update(overrides)
    return Story(**data)


def flat(score: int = 2) -> dict[str, int]:
    return {item.id: score for item in FACTORS}


def board_for(scores: dict[str, int], confidence: float = 0.85) -> Blackboard:
    board = Blackboard()
    for factor in scores:
        board.add(Evidence(
            evidence_id=f"EV-{factor}", factor=factor, claim="evidence",
            source_type="story", source="story.description", confidence=confidence,
        ))
    return board


# -- §2 Estimation Contract -------------------------------------------------------------

def test_contract_is_frozen_and_hashed():
    contract = build_contract(make_story())
    assert contract.contract_hash.startswith("sha256:")
    with pytest.raises(Exception):
        contract.title = "changed"


def test_contract_hash_is_stable_for_the_same_story():
    assert build_contract(make_story()).contract_hash == build_contract(make_story()).contract_hash


def test_contract_hash_changes_with_the_story():
    other = make_story(title="Something else entirely")
    assert build_contract(make_story()).contract_hash != build_contract(other).contract_hash


def test_contract_requires_code_evidence_only_when_a_commit_is_supplied():
    assert not build_contract(make_story()).completion.require_code_evidence
    assert build_contract(make_story(), repository_commit="abc123").completion.require_code_evidence


# -- §5 Evidence Blackboard -------------------------------------------------------------

def sparse_scorecard(story):
    """A draft carrying one real signal; the other fifteen factors fall to heuristics."""
    from backend.estimate_code import EstimateDraft, build_scorecard

    draft = EstimateDraft(factors=[
        {"factor": "technical_complexity", "score": 4, "reason": "Two services change together"},
    ])
    return build_scorecard(draft, story)


def test_blackboard_records_every_acceptance_criterion_and_factor():
    story = make_story()
    board = build_blackboard(story, sparse_scorecard(story))
    assert len([item for item in board.records if item.source_type == "acceptance_criteria"]) == 3
    assert all(board.for_factor(item.id) for item in FACTORS)


def test_heuristic_evidence_is_recorded_at_low_confidence():
    """A guess must never be presented at the same confidence as something read from the story."""
    story = make_story()
    board = build_blackboard(story, sparse_scorecard(story))
    heuristic = [item for item in board.records if item.source_type == "heuristic"]
    assert heuristic, "the sparse draft should have produced heuristic fills"
    assert all(item.confidence < 0.5 for item in heuristic)
    scored = [item for item in board.records if item.factor == "technical_complexity"]
    assert all(item.confidence >= 0.5 for item in scored)


# -- §9 Median aggregation --------------------------------------------------------------

def test_median_of_three_estimators_is_the_middle_value():
    assert median_scores([{"a": 2}, {"a": 4}, {"a": 4}])["a"] == 4
    assert median_scores([{"a": 2}, {"a": 3}, {"a": 4}])["a"] == 3


def test_median_of_an_even_count_rounds_up_not_towards_optimism():
    assert median_scores([{"a": 3}, {"a": 4}])["a"] == 4


def test_median_ignores_factors_an_estimator_did_not_score():
    assert median_scores([{"a": 5}, {"b": 1}]) == {"a": 5, "b": 1}


# -- §14 Conflict detection -------------------------------------------------------------

def test_spread_of_zero_is_accepted():
    scores = flat(3)
    _, rows = aggregate([scores, scores], board_for(scores))
    assert {row.status for row in rows} == {"accept"}


def test_spread_of_one_accepts_the_median():
    first, second = flat(3), flat(3)
    second["technical_complexity"] = 4
    _, rows = aggregate([first, second], board_for(first))
    row = next(item for item in rows if item.factor == "technical_complexity")
    assert row.status == "accept_median"
    assert row.spread == 1


def test_spread_of_two_is_a_dispute():
    first, second = flat(2), flat(2)
    second["technical_complexity"] = 4
    _, rows = aggregate([first, second], board_for(first))
    row = next(item for item in rows if item.factor == "technical_complexity")
    assert row.spread == DISPUTE_SPREAD
    assert row.status == "dispute"


def test_an_elevated_score_without_evidence_is_disputed_even_when_estimators_agree():
    """§6: missing information must never resolve quietly to a number."""
    scores = flat(2)
    scores["security_review"] = 5
    _, rows = aggregate([scores, scores], board_for(scores, confidence=0.2))
    row = next(item for item in rows if item.factor == "security_review")
    assert row.spread == 0
    assert row.status == "dispute"


def test_every_factor_carries_its_owning_specialist():
    scores = flat(3)
    _, rows = aggregate([scores], board_for(scores))
    assert all(row.owner for row in rows)
    assert next(r for r in rows if r.factor == "security_review").owner == "Security Agent"


# -- §11 / §12 / §13 Reviewers ----------------------------------------------------------

def test_critic_blocks_an_elevated_score_with_no_evidence():
    scores = flat(2)
    scores["test_effort"] = 5
    board = board_for(scores, confidence=0.1)
    _, rows = aggregate([scores, scores], board)
    findings = critic_review(rows, scores, StackProfile(), board)
    assert any(item.severity == "blocker" and item.factor == "test_effort" for item in findings)


def test_critic_flags_calendar_time_dressed_up_as_points():
    scores = flat(2)
    scores["cross_team_dependency"] = 4
    scores["integration_surface"] = 1
    board = board_for(scores)
    _, rows = aggregate([scores, scores], board)
    findings = critic_review(rows, scores, StackProfile(), board)
    assert any("calendar" in item.suggested_correction.lower() for item in findings)


def test_every_finding_carries_all_six_required_fields():
    """§11 lists them; a finding missing one cannot be acted on."""
    scores = flat(4)
    board = board_for(scores, confidence=0.2)
    _, rows = aggregate([scores, scores], board)
    findings = critic_review(rows, scores, StackProfile(), board)
    assert findings
    for item in findings:
        assert item.finding and item.severity and item.suggested_correction
        assert item.evidence_ids is not None and 0 <= item.confidence <= 1


def test_adversarial_reviewer_finds_migration_work_scored_as_incidental():
    scores = flat(2)
    findings = adversarial_review(
        scores, StackProfile(), board_for(scores),
        "adds a schema migration and a backfill for the customer table",
    )
    assert any(item.factor == "data_model_change" for item in findings)


def test_adversarial_reviewer_pairs_complexity_with_test_effort():
    scores = flat(2)
    scores["technical_complexity"] = 4
    findings = adversarial_review(scores, StackProfile(), board_for(scores), "complex work")
    assert any(item.factor == "test_effort" for item in findings)


def test_optimistic_reviewer_finds_double_counted_platform_work():
    scores = flat(2)
    scores["security_review"] = 4
    scores["backend_effort"] = 4
    findings = optimistic_review(scores, StackProfile(), board_for(scores))
    assert any(item.factor == "backend_effort" for item in findings)
    assert all(item.reviewer == "optimistic" for item in findings)


def test_reviewers_are_deterministic():
    scores = flat(3)
    scores["data_model_change"] = 4
    board = board_for(scores)
    text = "schema migration with downstream consumers"
    first = adversarial_review(scores, StackProfile(), board, text)
    second = adversarial_review(scores, StackProfile(), board, text)
    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]


# -- §15 Targeted debate ----------------------------------------------------------------

def test_debate_only_touches_disputed_factors():
    first, second = flat(2), flat(2)
    second["technical_complexity"] = 4
    board = board_for(first)
    _, rows = aggregate([first, second], board)
    contract = build_contract(make_story())
    _, outcome = debate(rows, [], contract)
    assert outcome.factors_debated == ["technical_complexity"]


def test_debate_is_bounded_by_the_contract():
    first, second = flat(1), flat(1)
    second["technical_complexity"] = 5
    board = board_for(first)
    _, rows = aggregate([first, second], board)
    contract = build_contract(make_story())
    _, outcome = debate(rows, [], contract)
    assert max(item.round for item in outcome.rounds) <= contract.max_debate_rounds


def test_protected_factors_settle_conservatively():
    first, second = flat(2), flat(2)
    second["security_review"] = 5
    board = board_for(first)
    _, rows = aggregate([first, second], board)
    resolved, _ = debate(rows, [], build_contract(make_story()))
    assert resolved["security_review"] == 5


def test_a_blocker_finding_escalates_to_human_review():
    scores = flat(4)
    board = board_for(scores, confidence=0.1)
    _, rows = aggregate([scores, scores], board)
    findings = critic_review(rows, scores, StackProfile(), board)
    _, outcome = debate(rows, findings, build_contract(make_story()))
    assert outcome.escalation == "HUMAN_REVIEW"


# -- §17 Validation engine --------------------------------------------------------------

def test_validation_passes_a_well_formed_scorecard():
    scores = flat(3)
    stack = StackProfile()
    result = validate(scores, stack, board_for(scores), calculate(scores, stack))
    assert result.passed, [item.detail for item in result.failures()]


def test_validation_requires_all_sixteen_factors():
    scores = flat(3)
    scores.pop("dod_overhead")
    stack = StackProfile()
    result = validate(scores, stack, board_for(scores), calculate(flat(3), stack))
    assert not result.passed
    assert any(item.rule == "exactly 16 factors" for item in result.failures())


def test_validation_rejects_an_unevidenced_elevated_score():
    scores = flat(2)
    scores["backend_effort"] = 5
    stack = StackProfile()
    result = validate(scores, stack, board_for(scores, confidence=0.1), calculate(scores, stack))
    assert any(item.rule == "every factor >= 4 has evidence" for item in result.failures())


def test_validation_reports_every_rule_whether_or_not_it_fired():
    scores = flat(3)
    stack = StackProfile()
    result = validate(scores, stack, board_for(scores), calculate(scores, stack))
    assert len(result.rules) == 10


def test_validation_checks_the_adjustments_reconcile():
    scores = flat(3)
    stack = StackProfile()
    calculation = calculate(scores, stack)
    result = validate(scores, stack, board_for(scores), calculation)
    schema = next(item for item in result.rules if item.rule == "final output schema valid")
    assert schema.passed


# -- §20 Spike gate ---------------------------------------------------------------------

def test_uncertainty_five_forces_a_spike():
    scores = flat(2)
    scores["uncertainty"] = 5
    assert spike_gate(scores, StackProfile()).decision == "SPIKE"


def test_unfamiliar_framework_forces_a_spike():
    assert spike_gate(flat(2), StackProfile(maturity_level=5)).decision == "SPIKE"


def test_inexperienced_team_on_complex_work_pairs_or_spikes():
    scores = flat(2)
    scores["technical_complexity"] = 4
    gate = spike_gate(scores, StackProfile(team_experience=2))
    assert gate.decision in {"SPIKE", "SPIKE_OR_PAIR"}


def test_two_maxed_factors_force_decomposition_or_a_spike():
    scores = flat(2)
    scores["integration_surface"] = 5
    scores["backend_effort"] = 5
    assert spike_gate(scores, StackProfile()).decision == "DECOMPOSE_OR_SPIKE"


def test_a_clean_story_proceeds():
    gate = spike_gate(flat(2), StackProfile())
    assert gate.decision == "PROCEED"
    assert gate.triggered == []


# -- §10 Reference comparator -----------------------------------------------------------

def history_entry(title: str, points: int, score: int, **extra):
    return {
        "id": title, "title": title, "points": points, "tldr": title,
        "frontend": "react", "backend": "spring_boot",
        "result": {"scorecard": [{"factor": item.id, "score": score} for item in FACTORS]},
        **extra,
    }


def test_comparator_reports_no_anchor_rather_than_inventing_one():
    comparison = compare_references(make_story(), flat(3), 8, [])
    assert comparison.closest is None
    assert comparison.relative_assessment == "unknown"
    assert "no comparable" in comparison.note.lower()


def test_comparator_ranks_the_structurally_closest_story_first():
    history = [
        history_entry("wildly different story", 21, 5),
        history_entry("Add customer risk classification for exposure", 5, 3),
    ]
    comparison = compare_references(make_story(), flat(3), 5, history)
    assert comparison.closest is not None
    assert comparison.closest.points == 5


def test_comparator_states_whether_this_story_is_larger():
    history = [history_entry("Add customer risk classification", 5, 3)]
    assert compare_references(make_story(), flat(3), 8, history).relative_assessment == "larger"
    assert compare_references(make_story(), flat(3), 3, history).relative_assessment == "smaller"


def test_comparator_flags_a_weak_match_instead_of_anchoring_on_it():
    history = [history_entry("completely unrelated infrastructure chore", 34, 5)]
    comparison = compare_references(make_story(), flat(1), 3, history)
    assert "too weak" in comparison.note.lower()


def test_comparator_breaks_similarity_into_its_components():
    history = [history_entry("Add customer risk classification", 5, 3)]
    comparison = compare_references(make_story(), flat(3), 5, history)
    assert set(comparison.closest.components) == {"semantic", "structural", "stack"}


# -- §22 Snapshot / §29 Failure attribution ---------------------------------------------

def test_snapshot_records_every_version_needed_to_explain_a_difference():
    contract = build_contract(make_story())
    snapshot = build_snapshot(contract, "sha256:abc", StackProfile(), "gemma", 2, 12)
    assert snapshot.agent_graph_version == EAGLE_VERSION
    assert snapshot.contract_hash == contract.contract_hash
    assert snapshot.estimator_count == 2
    assert snapshot.reference_dataset_size == 12


def test_failure_attribution_sends_a_missing_evidence_failure_to_retrieval():
    scores = flat(2)
    scores["backend_effort"] = 5
    stack = StackProfile()
    board = board_for(scores, confidence=0.1)
    result = validate(scores, stack, board, calculate(scores, stack))
    _, rows = aggregate([scores, scores], board)
    _, outcome = debate(rows, [], build_contract(make_story()))
    problems = attribute_failure(result, outcome, board, 2)
    assert any(item.layer == "retrieval" for item in problems)


def test_failure_attribution_calls_a_single_pass_a_reviewer_failure():
    scores = flat(3)
    stack = StackProfile()
    board = board_for(scores)
    result = validate(scores, stack, board, calculate(scores, stack))
    _, rows = aggregate([scores], board)
    _, outcome = debate(rows, [], build_contract(make_story()))
    problems = attribute_failure(result, outcome, board, 1)
    assert any(item.layer == "reviewer" for item in problems)


def test_a_healthy_run_attributes_no_failures():
    scores = flat(3)
    stack = StackProfile()
    board = board_for(scores)
    result = validate(scores, stack, board, calculate(scores, stack))
    _, rows = aggregate([scores, scores], board)
    _, outcome = debate(rows, [], build_contract(make_story()))
    assert attribute_failure(result, outcome, board, 2) == []
