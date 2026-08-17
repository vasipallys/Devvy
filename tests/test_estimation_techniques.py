"""The five estimation techniques.

Two properties matter more than any individual rule here, and both are easy to lose.

The first is that **no technique lets a model choose the number**. Every point value in this
module is produced by published arithmetic from judgements the model supplied, which is the
whole reason a reader can replay a scorecard by hand.

The second is that the techniques stay *different from each other*. It is very easy to build
five code paths that all end up calling the same function and returning the same answer, at
which point the picker is decoration. Planning Poker must be able to disagree with the bucket
system, and a technique that cannot diverge from the factor arithmetic is not a second opinion.
"""

from __future__ import annotations

import os
import tempfile

os.environ["PHOENIX_ENABLED"] = "false"
os.environ.setdefault("APP_DATA_DIR", tempfile.mkdtemp())

import pytest

from backend.estimation_framework import FACTOR_IDS, FIBONACCI_POINTS, StackProfile, calculate
from backend.estimation_techniques import (
    ANCHOR_OVERLAP,
    CONSENSUS_SPREAD,
    DOTS_PER_MEMBER,
    OWNER_OF,
    SQUAD,
    TECHNIQUE_BY_ID,
    TECHNIQUE_IDS,
    TSHIRT_POINTS,
    MemberVote,
    affinity_outcome,
    apply_dots,
    assemble_scorecard,
    bucket_outcome,
    card_for,
    dot_tally,
    facilitator_note,
    ladder_step,
    match_anchor,
    outliers,
    poker_outcome,
    spread_of,
    squad_for,
    tshirt_outcome,
)

STACK = StackProfile(frontend="react", backend="fastapi", database="postgres")
BASELINE = {factor: 3 for factor in FACTOR_IDS}


def seat(role: str, **scores: int) -> MemberVote:
    member = next(item for item in SQUAD if item.role == role)
    vote = MemberVote(
        role=member.role, label=member.label, discipline=member.discipline,
        owns=list(member.owns), scores=scores or {f: 3 for f in member.owns},
    )
    vote.points = card_for(vote, BASELINE, STACK)
    return vote


# -- The squad ------------------------------------------------------------------------------

def test_every_factor_has_exactly_one_owner():
    """Shared ownership would mean the assembled scorecard had two candidate values for a
    factor and a tie-break nobody published."""
    assert set(OWNER_OF) == set(FACTOR_IDS)
    owned = [factor for member in SQUAD for factor in member.owns]
    assert len(owned) == len(set(owned)) == len(FACTOR_IDS)


def test_the_squad_covers_the_disciplines_a_team_actually_has():
    disciplines = {member.discipline for member in SQUAD}
    assert {"frontend", "backend", "automation testing", "data", "functional"} <= disciplines


def test_routing_narrows_the_room_but_never_empties_the_functional_seat():
    """Somebody always has to say what the story asks for."""
    chosen = squad_for("a copy change", ["FRONTEND"])
    roles = {item.role for item in chosen}
    assert roles == {"FRONTEND", "PRODUCT_DOMAIN"}
    assert len(squad_for("anything", [])) == len(SQUAD)


# -- Planning Poker -------------------------------------------------------------------------

def test_cards_can_actually_disagree():
    """The bug this pins: a seat owns up to three of sixteen factors, so laying its scores over
    the baseline moved the total by a few points and never crossed a band edge. Every card came
    back identical, the spread was always zero, and the second round could never fire."""
    quiet = seat("PRODUCT_DOMAIN", requirements_clarity=1, documentation_knowledge_transfer=1)
    loud = seat("SECURITY_COMPLIANCE", security_review=5, regulatory_compliance=5)
    assert quiet.points != loud.points
    assert spread_of([quiet.points, loud.points]) >= 2


def test_a_card_is_the_story_seen_from_one_seat():
    """Higher concern in my own dimensions means a higher card, always."""
    low = seat("FRONTEND", frontend_effort=1)
    high = seat("FRONTEND", frontend_effort=5)
    assert high.points > low.points


def test_spread_is_measured_in_ladder_steps_not_points():
    """21 to 34 is thirteen points and one step; 3 to 5 is two points and also one step."""
    assert spread_of([21, 34]) == 1
    assert spread_of([3, 5]) == 1
    assert spread_of([3, 34]) == 5
    assert spread_of([8, 8]) == 0


def test_a_narrow_spread_does_not_trigger_a_second_round():
    votes = [seat("FRONTEND", frontend_effort=3), seat("BACKEND", backend_effort=3,
                                                       performance_scalability=3)]
    assert outliers(votes) == []


def test_a_wide_spread_re_polls_only_the_extremes():
    """Re-asking the whole room when two people disagree is how a short session becomes a long
    one — and on a CPU model it is minutes of wall clock for cards that will not move."""
    votes = [
        seat("PRODUCT_DOMAIN", requirements_clarity=1, documentation_knowledge_transfer=1),
        seat("FRONTEND", frontend_effort=3),
        seat("SECURITY_COMPLIANCE", security_review=5, regulatory_compliance=5),
    ]
    extremes = outliers(votes)
    assert set(extremes) == {"PRODUCT_DOMAIN", "SECURITY_COMPLIANCE"}
    assert "FRONTEND" not in extremes


def test_the_final_scorecard_takes_each_factor_from_its_owner_not_an_average():
    """Averaging the frontend engineer's guess at the migration with the data engineer's
    knowledge of it produces a number neither would defend."""
    votes = [
        seat("DATA_MIGRATION", data_model_change=5),
        seat("FRONTEND", frontend_effort=1),
    ]
    scores, attribution = assemble_scorecard(votes, BASELINE)
    assert scores["data_model_change"] == 5
    assert attribution["data_model_change"] == "DATA_MIGRATION"
    assert scores["frontend_effort"] == 1
    # Nobody owned this one, so it keeps the baseline and says so.
    assert attribution["security_review"] == "baseline"
    assert scores["security_review"] == BASELINE["security_review"]


def test_a_seat_cannot_overwrite_a_dimension_it_does_not_own():
    """The frontend engineer having an opinion about the migration is fine. Writing it into the
    estimate is not."""
    intruder = seat("FRONTEND", frontend_effort=1)
    intruder.scores["data_model_change"] = 1
    scores, attribution = assemble_scorecard([intruder], BASELINE)
    assert scores["data_model_change"] == BASELINE["data_model_change"]
    assert attribution["data_model_change"] == "baseline"


def test_an_unresolved_room_is_flagged_rather_than_averaged_away():
    votes = [
        seat("PRODUCT_DOMAIN", requirements_clarity=1, documentation_knowledge_transfer=1),
        seat("SECURITY_COMPLIANCE", security_review=5, regulatory_compliance=5),
    ]
    outcome, _, _ = poker_outcome(votes, BASELINE, STACK, rounds=1)
    assert outcome.consensus == "unresolved"
    assert outcome.needs_human
    assert outcome.points in FIBONACCI_POINTS


def test_agreement_is_reported_as_agreement():
    votes = [seat("FRONTEND", frontend_effort=3), seat("DATA_MIGRATION", data_model_change=3)]
    outcome, _, _ = poker_outcome(votes, BASELINE, STACK, rounds=1)
    assert outcome.consensus in {"unanimous", "consensus"}
    assert not outcome.needs_human
    assert outcome.spread <= CONSENSUS_SPREAD


def test_the_number_comes_from_the_scorecard_not_from_the_cards():
    """The cards are a disagreement signal. The estimate is the owners' scorecard."""
    votes = [seat("DATA_MIGRATION", data_model_change=5)]
    outcome, calculation, scores = poker_outcome(votes, BASELINE, STACK, rounds=1)
    assert outcome.points == calculation.points == calculate(scores, STACK).points


def test_every_rule_that_fired_is_recorded():
    votes = [seat("FRONTEND", frontend_effort=4)]
    outcome, _, _ = poker_outcome(votes, BASELINE, STACK, rounds=1)
    assert any("revealed together" in step for step in outcome.steps)
    assert any("§9" in step for step in outcome.steps)


# -- T-Shirt Sizing -------------------------------------------------------------------------

@pytest.mark.parametrize("size,points", list(TSHIRT_POINTS.items()))
def test_each_size_maps_through_the_published_table(size, points):
    framework = calculate(BASELINE, STACK)
    assert tshirt_outcome(size, "because", framework).points == points


def test_sizing_is_case_and_space_insensitive():
    framework = calculate(BASELINE, STACK)
    assert tshirt_outcome("  m ", "", framework).points == TSHIRT_POINTS["M"]


def test_an_unusable_size_falls_back_to_the_arithmetic_and_says_so():
    """Rather than guessing what "medium-large" meant."""
    framework = calculate(BASELINE, STACK)
    outcome = tshirt_outcome("medium-large", "", framework)
    assert outcome.points == framework.points
    assert outcome.needs_human
    assert "did not return one of" in outcome.steps[0]


def test_disagreement_with_the_arithmetic_is_reported_not_reconciled():
    """That gap is the finding: a quick size and a scored one part company where the story
    hides its work."""
    framework = calculate(BASELINE, STACK)
    outcome = tshirt_outcome("XXL", "", framework)
    assert outcome.divergence != 0
    assert any("disagree" in step for step in outcome.steps)
    assert outcome.framework_points == framework.points


# -- Dot Voting -----------------------------------------------------------------------------

def dotted(role: str, *factors: str) -> MemberVote:
    member = next(item for item in SQUAD if item.role == role)
    return MemberVote(role=member.role, label=member.label, discipline=member.discipline,
                      dots=list(factors))


def test_a_seat_cannot_spend_more_dots_than_it_has():
    """Scarcity is the entire mechanism."""
    greedy = dotted("FRONTEND", *FACTOR_IDS[:8])
    tally = dot_tally([greedy])
    assert sum(tally.values()) == DOTS_PER_MEMBER


def test_a_dimension_the_room_agrees_on_is_raised():
    votes = [dotted(role, "uncertainty") for role in
             ("FRONTEND", "BACKEND", "DATA_MIGRATION", "TEST_QUALITY")]
    scores, _ = apply_dots(BASELINE, dot_tally(votes), squad_size=4)
    assert scores["uncertainty"] >= 4


def test_unanimous_concern_reaches_the_top_of_the_scale():
    votes = [dotted(role, "uncertainty") for role in
             ("FRONTEND", "BACKEND", "DATA_MIGRATION")]
    scores, _ = apply_dots(BASELINE, dot_tally(votes), squad_size=3)
    assert scores["uncertainty"] == 5


def test_dots_never_lower_a_score():
    """Nobody spends a scarce dot to say something is easy, so silence is not evidence of ease."""
    high = {**BASELINE, "security_review": 5}
    scores, _ = apply_dots(high, dot_tally([dotted("FRONTEND", "uncertainty")]), squad_size=6)
    assert scores["security_review"] == 5


def test_one_voice_is_not_a_majority():
    votes = [dotted("FRONTEND", "uncertainty")]
    scores, _ = apply_dots(BASELINE, dot_tally(votes), squad_size=6)
    assert scores["uncertainty"] == BASELINE["uncertainty"]


def test_every_dot_decision_is_recorded_whether_or_not_it_fired():
    votes = [dotted("FRONTEND", "uncertainty")]
    _, steps = apply_dots(BASELINE, dot_tally(votes), squad_size=6)
    assert any("below threshold, unchanged" in step for step in steps)


# -- Affinity Mapping -----------------------------------------------------------------------

def test_a_strong_cluster_lends_its_delivered_size():
    framework = calculate(BASELINE, STACK)
    matches = [
        {"title": "Similar work", "points": 21, "similarity": 0.91},
        {"title": "Also similar", "points": 21, "similarity": 0.80},
    ]
    outcome = affinity_outcome(matches, "same shape", framework)
    assert outcome.points == 21
    assert not outcome.needs_human


def test_a_weak_resemblance_is_reported_as_weak_and_never_used_as_an_anchor():
    """Otherwise a coincidence of vocabulary silently sets the estimate."""
    framework = calculate(BASELINE, STACK)
    outcome = affinity_outcome(
        [{"title": "Vaguely alike", "points": 34, "similarity": 0.30}], "", framework
    )
    assert outcome.points == framework.points
    assert outcome.needs_human
    assert any("not used as an anchor" in step for step in outcome.steps)


def test_no_history_is_stated_rather_than_worked_around():
    framework = calculate(BASELINE, STACK)
    outcome = affinity_outcome([], "", framework)
    assert outcome.points == framework.points
    assert outcome.needs_human


# -- Bucket System --------------------------------------------------------------------------

ANCHORS = [
    {"label": "Presentational component driven by props", "pts": 3},
    {"label": "Async endpoint with DB integration", "pts": 5},
    {"label": "WebSocket endpoint with auth and full tests", "pts": 8},
]


@pytest.mark.parametrize("relative,expected", [
    ("smaller", 3), ("similar", 5), ("larger", 8),
])
def test_placement_steps_the_ladder_from_the_anchor(relative, expected):
    framework = calculate(BASELINE, STACK)
    outcome = bucket_outcome(ANCHORS[1], relative, "", framework, ANCHORS)
    assert outcome.points == expected


def test_the_ladder_is_clamped_at_both_ends():
    assert ladder_step(3, -5) == FIBONACCI_POINTS[0]
    assert ladder_step(34, 5) == FIBONACCI_POINTS[-1]


def test_an_anchor_can_be_named_by_paraphrase():
    assert match_anchor("async endpoint with DB", ANCHORS)["pts"] == 5
    assert match_anchor("WebSocket endpoint", ANCHORS)["pts"] == 8


def test_an_invented_anchor_is_refused_rather_than_substituted():
    """Quietly falling back to some middle anchor produces a confident bucket derived from a
    reference nobody chose — and the number that comes out looks exactly like a reasoned one."""
    assert match_anchor("Cross-layer change", ANCHORS) is None
    assert match_anchor("", ANCHORS) is None


def test_the_overlap_threshold_is_what_separates_paraphrase_from_invention():
    assert 0 < ANCHOR_OVERLAP < 1


def test_an_unusable_comparison_falls_back_to_the_arithmetic():
    framework = calculate(BASELINE, STACK)
    outcome = bucket_outcome({}, "", "not on the list", framework, ANCHORS)
    assert outcome.points == framework.points
    assert outcome.needs_human


# -- The techniques stay different from each other -------------------------------------------

def test_every_technique_is_described_for_the_picker():
    for technique_id in TECHNIQUE_IDS:
        technique = TECHNIQUE_BY_ID[technique_id]
        assert technique.name and technique.tagline and technique.best_for
        assert technique.precision and technique.speed and technique.model_calls
        # The published rule is the thing that makes the number checkable.
        assert len(technique.rule) > 80
        assert len(technique.how) > 80


def test_the_techniques_do_not_all_cost_the_same():
    """A picker whose options are indistinguishable is decoration."""
    assert len({TECHNIQUE_BY_ID[t].speed for t in TECHNIQUE_IDS}) > 1
    assert len({TECHNIQUE_BY_ID[t].precision for t in TECHNIQUE_IDS}) > 1


def test_a_technique_can_diverge_from_the_factor_arithmetic():
    """A second opinion that can only ever agree is not a second opinion."""
    framework = calculate(BASELINE, STACK)
    assert tshirt_outcome("XXL", "", framework).divergence != 0
    assert bucket_outcome(ANCHORS[0], "smaller", "", framework, ANCHORS).divergence != 0


def test_the_facilitator_note_says_what_actually_happened():
    votes = [
        seat("PRODUCT_DOMAIN", requirements_clarity=1, documentation_knowledge_transfer=1),
        seat("SECURITY_COMPLIANCE", security_review=5, regulatory_compliance=5),
    ]
    outcome, _, _ = poker_outcome(votes, BASELINE, STACK, rounds=2)
    note = facilitator_note(outcome)
    assert "Planning Poker" in note and "points" in note
    assert "ladder step" in note


def test_an_absent_seat_is_named_as_absent():
    """An inferred dimension must never be presented as somebody's first-hand judgement."""
    absent = seat("FRONTEND", frontend_effort=3)
    absent.inferred = True
    outcome, _, _ = poker_outcome([absent], BASELINE, STACK, rounds=1)
    assert "did not answer" in facilitator_note(outcome)
