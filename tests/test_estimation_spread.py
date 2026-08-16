"""The estimator must be able to tell stories apart.

Every test here exists because of one report: a batch of seventeen stories all came back with
the same number of points. The cause was not the model — it was the heuristic fallback, which
returned a fixed 2 for any factor whose keywords did not appear. Sixteen factors at 2 is a base
sum of 32, already inside the 25-34 band, so the bottom of the Fibonacci scale was unreachable
and everything above it clustered in one or two bands.

A framework that cannot distinguish a copy change from an authentication rewrite is not
producing estimates, whatever number it prints. These tests pin the discrimination itself
rather than any particular value, so the rules can be tuned without rewriting the suite.
"""

from __future__ import annotations

import os
import pathlib

os.environ["PHOENIX_ENABLED"] = "false"

from backend.estimate_code import EstimateDraft, Story, build_scorecard
from backend.estimation_framework import FACTORS, StackProfile, calculate

STACK = StackProfile(frontend="react", backend="spring_boot", database="postgresql")
#: A draft with no factor scores at all, which is what `build_scorecard` receives when the
#: model cannot hold the contract. `drivers` satisfies the "at least one signal" rule without
#: pre-scoring a factor — seeding one hides the very heuristic these tests exist to check.
SEED = EstimateDraft(drivers=["unspecified"])


def _reasons(card: dict) -> dict[str, str]:
    return card["reasons"]


def score(title: str, body: str, criteria: list[str] | None = None, **kwargs) -> dict:
    story = Story(
        title=title, user_story=body, acceptance_criteria=criteria or [], stack=STACK, **kwargs
    )
    card = build_scorecard(SEED, story)
    scores = {item.factor: item.score for item in card}
    calculation = calculate(scores, STACK)
    return {
        "scores": scores,
        "reasons": {item.factor: item.reason for item in card},
        "base": calculation.base_sum,
        "points": calculation.points,
    }


TRIVIAL = ("Fix typo", "Correct 'recieve' to 'receive' on the settings page.", [])
SMALL = ("Add tooltip", "Show a tooltip reading 'Save draft' on the save button.", [])
MEDIUM = (
    "Export orders to CSV",
    "Let users download the orders list as a CSV file from the orders screen.",
    ["Includes every column", "Respects the current filter"],
)
LARGE = (
    "Migrate orders schema",
    "Migrate the orders table to the new schema with a backfill, and keep the legacy API "
    "working during cutover across three downstream services.",
    ["No downtime", "Legacy API still works", "Backfill verified", "Rollback tested"],
)
EPIC = (
    "Replace authentication with OAuth2",
    "Replace basic auth with OAuth2 across every endpoint, encrypt PII at rest, capture GDPR "
    "consent, and add an audit trail. The permission model is rebuilt from scratch and every "
    "existing session must be migrated without logging anyone out.",
    ["All endpoints use OAuth2", "PII encrypted at rest", "Audit events retained seven years",
     "Threat model signed off", "Consent captured", "Sessions migrated"],
)


# -- The failure that was reported ------------------------------------------------------

def test_different_stories_do_not_all_get_the_same_points():
    points = {name: score(*story)["points"]
              for name, story in [("trivial", TRIVIAL), ("small", SMALL), ("medium", MEDIUM),
                                  ("large", LARGE), ("epic", EPIC)]}
    assert len(set(points.values())) >= 3, f"the scale collapsed: {points}"


def test_a_copy_change_is_smaller_than_an_authentication_rewrite():
    assert score(*TRIVIAL)["points"] < score(*EPIC)["points"]


def test_points_rise_with_the_size_of_the_work():
    ladder = [score(*story)["base"] for story in (TRIVIAL, MEDIUM, LARGE, EPIC)]
    assert ladder == sorted(ladder), f"base sums are not monotonic: {ladder}"


# -- Why it collapsed: the floor ---------------------------------------------------------

def test_the_bottom_of_the_fibonacci_scale_is_reachable():
    """With a floor of 2 on every factor the minimum base sum was 32 — already a 5."""
    assert score(*TRIVIAL)["points"] == 3


def test_a_trivial_story_can_score_one_on_a_factor():
    scores = score(*TRIVIAL)["scores"]
    assert min(scores.values()) == 1, scores


def test_a_specified_trivial_story_can_reach_the_bottom_band():
    """A story that bounds itself may be scored small. One that does not may not — see below."""
    assert score("Rename a field", "Rename customerName to customer_name.")["base"] <= 24


# -- Silence is read against what the story pinned down, not against its length ----------
#
# The discriminator is whether the story bounds its own scope, not how long it is. A story that
# says what "done" looks like has licensed a low score on everything it did not mention. A story
# that has not said anything about scope has not, and reading that silence as simplicity is the
# failure the whole framework exists to prevent.

def test_an_unclear_story_scores_high_on_what_it_does_not_say():
    scores = score("Improve reporting", "Improve reporting.")["scores"]
    assert scores["data_model_change"] >= 4
    assert scores["security_review"] >= 4


def test_the_reason_says_the_score_is_high_because_the_story_is_silent():
    """A 4 that means "we were not told" and a 4 that means "we found evidence" are different
    claims, and a reader has to be able to tell which one they are looking at."""
    card = score("Improve reporting", "Improve reporting.")
    reason = next(
        item for factor, item in _reasons(card).items() if factor == "data_model_change"
    ).lower()
    assert "does not say" in reason
    assert "unstated scope is unbounded" in reason
    assert "not because evidence" in reason


def test_an_unclear_story_is_not_cheaper_than_a_specified_one():
    """The bug this guards: two vague words scoring lower than a fully described migration."""
    assert score("Improve reporting", "Improve reporting.")["points"] >= score(*LARGE)["points"]


def test_a_specified_story_may_be_scored_low_on_what_it_rules_out():
    for name, story in (("trivial", TRIVIAL), ("small", SMALL), ("medium", MEDIUM)):
        scores = score(*story)["scores"]
        assert scores["regulatory_compliance"] <= 2, name


def test_a_specified_story_says_so_in_the_reason():
    reason = _reasons(score(*TRIVIAL))["regulatory_compliance"].lower()
    assert "states its finished state" in reason or "bounds its scope" in reason


# -- Exploratory stories are not estimates ----------------------------------------------

def test_an_investigation_is_maximum_uncertainty():
    scores = score("Vendor API", "Investigate whether the new vendor API can replace ours.")["scores"]
    assert scores["uncertainty"] == 5
    assert scores["requirements_clarity"] == 5


def test_a_vague_change_is_uncertain_but_not_a_spike():
    """"Improve performance" is an under-specified change; it still has a known shape."""
    scores = score("Speed up search", "Improve the performance of the search endpoint.")["scores"]
    assert 3 <= scores["uncertainty"] <= 4


def test_a_well_specified_story_is_not_treated_as_uncertain():
    scores = score(*LARGE, technical_breakdown="Add a v2 table, dual-write, backfill, cut over.")["scores"]
    assert scores["uncertainty"] <= 3


# -- Keyword evidence still drives the relevant factor ----------------------------------

def test_schema_work_raises_the_data_model_factor():
    assert score(*LARGE)["scores"]["data_model_change"] >= 4


def test_security_work_raises_security_and_compliance():
    scores = score(*EPIC)["scores"]
    assert scores["security_review"] >= 4
    assert scores["regulatory_compliance"] >= 4


def test_an_incidental_keyword_in_a_long_story_cannot_reach_the_top_alone():
    """One passing mention of an API should not make integration surface a 5."""
    scores = score(
        "Rename a field",
        "Rename customerName to customer_name in the response of the existing api. " * 6,
        ["The field is renamed"],
    )["scores"]
    assert scores["integration_surface"] <= 4


def test_a_declared_absent_stack_scores_one():
    story = Story(title="Backend only", user_story="Add a batch job.",
                  stack=StackProfile(frontend="none", backend="spring_boot"))
    scores = {item.factor: item.score for item in build_scorecard(SEED, story)}
    assert scores["frontend_effort"] == 1


# -- The model, when it works, still owns the score --------------------------------------

def test_model_scores_are_used_verbatim_and_span_the_whole_scale():
    for level in (1, 3, 5):
        draft = EstimateDraft(factors=[
            {"factor": item.id, "score": level, "reason": "model evidence"} for item in FACTORS
        ])
        story = Story(title="t", user_story="body", stack=STACK)
        scores = {item.factor: item.score for item in build_scorecard(draft, story)}
        assert set(scores.values()) == {level}
        assert calculate(scores, STACK).base_sum == level * len(FACTORS)


# -- The second collapse: everything at 5 -------------------------------------------------
#
# The first report was "every story is 8". The fix moved the floor, and the next report was
# "every story is 5" — from two independent causes that both produce clustering and needed
# separate fixes.

def test_a_uniform_scorecard_is_rejected_as_carrying_no_information():
    """Sixteen factors at 2 is a base sum of 32 — the middle of one band. Every story scored
    that way returns 5 points, and the scorecard looks like an assessment while being one
    answer repeated sixteen times."""
    from backend.estimate_code import _validate_draft

    for value in (2, 3, 4):
        draft = EstimateDraft(factors=[
            {"factor": item.id, "score": value, "reason": "r"} for item in FACTORS
        ])
        message = _validate_draft(draft)
        assert message, f"all {value}s should be rejected"
        assert f"scored {value}" in message
        assert "not read factor by factor" in message


def test_the_extremes_are_not_treated_as_degenerate():
    """All 1s is a coherent claim — nothing applies — and lands in the smallest band. All 5s is
    coherent too, and the spike gate answers it. Only the middle hides a non-answer."""
    from backend.estimate_code import _validate_draft

    for value in (1, 5):
        draft = EstimateDraft(factors=[
            {"factor": item.id, "score": value, "reason": "r"} for item in FACTORS
        ])
        assert _validate_draft(draft) is None


def test_a_varied_scorecard_passes():
    from backend.estimate_code import _validate_draft

    draft = EstimateDraft(factors=[
        {"factor": item.id, "score": 1 + (index % 4), "reason": "r"}
        for index, item in enumerate(FACTORS)
    ])
    assert _validate_draft(draft) is None


def test_the_scale_separates_a_typo_from_a_described_endpoint():
    """Both are short. The earlier thresholds started at 220 characters and three criteria, so
    both scored 0 and every unmatched factor fell to 1 — which is why small stories all came
    back at the same number."""
    typo = score("Fix typo", "Correct 'recieve' to 'receive' on the settings page.")
    endpoint = score(
        "Add endpoint",
        "Add GET /api/v1/orders/{id}/history returning the audit trail.",
        ["Returns 200", "404 when missing"],
    )
    assert typo["points"] < endpoint["points"]


def test_small_stories_do_not_all_land_in_one_band():
    """The reported failure: a backlog of small-to-medium stories all returning one number."""
    stories = [
        ("Fix typo", "Correct 'recieve' to 'receive' on the settings page.", []),
        ("Export CSV", "Let users download the orders list as a CSV file.",
         ["All columns", "Respects filter"]),
        ("Add endpoint", "Add GET /api/v1/orders/{id}/history returning the audit trail.",
         ["Returns 200", "404 when missing"]),
        ("Migration", "Migrate the orders table to the new schema with a backfill and keep the "
         "legacy API working during cutover across three services.",
         ["No downtime", "Legacy API works", "Backfill verified", "Rollback tested"]),
    ]
    points = [score(*item)["points"] for item in stories]
    assert len(set(points)) >= 3, f"small stories collapsed into one band: {points}"
    assert points == sorted(points), f"points do not rise with the work: {points}"


# -- The model must actually be used ------------------------------------------------------
#
# Rejecting a uniform scorecard fixed the clustering and created a worse problem: every
# estimate then ran on keyword heuristics with no model contribution at all. The focus pass
# exists so the model is asked something it can answer rather than dropped.

def test_the_focus_pass_asks_for_lists_not_scores():
    from backend.estimate_code import build_focus_prompt

    prompt = build_focus_prompt(Story(title="t", user_story="body", stack=STACK))
    assert "touched" in prompt and "largest" in prompt and "unclear" in prompt
    assert "ids only, no scores" in prompt
    # The grounding contract travels with every prompt, including this one.
    assert "The provided text does not contain this information." in prompt


def test_focus_ids_are_normalised_and_unknown_ones_dropped():
    from backend.estimate_code import FactorFocus

    focus = FactorFocus.model_validate({
        "touched": ["Backend Effort", "test-effort", "not_a_factor"],
        "biggest": ["backend_effort"],
        "unanswered": ["data model change"],
    })
    assert "backend_effort" in focus.touched
    assert "test_effort" in focus.touched
    assert focus.largest == ["backend_effort"]
    assert focus.unclear == ["data_model_change"]


def test_a_focus_naming_nothing_real_is_rejected():
    """Unknown ids are dropped by the alias mapping, so a response naming only invented
    factors fails construction and reaches the repair loop rather than scoring anything."""
    import pytest as _pytest

    from backend.estimate_code import FactorFocus, _validate_focus

    with _pytest.raises(ValueError):
        FactorFocus.model_validate({"touched": ["nonsense"], "largest": [], "unclear": []})
    assert _validate_focus(FactorFocus(touched=["backend_effort"], largest=[], unclear=[])) is None


def test_the_model_reading_becomes_a_full_spread_of_scores():
    """Untouched 1, involved 3, largest 4, unanswered 4, both 5 — the model judged, code
    did the arithmetic."""
    from backend.estimate_code import FactorFocus, scores_from_focus

    focus = FactorFocus(
        touched=["backend_effort", "test_effort"],
        largest=["backend_effort"],
        unclear=["data_model_change", "security_review"],
    )
    scores = scores_from_focus(focus, Story(title="t", user_story="b", stack=STACK))
    assert scores["backend_effort"]["score"] == 4
    assert scores["test_effort"]["score"] == 3
    assert scores["data_model_change"]["score"] == 4
    assert scores["frontend_effort"]["score"] == 1
    assert len({item["score"] for item in scores.values()}) >= 3


def test_a_factor_both_largest_and_unanswered_is_maximal():
    from backend.estimate_code import FactorFocus, scores_from_focus

    focus = FactorFocus(touched=["data_model_change"], largest=["data_model_change"],
                        unclear=["data_model_change"])
    scores = scores_from_focus(focus, Story(title="t", user_story="b", stack=STACK))
    assert scores["data_model_change"]["score"] == 5


def test_an_unanswered_factor_still_says_why_it_scored_high():
    from backend.estimate_code import FactorFocus, scores_from_focus

    focus = FactorFocus(touched=[], largest=[], unclear=["data_model_change"])
    reason = scores_from_focus(focus, Story(title="t", user_story="b", stack=STACK))[
        "data_model_change"]["why"].lower()
    assert "unstated scope is unbounded" in reason
    assert "not because evidence" in reason


# -- Stages must report what they found, not only what they are for -----------------------
#
# A stage that explains its own purpose tells the reader something that was equally true
# before they typed anything. These assert the *events* carry the story's own material, which
# is what the narration renders.

def _events_for(story_kwargs: dict, scores: dict[str, int] | None = None) -> list[dict]:
    import asyncio
    import json

    from backend.config import get_settings
    from backend.estimate_code import EstimateService
    from backend.estimation_framework import FACTOR_IDS

    values = scores or {factor: 1 + (index % 4) for index, factor in enumerate(FACTOR_IDS)}

    class Runtime:
        async def generate(self, *_args, **_kwargs):
            return json.dumps({
                "scores": {f: {"score": values[f], "why": f"read {f}"} for f in FACTOR_IDS}
            })

    events: list[dict] = []
    asyncio.run(EstimateService(Runtime(), get_settings()).estimate(
        Story(stack=STACK, **story_kwargs), events.append
    ))
    return events


def _evidence(events: list[dict], stage: str) -> dict:
    return next(
        item.get("evidence") or {}
        for item in reversed(events)
        if item["stage"] == stage and item["status"] != "running"
    )


STORY = {
    "title": "Add customer risk classification",
    "user_story": "As a risk officer I need customers classified so exposure is visible.",
    "acceptance_criteria": ["Risk category is persisted", "Audit events are generated"],
    "components": ["crm"],
}


def test_the_contract_event_carries_what_it_sealed():
    evidence = _evidence(_events_for(STORY), "contract")
    assert evidence["objective"] == STORY["user_story"]
    assert evidence["acceptance_criteria"] == STORY["acceptance_criteria"]
    assert evidence["stack"]["backend"] == STACK.backend
    assert evidence["affected_application"] == "crm"
    assert evidence["contract_hash"].startswith("sha256:")


def test_the_requirements_event_numbers_what_the_story_asked_for():
    """Estimation and code generation read the same story through the same decomposition.

    Scoring prose while the builder works from numbered requirements is how a factor gets
    scored against a requirement nobody ever wrote down.
    """
    evidence = _evidence(_events_for(STORY), "requirements")
    assert [item["id"] for item in evidence["functional"]][:1] == ["FR-001"]
    assert all(item["source"] for item in evidence["functional"])
    assert isinstance(evidence["assumptions"], list)
    assert isinstance(evidence["open_questions"], list)


def test_every_pipeline_checkpoint_has_a_stage_that_can_produce_it():
    """The checklist and the pipeline are two lists that must not drift apart.

    A checkpoint no stage emits sits permanently pending, which reads as a stalled run — and
    that is exactly how `requirements` shipped: label, tooltip and narrator all present, and
    nothing on the server ever sent the event.
    """
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    screen = (root / "frontend" / "src" / "EstimateCodeScreen.tsx").read_text(encoding="utf-8")
    node_map = re.search(r"const NODE_MAP[^{]*\{(.*?)^\}", screen, re.S | re.M).group(1)
    checkpoints = {
        stage: re.findall(r"'([a-z_]+)'", body)
        for stage, body in re.findall(r"^  (\w+): \[([^\]]*)\]", node_map, re.M)
    }
    assert len(checkpoints) > 20, "NODE_MAP was not parsed"

    source = (root / "backend" / "estimate_code.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'"stage": "(\w+)"', source))
    unreachable = sorted(set(checkpoints) - emitted)
    assert not unreachable, f"checklist steps no stage emits: {unreachable}"

    listed = set(re.findall(
        r"'([a-z_]+)'", re.search(r"const steps = \[(.*?)\]", screen, re.S).group(1)))
    unlisted = sorted({s for nodes in checkpoints.values() for s in nodes} - listed)
    assert not unlisted, f"stages producing a step the checklist never shows: {unlisted}"


def test_routing_names_the_roles_and_what_each_owns():
    roles = _evidence(_events_for(STORY), "specialist_routing")["roles"]
    assert roles
    for item in roles:
        assert item["role"] and item["owns"] and item["why"]


def test_readiness_names_the_checks_that_were_not_ready():
    evidence = _evidence(_events_for(STORY), "readiness")
    assert isinstance(evidence["assumptions"], list)
    assert isinstance(evidence["questions"], list)
    for item in evidence["unready"]:
        assert item["area"] and item["status"] != "ready" and item["detail"]


def test_stack_calibration_names_the_anchors_it_loaded():
    anchors = _evidence(_events_for(STORY), "declare_stack")["anchors"]
    assert anchors and all("pts" in item for item in anchors)


def test_the_scorecard_event_names_what_costs_most():
    evidence = _evidence(_events_for(STORY), "score_factors")
    assert evidence["highest"] and evidence["lowest"]
    assert "/5" in evidence["highest"][0]


def _spread(**pinned: int) -> dict[str, int]:
    """A varied scorecard with named factors pinned.

    A fixture that pins one factor and leaves the other fifteen identical is itself the
    degenerate scorecard `_validate_draft` rejects, so it never reaches the stage under test.
    """
    from backend.estimation_framework import FACTOR_IDS

    scores = {factor: 1 + (index % 4) for index, factor in enumerate(FACTOR_IDS)}
    scores.update(pinned)
    return scores


def test_the_calculation_names_the_rules_that_fired():
    # Uncertainty at 4 fires a base adjustment, so there is a rule to name.
    evidence = _evidence(_events_for(STORY, _spread(uncertainty=4)), "calculate")
    assert any("uncertainty" in item for item in evidence["applied"])


def test_a_failed_gate_says_which_one_and_why():
    evidence = _evidence(_events_for(STORY, _spread(uncertainty=5)), "policy_gate")
    assert evidence["failed_detail"]
    assert any("uncertainty" in item.lower() for item in evidence["failed_detail"])
    assert evidence["confidence_detail"]


def test_the_calculation_reports_its_three_parts_separately():
    """Base adjustments, stack adjustments and the Fibonacci map are three checklist steps
    driven by one event. Without separate content the same sentence prints three times."""
    evidence = _evidence(_events_for(STORY, _spread(uncertainty=4, reversibility=4)), "calculate")
    assert evidence["base_applied"], "no base rule fired, so the step has nothing to report"
    assert evidence["base_skipped"], "rules that did not fire are evidence too"
    assert "stack_applied" in evidence and "stack_skipped" in evidence
    assert evidence["base_sum"] and evidence["band"] and evidence["points"]


def test_the_gate_step_and_the_decision_step_carry_different_content():
    evidence = _evidence(_events_for(STORY, _spread(uncertainty=5)), "policy_gate")
    assert evidence["failed_detail"], "the gate step needs the failures"
    assert evidence["gates_passed"], "and the gates that held"
    assert evidence["recommendation"], "the decision step needs the recommendation"
    assert evidence["recommendation_detail"]
