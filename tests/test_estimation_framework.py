"""The framework specification's own worked examples, as executable regression tests.

Section 12 of ``agile_story_point_estimation_framework_fullstack.md`` publishes four
end-to-end walkthroughs with their base sums, adjustments, and final point values. They
are the only independent oracle available for this engine, so they are pinned here: if
the calculator ever drifts from the published framework, these fail.
"""

import pytest

from backend.estimation_framework import (
    FACTOR_IDS,
    calculate,
    confidence,
    decide,
    policy_checks,
    risk_flags,
    StackProfile,
)


def scores(*values: int) -> dict[str, int]:
    """Map 16 positional scores onto factor ids in the framework's numbered order."""
    assert len(values) == 16, "The framework defines exactly 16 factors"
    return dict(zip(FACTOR_IDS, values, strict=True))


def run(values: dict[str, int], stack: StackProfile):
    calculation = calculate(values, stack)
    checks = policy_checks(values, stack, calculation)
    recommendation, _ = decide(checks, stack, calculation, values)
    level, _ = confidence(values, stack, calculation)
    return calculation, recommendation, level


def test_spring_boot_kafka_walkthrough():
    """§12.1 — base 48, +1 for a new observability signal, 49 → 13 points."""
    values = scores(2, 4, 4, 3, 1, 4, 4, 2, 3, 4, 3, 3, 3, 3, 3, 2)
    stack = StackProfile(
        backend="spring_boot", maturity_level=2, team_experience=4,
        new_observability_signal=True,
    )
    calculation, recommendation, level = run(values, stack)

    assert calculation.base_sum == 48
    assert calculation.base_adjustment_total == 0, "no factor crosses a §8.1 threshold"
    assert calculation.stack_adjustment_total == 1
    assert calculation.adjusted_score == 49
    assert calculation.band == "45-54"
    assert calculation.points == 13
    assert calculation.cap_exceeded is False, "maturity 2 allows up to 21 points"
    assert recommendation == "proceed"
    # The walkthrough's prose says "Medium", but five factors score 4 and the §13.2
    # confidence table defines Low as "3+ factors ≥ 4". The table is the normative rule,
    # so the engine follows it and reports the more cautious level.
    assert level == "Low"


def test_react_collaboration_cursors_walkthrough():
    """§12.2 — base 49, +3 uncertainty, +1 full-stack tax, +1 new test layer, 54 → 13."""
    values = scores(3, 5, 4, 2, 5, 3, 4, 1, 3, 3, 2, 2, 4, 4, 2, 2)
    stack = StackProfile(
        frontend="react", maturity_level=3, team_experience=3, new_testing_layer=True,
    )
    calculation, recommendation, level = run(values, stack)

    assert calculation.base_sum == 49
    assert calculation.base_adjustment_total == 4
    assert calculation.stack_adjustment_total == 1
    assert calculation.adjusted_score == 54
    assert calculation.points == 13
    assert level == "Low", "two factors at 5 and several at 4"
    # Technical complexity and frontend effort both score 5, so §10's "two or more factors
    # at 5" gate fires. This matches the walkthrough's published recommendation: Decompose.
    assert recommendation == "decompose"


def test_fastapi_pipeline_walkthrough_resolves_the_decompose_or_spike_fork():
    """§12.3 — 54 adjusted, but maturity 4 caps at 8, and uncertainty 4 forces the spike."""
    values = scores(2, 4, 3, 3, 3, 4, 4, 1, 3, 3, 2, 2, 4, 4, 3, 2)
    stack = StackProfile(
        backend="fastapi", maturity_level=4, team_experience=3, new_testing_layer=True,
    )
    calculation, recommendation, level = run(values, stack)

    assert calculation.base_sum == 47
    assert calculation.base_adjustment_total == 4, "uncertainty ≥ 4 and the full-stack tax"
    assert calculation.stack_adjustment_total == 3, "emerging framework and a new test layer"
    assert calculation.adjusted_score == 54
    assert calculation.mapped_points == 13
    assert calculation.maturity_cap == 8
    assert calculation.cap_exceeded is True
    # §13.4 offers "DECOMPOSE or SPIKE"; §12.3's published recommendation is a spike.
    assert recommendation == "spike_first"
    assert level == "Low"


def test_flask_auth0_migration_walkthrough_refuses_to_estimate():
    """§12.4 — uncertainty 5 means the point value is meaningless; spike first."""
    values = scores(3, 5, 5, 4, 3, 5, 5, 3, 5, 3, 4, 5, 5, 3, 4, 4)
    stack = StackProfile(
        backend="flask", maturity_level=4, team_experience=1,
    )
    calculation, recommendation, level = run(values, stack)

    # The published walkthrough states 67; summing its own table gives 66. The table is
    # the normative input, so the engine follows it.
    assert calculation.base_sum == 66
    # The walkthrough tallies +6 because it stops applying rules once it declares the spike
    # ("Adjusted Score: 77 — irrelevant, spike required"). The engine keeps evaluating every
    # rule so the audit trail stays complete: +3 uncertainty, +2 cross-team, +2 reversibility,
    # +1 full-stack tax, +2 security review.
    assert calculation.base_adjustment_total == 10
    assert recommendation == "spike_first"
    assert level == "Low"
    flags = risk_flags(values, stack)
    assert len([flag for flag in flags if flag["source"] == "factor"]) == 11
    assert flags[0]["score"] == 5, "risk flags lead with the most severe factors"
    assert any(flag["source"] == "stack" for flag in flags), "Flask hazards are flagged too"


# --------------------------------------------------------------------------------------
# Rule-level behaviour
# --------------------------------------------------------------------------------------


def test_every_rule_is_recorded_even_when_it_does_not_fire():
    """Evidence design: a penalty that was considered and skipped is still shown."""
    values = scores(*([1] * 16))
    calculation = calculate(values, StackProfile())

    assert calculation.base_sum == 16
    assert calculation.adjusted_score == 16
    assert calculation.points == 3, "the framework floor is 3 points"
    fired = [step for step in calculation.steps if step.applied]
    skipped = {step.rule for step in calculation.steps if not step.applied}
    assert [step.rule for step in fired] == ["base_sum", "fibonacci_map"]
    assert skipped == {
        # §8.1 base adjustments, none of which apply to an all-1 scorecard.
        "uncertainty_ge_4", "cross_team_ge_4", "reversibility_ge_4", "full_stack_tax",
        "review_cycle_tax",
        # §8.2 stack adjustments, none of which apply to a default stack profile.
        "maturity_bleeding_edge", "maturity_emerging", "maturity_legacy",
        "team_experience_low", "new_testing_layer", "new_observability_signal",
        "build_pattern_change", "polyglot_boundary",
        # §9 cap, evaluated and not breached.
        "maturity_cap",
    }
    assert all(step.reference for step in calculation.steps), "every step cites the spec"


def test_running_total_is_replayable_by_hand():
    """The audit trail must reconcile: each applied delta accumulates to the final score."""
    values = scores(3, 4, 3, 2, 4, 4, 3, 1, 5, 2, 4, 4, 4, 3, 2, 2)
    calculation = calculate(values, StackProfile(frontend="react", backend="fastapi",
                                                 maturity_level=4, team_experience=2))
    replay = 0
    for step in calculation.steps:
        replay += step.delta
        assert step.running_total == replay, f"{step.rule} breaks the running total"
    assert replay == calculation.adjusted_score


@pytest.mark.parametrize(
    ("adjusted_target", "expected"),
    # 66 is the highest total reachable without tripping an §8.1 rule, so it stands in for
    # the open-ended 65+ band.
    [(16, 3), (24, 3), (25, 5), (34, 5), (35, 8), (44, 8), (45, 13), (54, 13), (55, 21),
     (64, 21), (65, 34), (66, 34)],
)
def test_fibonacci_band_boundaries(adjusted_target: int, expected: int):
    """§9 — every band boundary maps to the published point value."""
    # Build a scorecard summing exactly to the target while keeping every §8.1 rule dormant,
    # so the test isolates the band mapping. The five threshold factors stop at 3, and
    # backend effort stays at 1 so the full-stack tax cannot fire alongside frontend effort.
    threshold_factors = {"uncertainty", "cross_team_dependency", "reversibility",
                         "regulatory_compliance", "security_review"}
    values = scores(*([1] * 16))
    remaining = adjusted_target - 16
    for factor_id in ("technical_complexity", "integration_surface", "data_model_change",
                      "test_effort", "documentation_knowledge_transfer", "dod_overhead",
                      "performance_scalability", "requirements_clarity",
                      "observability_operations", "frontend_effort",
                      *sorted(threshold_factors)):
        headroom = min(remaining, 2 if factor_id in threshold_factors else 4)
        values[factor_id] += headroom
        remaining -= headroom
    assert remaining == 0, f"{adjusted_target} unreachable without triggering a rule"

    calculation = calculate(values, StackProfile())
    assert calculation.base_adjustment_total == 0
    assert calculation.adjusted_score == adjusted_target
    assert calculation.points == expected


def test_bleeding_edge_framework_is_never_estimated():
    """§10 — maturity 5 short-circuits the flowchart before any size reasoning."""
    values = scores(*([1] * 16))
    stack = StackProfile(backend="fastapi", maturity_level=5)
    calculation, recommendation, _ = run(values, stack)
    assert recommendation == "upgrade_framework_first"
    assert calculation.maturity_cap == 5


def test_framework_migration_is_an_epic_not_a_story():
    values = scores(*([2] * 16))
    calculation, recommendation, _ = run(
        values, StackProfile(backend="spring_boot", scenario="framework_migration")
    )
    assert recommendation == "epic_discovery"
    assert calculation.points == 5, "the number is still computed; the decision overrides it"


def test_low_team_experience_with_high_complexity_forces_a_spike():
    values = scores(2, 4, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 3, 2, 2, 2)
    _, recommendation, _ = run(
        values, StackProfile(backend="spring_boot", maturity_level=2, team_experience=2)
    )
    assert recommendation == "spike_first"


def test_high_confidence_requires_a_clean_scorecard_and_no_stack_penalty():
    clean = scores(*([3] * 16))
    calculation = calculate(clean, StackProfile(maturity_level=3, team_experience=4))
    level, detail = confidence(clean, StackProfile(maturity_level=3, team_experience=4),
                               calculation)
    assert level == "High"
    assert "3 or below" in detail

    with_penalty = StackProfile(maturity_level=3, team_experience=4, build_pattern_change=True)
    level, _ = confidence(clean, with_penalty, calculate(clean, with_penalty))
    assert level == "Medium", "any stack penalty rules out High confidence"


def test_stack_profile_selects_guidance_anchors_and_risks():
    stack = StackProfile(frontend="react", backend="spring_boot")
    guidance = stack.guidance()
    assert "React" in " ".join(guidance["frontend_effort"])
    assert "Spring Boot" in " ".join(guidance["backend_effort"])
    # Both declared stacks contribute a note to a shared factor, each labelled by stack.
    complexity = guidance["technical_complexity"]
    assert len(complexity) == 2
    assert "Hibernate N+1" in " ".join(complexity)
    assert {anchor["stack"] for anchor in stack.anchors()} == {"ReactJS", "Spring Boot"}
    risks = " ".join(stack.risks())
    assert "State management sprawl" in risks and "Starter transitive" in risks
    assert "RxJS" not in risks, "Angular hazards must not leak into a React + Spring profile"

    generic = StackProfile()
    assert generic.guidance() == {}
    assert all(anchor["stack"] == "Generic" for anchor in generic.anchors())
