"""The five estimation techniques, run as a squad rather than as a single opinion.

The organising rule is the same one the rest of this product is built on, and every technique
here obeys it: **the squad votes, the code counts.** A model supplies judgement — what this
story asks of the frontend, whether the data change is reversible, which risk deserves a dot —
and published arithmetic turns those judgements into a number. Nothing here lets a model pick a
story-point value directly, because the moment it can, the scorecard stops being something a
reader can replay by hand and becomes a number with an explanation attached to it afterwards.

That constraint is also what makes the simulation honest. A real Planning Poker round is not
nine people guessing a number; it is nine people who each own part of the work, saying what
their part costs, disagreeing, and converging. Cards, spread, a second round, a facilitator's
call — all of that is mechanism, and mechanism is exactly the part that can be implemented
faithfully in code. What the model contributes is the thing only a domain judgement can supply:
what the story actually asks of *my* discipline.

Each technique keeps its real trade-off rather than being levelled into the others. Planning
Poker is precise and slow because it asks every discipline. T-shirt sizing is one pass and
coarse. The bucket system compares against anchors instead of deriving from factors. A tool
that made them all cost the same and return the same answer would not be offering five
techniques; it would be offering one, five times.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from backend.estimation_framework import (
    FACTOR_BY_ID,
    FACTOR_IDS,
    FIBONACCI_POINTS,
    Calculation,
    StackProfile,
    calculate,
)

TechniqueId = Literal["planning_poker", "tshirt", "dot_voting", "affinity", "bucket"]

TECHNIQUE_IDS: tuple[str, ...] = (
    "planning_poker", "tshirt", "dot_voting", "affinity", "bucket",
)
DEFAULT_TECHNIQUE = "planning_poker"


# ------------------------------------------------------------------------------------------
# The squad
# ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SquadMember:
    """One discipline at the table, and the dimensions it is answerable for.

    Ownership is exclusive on purpose. Two engineers can argue about the integration surface,
    and in a real room they do — but when the estimate is written down, one of them owned the
    call. Shared ownership here would mean the assembled scorecard had two candidate values for
    a factor and a tie-break nobody published, which is precisely the kind of hidden arithmetic
    this module exists to avoid.
    """

    role: str
    label: str
    #: The everyday name for this seat, as a team would say it.
    discipline: str
    owns: tuple[str, ...]
    #: What this member is asked to judge, in their own terms. Goes into their prompt.
    charter: str


SQUAD: tuple[SquadMember, ...] = (
    SquadMember(
        "PRODUCT_DOMAIN", "Product / functional", "functional",
        ("requirements_clarity", "documentation_knowledge_transfer"),
        "whether the story says what it wants clearly enough to build, and what has to be "
        "written down for anyone else to pick it up",
    ),
    SquadMember(
        "ARCHITECTURE", "Architecture", "architecture",
        ("technical_complexity", "integration_surface", "reversibility"),
        "how hard the change is technically, how many systems it touches, and how easily it "
        "could be undone if it goes wrong",
    ),
    SquadMember(
        "FRONTEND", "Frontend", "frontend",
        ("frontend_effort",),
        "the user-interface work: screens, states, responsiveness, accessibility",
    ),
    SquadMember(
        "BACKEND", "Backend", "backend",
        ("backend_effort", "performance_scalability"),
        "the server-side work, and whether it holds up under the load it will actually see",
    ),
    SquadMember(
        "DATA_MIGRATION", "Data / migration", "data",
        ("data_model_change",),
        "schema changes, migrations, and backfills",
    ),
    SquadMember(
        "TEST_QUALITY", "Test / automation", "automation testing",
        ("test_effort",),
        "what has to be automated to trust this: unit, integration, end-to-end, regression",
    ),
    SquadMember(
        "SECURITY_COMPLIANCE", "Security / compliance", "security",
        ("security_review", "regulatory_compliance"),
        "authorisation, sensitive data, audit trails, and any regulation that applies",
    ),
    SquadMember(
        "DEVOPS_SRE", "DevOps / SRE", "platform",
        ("observability_operations", "dod_overhead"),
        "how this is released, monitored and operated, and what the definition of done costs",
    ),
    SquadMember(
        "DELIVERY_RISK", "Delivery risk", "delivery",
        ("cross_team_dependency", "uncertainty"),
        "what this depends on outside the team, and what nobody has thought of yet",
    ),
)

SQUAD_BY_ROLE = {member.role: member for member in SQUAD}
#: factor id -> the member answerable for it. Every factor has exactly one; see `SquadMember`.
OWNER_OF: dict[str, SquadMember] = {
    factor: member for member in SQUAD for factor in member.owns
}


def squad_for(story_text: str, routed_roles: list[str] | None = None) -> list[SquadMember]:
    """Who is actually in the room for this story.

    A real refinement session does not summon nine specialists for a copy change. The routed
    roles come from the existing specialist router; anyone not routed still has their factors
    represented, they simply do not get their own model call — the baseline scorecard stands in
    for the seat nobody needed to fill.
    """
    if not routed_roles:
        return list(SQUAD)
    wanted = set(routed_roles)
    # The functional seat is never empty: somebody always has to say what the story asks for.
    wanted.add("PRODUCT_DOMAIN")
    chosen = [member for member in SQUAD if member.role in wanted]
    return chosen or list(SQUAD)


# ------------------------------------------------------------------------------------------
# The techniques, as data
# ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Technique:
    id: str
    name: str
    #: One line, for the picker.
    tagline: str
    precision: str
    speed: str
    best_for: str
    #: How it works, in the interface's own words.
    how: str
    #: The published rule that turns the squad's judgement into a number.
    rule: str
    #: Roughly how many model calls a run costs. On a CPU model this is the wall clock.
    model_calls: str


TECHNIQUES: tuple[Technique, ...] = (
    Technique(
        "planning_poker", "Planning Poker",
        "Every discipline estimates independently, then the room converges.",
        "High", "Medium",
        "Detailed sprint planning, item by item",
        "Each discipline scores only the dimensions it owns, without seeing anyone else's "
        "answer — the same reason real cards are played face down. The cards are revealed "
        "together, the spread is measured, and a wide spread triggers a second round in which "
        "the outliers see the reasoning they disagreed with.",
        "A member's card is the story sized as if all of it were as demanding as their own "
        "part — which is what a person actually plays, since what you know best dominates your "
        "guess about the rest. Cards only ever measure disagreement. The number the team takes "
        "away comes from the final scorecard, which takes every factor from whoever owns it, "
        "with §9 mapping the total. A spread of two ladder steps or more forces a second "
        "round; still two or more after that, and it goes to a person.",
        "one per seat, plus a second round only for the outliers",
    ),
    Technique(
        "tshirt", "T-Shirt Sizing",
        "One pass, one size, for backlogs nobody has read yet.",
        "Low", "High",
        "Early epic sizing and high-level prioritisation",
        "The story is placed in a size bucket on perceived effort alone — no factor scoring, "
        "no discussion. This is the technique for a backlog of eighty items where the useful "
        "question is which ten are large, not whether one of them is a 5 or an 8.",
        "The size maps to points through a published table (XS 3, S 5, M 8, L 13, XL 21, "
        "XXL 34). The factor arithmetic still runs alongside it, and any disagreement between "
        "the two is reported rather than reconciled — that gap is the finding.",
        "one",
    ),
    Technique(
        "dot_voting", "Dot Voting",
        "Each discipline spends its dots on what will actually hurt.",
        "Low–Medium", "Fast",
        "Surfacing the risk everyone can see and nobody wrote down",
        "Every member is given a small, fixed number of dots and places them on the dimensions "
        "they believe will dominate the work. Scarcity is the mechanism: three dots force a "
        "ranking in a way that asking someone to rate sixteen things does not.",
        "A dimension carrying dots from two thirds of the squad is raised to at least 4; one "
        "carrying a dot from every member is raised to 5. Dots only ever raise a score — the "
        "technique surfaces concentration of concern, and silence is not evidence of ease.",
        "one per seat, and each is short",
    ),
    Technique(
        "affinity", "Affinity Mapping",
        "Size by resemblance to work already delivered.",
        "Medium", "Slow–Medium",
        "Large breakdowns, and teams with estimation history",
        "The story is grouped with past stories of similar shape rather than scored from "
        "scratch. Teams are far better at 'this is like that one' than at absolute sizing, and "
        "the comparison carries the actual outcome of the work it resembles.",
        "Comparison is on all sixteen factor scores, not shared vocabulary. The nearest cluster "
        "lends its points when the match is strong; a weak match is reported as weak and the "
        "factor arithmetic stands instead of being overridden by a coincidence.",
        "one, plus the deterministic comparison",
    ),
    Technique(
        "bucket", "Bucket System",
        "Place it against the anchors and move on.",
        "Medium", "Fast",
        "Bulk estimation of a large backlog",
        "The story is compared against calibration anchors — reference stories already sized on "
        "this stack — and dropped into the bucket it belongs beside. No debate over whether "
        "something is a 5 or an 8 when the useful distinction is 5 versus 21.",
        "The squad places the story relative to one anchor: larger, similar, or smaller. The "
        "bucket is the anchor's band, stepped one rung up or down the ladder accordingly.",
        "one",
    ),
)

TECHNIQUE_BY_ID = {item.id: item for item in TECHNIQUES}


# ------------------------------------------------------------------------------------------
# Shared vocabulary
# ------------------------------------------------------------------------------------------

def ladder_index(points: int) -> int:
    """Where a point value sits on the modified Fibonacci ladder."""
    return FIBONACCI_POINTS.index(points) if points in FIBONACCI_POINTS else 0


def ladder_step(points: int, steps: int) -> int:
    """Move up or down the ladder, clamped at both ends."""
    index = max(0, min(len(FIBONACCI_POINTS) - 1, ladder_index(points) + steps))
    return FIBONACCI_POINTS[index]


def spread_of(cards: list[int]) -> int:
    """Disagreement measured in ladder steps, not in points.

    The gap between 21 and 34 is thirteen points and one step; between 3 and 5 it is two points
    and also one step. Measuring in points would make every disagreement about a large story
    look like a crisis and every disagreement about a small one look like agreement.
    """
    if not cards:
        return 0
    indices = [ladder_index(item) for item in cards]
    return max(indices) - min(indices)


#: A spread this wide means the squad is not looking at the same story. Matches EAGLE's
#: `DISPUTE_SPREAD`, which measures the same thing on factor scores rather than on cards.
CONSENSUS_SPREAD = 1


class MemberVote(BaseModel):
    """One seat's contribution, whatever the technique asked for."""

    role: str
    label: str
    discipline: str
    owns: list[str] = Field(default_factory=list)
    #: Populated by techniques that produce a card (Planning Poker).
    points: int | None = None
    #: The member's scores for the dimensions they own.
    scores: dict[str, int] = Field(default_factory=dict)
    #: Populated by dot voting.
    dots: list[str] = Field(default_factory=list)
    reasoning: str = ""
    #: True when this seat's model call failed or was not run and the baseline stood in.
    inferred: bool = False
    #: Set on the second round, so a changed card is visible as a change.
    revised_from: int | None = None


class TechniqueOutcome(BaseModel):
    """What a technique produced, in a shape the interface can render for any of the five."""

    technique: str
    name: str
    #: The number the technique arrived at.
    points: int
    #: How it got there, in one sentence a reader can check against `rule`.
    verdict: str
    votes: list[MemberVote] = Field(default_factory=list)
    #: Ladder-step disagreement across the squad, where the technique produces cards.
    spread: int = 0
    rounds: int = 1
    consensus: Literal["unanimous", "consensus", "converged", "unresolved", "n/a"] = "n/a"
    #: The framework's own number, always computed, so a technique can be checked against it.
    framework_points: int = 0
    #: Ladder steps between this technique and the framework arithmetic. Reported, never hidden.
    divergence: int = 0
    #: Technique-specific detail: the size, the dot tally, the anchor, the cluster.
    detail: dict = Field(default_factory=dict)
    #: Every rule that fired, in order, so the result can be replayed by hand.
    steps: list[str] = Field(default_factory=list)
    needs_human: bool = False


# ------------------------------------------------------------------------------------------
# 1. Planning Poker
# ------------------------------------------------------------------------------------------

def card_for(member: MemberVote, baseline: dict[str, int], stack: StackProfile) -> int:
    """What this seat would play: the story sized as if all of it were like their part.

    The obvious implementation — their dimensions on top of the baseline for everything else —
    is wrong, and wrong in a way that quietly disables the technique. A seat owns one to three
    of sixteen factors, so overriding them moves the base sum by a handful of points and almost
    never crosses a band edge. Every card comes back identical, the spread is always zero, the
    second round never fires, and what is left is a scoring pass wearing Planning Poker's
    clothes.

    It is also not what a person does. Someone holding a card is not reporting their slice;
    they are sizing the whole story from where they sit, and what they know best dominates that
    guess. A frontend engineer looking at four new screens plays high without knowing what the
    migration costs. So the card projects the seat's own assessment across the whole story: *if
    the rest of this is as demanding as my part, this is what it costs.*

    The card is only ever a disagreement signal. The number the team takes away comes from
    `assemble_scorecard`, where each dimension is owned by whoever actually knows.
    """
    owned = [value for key, value in member.scores.items() if key in FACTOR_IDS]
    if not owned:
        return calculate(dict(baseline), stack).points
    # Round half up: a seat sitting between two levels is expressing the higher concern, and
    # rounding their card down would systematically make the room look more relaxed than it is.
    projected = int((sum(owned) / len(owned)) + 0.5)
    scores = {factor: projected for factor in FACTOR_IDS}
    scores.update({key: value for key, value in member.scores.items() if key in FACTOR_IDS})
    return calculate(scores, stack).points


def assemble_scorecard(
    votes: list[MemberVote], baseline: dict[str, int]
) -> tuple[dict[str, int], dict[str, str]]:
    """The squad's scorecard: every factor taken from the member who owns it.

    Not an average. Averaging the frontend engineer's guess at the migration with the data
    engineer's knowledge of it produces a number neither of them would defend, and the whole
    point of having a squad is that somebody in the room actually knows.
    """
    scores = dict(baseline)
    attribution: dict[str, str] = {factor: "baseline" for factor in FACTOR_IDS}
    for vote in votes:
        for factor, value in vote.scores.items():
            if factor in FACTOR_IDS and OWNER_OF.get(factor, SQUAD[0]).role == vote.role:
                scores[factor] = value
                attribution[factor] = vote.role
    return scores, attribution


def outliers(votes: list[MemberVote]) -> list[str]:
    """The seats holding the extreme cards — the only ones a second round needs to hear from.

    Re-polling the whole room when two people disagree is how a fifteen-minute estimate becomes
    an hour, and on a CPU model it is minutes of wall clock for cards that are not going to
    move.
    """
    carded = [item for item in votes if item.points is not None]
    if len(carded) < 2:
        return []
    lowest = min(item.points for item in carded)  # type: ignore[type-var]
    highest = max(item.points for item in carded)  # type: ignore[type-var]
    if ladder_index(highest) - ladder_index(lowest) < 2:
        return []
    return [item.role for item in carded if item.points in {lowest, highest}]


def poker_outcome(
    votes: list[MemberVote],
    baseline: dict[str, int],
    stack: StackProfile,
    rounds: int,
) -> tuple[TechniqueOutcome, Calculation, dict[str, int]]:
    """Reveal, measure, and let the owners' scorecard decide."""
    steps: list[str] = []
    cards = [item.points for item in votes if item.points is not None]
    spread = spread_of(cards)  # type: ignore[arg-type]
    steps.append(
        f"{len(cards)} card(s) revealed together: "
        + ", ".join(f"{item.label} {item.points}" for item in votes if item.points is not None)
    )
    if not cards:
        consensus = "n/a"
        steps.append("No seat produced a card; the baseline scorecard stands alone.")
    elif spread == 0:
        consensus = "unanimous"
        steps.append("Every card matched — unanimous, no second round.")
    elif spread <= CONSENSUS_SPREAD:
        consensus = "consensus"
        steps.append(
            f"Spread of {spread} ladder step is within the consensus threshold "
            f"({CONSENSUS_SPREAD}); no second round."
        )
    elif rounds > 1:
        consensus = "converged"
        steps.append(
            f"Spread of {spread} steps after the second round. The outliers heard the "
            "reasoning they disagreed with and re-played."
        )
    else:
        consensus = "unresolved"
        steps.append(f"Spread of {spread} steps and no second round was possible.")

    scores, attribution = assemble_scorecard(votes, baseline)
    owned = sum(1 for value in attribution.values() if value != "baseline")
    steps.append(
        f"Final scorecard assembled from the owning seat for {owned} of {len(FACTOR_IDS)} "
        f"factors; the remaining {len(FACTOR_IDS) - owned} kept the baseline score."
    )
    calculation = calculate(scores, stack)
    steps.append(
        f"§9 maps the adjusted score {calculation.adjusted_score} to "
        f"{calculation.points} points (band {calculation.band})."
    )
    unresolved = consensus == "unresolved" or (consensus == "converged" and spread >= 2)
    if unresolved:
        steps.append(
            "The room did not converge. The number stands, and it is flagged for a person — "
            "an unresolved spread is information, not a failure to be averaged away."
        )
    return (
        TechniqueOutcome(
            technique="planning_poker",
            name="Planning Poker",
            points=calculation.points,
            verdict=(
                f"{len(cards)} discipline(s) played; spread {spread} step(s); "
                f"the owners' scorecard settles at {calculation.points} points."
            ),
            votes=votes,
            spread=spread,
            rounds=rounds,
            consensus=consensus,
            framework_points=calculation.points,
            divergence=0,
            detail={
                "attribution": attribution,
                "cards": {item.role: item.points for item in votes if item.points is not None},
                "consensus_threshold": CONSENSUS_SPREAD,
            },
            steps=steps,
            needs_human=unresolved,
        ),
        calculation,
        scores,
    )


# ------------------------------------------------------------------------------------------
# 2. T-Shirt Sizing
# ------------------------------------------------------------------------------------------

#: The published mapping. Configurable in principle, fixed here so a reader can check the
#: arithmetic without first discovering what somebody set it to.
TSHIRT_POINTS: dict[str, int] = {"XS": 3, "S": 5, "M": 8, "L": 13, "XL": 21, "XXL": 34}
TSHIRT_SIZES: tuple[str, ...] = tuple(TSHIRT_POINTS)


def tshirt_outcome(
    size: str, reasoning: str, framework: Calculation, inferred: bool = False
) -> TechniqueOutcome:
    normalised = (size or "").strip().upper()
    if normalised not in TSHIRT_POINTS:
        # Rather than guess at what "medium-large" meant, fall back to the arithmetic and say
        # that is what happened.
        return TechniqueOutcome(
            technique="tshirt", name="T-Shirt Sizing",
            points=framework.points,
            verdict=(
                f"No usable size came back{f' ({size})' if size else ''}, so the factor "
                "arithmetic stands on its own."
            ),
            framework_points=framework.points,
            detail={"size": None, "mapping": TSHIRT_POINTS, "reasoning": reasoning},
            steps=["The sizing pass did not return one of XS, S, M, L, XL, XXL."],
            needs_human=True,
        )
    points = TSHIRT_POINTS[normalised]
    divergence = ladder_index(points) - ladder_index(framework.points)
    steps = [
        f"Sized {normalised} on perceived effort, without factor scoring.",
        f"Published mapping puts {normalised} at {points} points.",
        f"The factor arithmetic independently reached {framework.points} points "
        f"(adjusted score {framework.adjusted_score}).",
    ]
    if divergence:
        steps.append(
            f"The two disagree by {abs(divergence)} ladder step(s). That gap is the finding: a "
            "quick size and a scored one part company where the story hides its work."
        )
    else:
        steps.append("Both methods agree, which is the strongest signal this technique offers.")
    return TechniqueOutcome(
        technique="tshirt", name="T-Shirt Sizing",
        points=points,
        verdict=(
            f"Sized {normalised} → {points} points"
            + (f", against {framework.points} from the factor arithmetic."
               if divergence else ", matching the factor arithmetic.")
        ),
        votes=[MemberVote(
            role="SQUAD", label="Whole squad", discipline="all", points=points,
            reasoning=reasoning, inferred=inferred,
        )],
        framework_points=framework.points,
        divergence=divergence,
        detail={"size": normalised, "mapping": TSHIRT_POINTS, "reasoning": reasoning},
        steps=steps,
        needs_human=abs(divergence) >= 2,
    )


# ------------------------------------------------------------------------------------------
# 3. Dot Voting
# ------------------------------------------------------------------------------------------

#: Dots per member. Small on purpose — scarcity is the entire mechanism. Ask someone to rate
#: sixteen things and they rate them all; give them three dots and they have to decide.
DOTS_PER_MEMBER = 3


def dot_tally(votes: list[MemberVote]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for vote in votes:
        for factor in vote.dots[:DOTS_PER_MEMBER]:
            if factor in FACTOR_IDS:
                tally[factor] = tally.get(factor, 0) + 1
    return tally


def apply_dots(
    baseline: dict[str, int], tally: dict[str, int], squad_size: int
) -> tuple[dict[str, int], list[str]]:
    """Raise the dimensions the room agrees will hurt. Dots never lower a score.

    Silence is not evidence of ease — nobody spends a scarce dot saying a thing is easy — so a
    factor with no dots keeps whatever the baseline gave it.
    """
    scores = dict(baseline)
    steps: list[str] = []
    majority = max(2, (squad_size * 2 + 2) // 3)
    for factor, count in sorted(tally.items(), key=lambda item: (-item[1], item[0])):
        label = FACTOR_BY_ID[factor].label
        if count >= squad_size and scores[factor] < 5:
            steps.append(f"{label}: {count}/{squad_size} dots — every seat. Raised to 5.")
            scores[factor] = 5
        elif count >= majority and scores[factor] < 4:
            steps.append(
                f"{label}: {count}/{squad_size} dots — at or above the two-thirds threshold "
                f"({majority}). Raised to 4."
            )
            scores[factor] = 4
        else:
            steps.append(f"{label}: {count}/{squad_size} dots — below threshold, unchanged.")
    return scores, steps


def dot_outcome(
    votes: list[MemberVote], tally: dict[str, int], calculation: Calculation, steps: list[str]
) -> TechniqueOutcome:
    top = sorted(tally.items(), key=lambda item: (-item[1], item[0]))[:3]
    return TechniqueOutcome(
        technique="dot_voting", name="Dot Voting",
        points=calculation.points,
        verdict=(
            f"{sum(tally.values())} dot(s) placed; concentration on "
            + (", ".join(FACTOR_BY_ID[f].label for f, _ in top) or "nothing in particular")
            + f" settles the estimate at {calculation.points} points."
        ),
        votes=votes,
        framework_points=calculation.points,
        detail={
            "tally": tally,
            "dots_per_member": DOTS_PER_MEMBER,
            "heatmap": [
                {"factor": f, "label": FACTOR_BY_ID[f].label, "dots": c} for f, c in
                sorted(tally.items(), key=lambda item: (-item[1], item[0]))
            ],
        },
        steps=steps + [
            f"§9 maps the adjusted score {calculation.adjusted_score} to "
            f"{calculation.points} points."
        ],
        needs_human=not tally,
    )


# ------------------------------------------------------------------------------------------
# 4. Affinity Mapping
# ------------------------------------------------------------------------------------------

#: Below this similarity the nearest neighbour is a coincidence, not an anchor.
AFFINITY_STRONG = 0.72


def affinity_outcome(
    matches: list[dict], reasoning: str, framework: Calculation
) -> TechniqueOutcome:
    """Group with the work it resembles; refuse to be anchored by a weak resemblance."""
    steps: list[str] = []
    if not matches:
        steps.append(
            "No past stories were available to group against, so there is no cluster to "
            "inherit from and the factor arithmetic stands."
        )
        return TechniqueOutcome(
            technique="affinity", name="Affinity Mapping",
            points=framework.points,
            verdict=(
                "No estimation history to group against; the estimate is the factor "
                f"arithmetic's {framework.points} points."
            ),
            framework_points=framework.points,
            detail={"cluster": [], "reasoning": reasoning, "threshold": AFFINITY_STRONG},
            steps=steps,
            needs_human=True,
        )
    best = max(matches, key=lambda item: float(item.get("similarity") or 0))
    similarity = float(best.get("similarity") or 0)
    cluster = [
        item for item in matches
        if float(item.get("similarity") or 0) >= AFFINITY_STRONG
    ]
    steps.append(
        f"Compared against {len(matches)} past stor(y/ies) on all sixteen factor scores. "
        f"Closest is \"{best.get('title', 'untitled')}\" at {similarity:.0%} similarity."
    )
    if not cluster:
        steps.append(
            f"Nothing reached the {AFFINITY_STRONG:.0%} threshold, so the resemblance is "
            "reported as weak and is not used as an anchor. The factor arithmetic stands."
        )
        points = framework.points
    else:
        values = sorted(int(item.get("points") or 0) for item in cluster)
        points = values[len(values) // 2]
        steps.append(
            f"{len(cluster)} stor(y/ies) cleared the threshold and form the cluster; their "
            f"median delivered size is {points} points."
        )
    divergence = ladder_index(points) - ladder_index(framework.points)
    if divergence:
        steps.append(
            f"The cluster and the factor arithmetic differ by {abs(divergence)} ladder "
            f"step(s) ({points} against {framework.points}); both are reported."
        )
    return TechniqueOutcome(
        technique="affinity", name="Affinity Mapping",
        points=points,
        verdict=(
            f"Grouped with {len(cluster)} similar stor(y/ies) at {points} points."
            if cluster else
            f"No strong resemblance found; the factor arithmetic's {points} points stands."
        ),
        framework_points=framework.points,
        divergence=divergence,
        detail={
            "cluster": cluster, "nearest": best, "reasoning": reasoning,
            "threshold": AFFINITY_STRONG, "compared": len(matches),
        },
        steps=steps,
        needs_human=not cluster,
    )


# ------------------------------------------------------------------------------------------
# 5. Bucket System
# ------------------------------------------------------------------------------------------

RELATIVE = ("smaller", "similar", "larger")

#: How much of an anchor's wording a paraphrase has to share before it counts as the same
#: anchor. A model rarely quotes a label back verbatim, but it should not be allowed to invent
#: one either.
ANCHOR_OVERLAP = 0.5


def match_anchor(named: str, anchors: list[dict]) -> dict | None:
    """The anchor the squad meant, or None if it named something that is not on the list.

    None is a real answer here. Substituting a plausible anchor when the named one does not
    exist yields a bucket that reads exactly like a reasoned one and was not — the same failure
    the repository path check refuses, in a different costume.
    """
    wanted = (named or "").strip().lower()
    if not wanted:
        return None
    for item in anchors:
        label = str(item.get("label", "")).lower()
        if wanted == label or (len(wanted) >= 6 and (wanted in label or label in wanted)):
            return item
    words = {w for w in re.findall(r"[a-z0-9]{3,}", wanted)}
    if not words:
        return None
    best, score = None, 0.0
    for item in anchors:
        label_words = set(re.findall(r"[a-z0-9]{3,}", str(item.get("label", "")).lower()))
        if not label_words:
            continue
        overlap = len(words & label_words) / len(words)
        if overlap > score:
            best, score = item, overlap
    return best if score >= ANCHOR_OVERLAP else None


def bucket_outcome(
    anchor: dict, relative: str, reasoning: str, framework: Calculation, anchors: list[dict]
) -> TechniqueOutcome:
    """One comparison against one anchor, then the ladder does the rest."""
    steps: list[str] = []
    anchor_points = int(anchor.get("pts") or anchor.get("points") or 0)
    call = (relative or "").strip().lower()
    if anchor_points not in FIBONACCI_POINTS or call not in RELATIVE:
        steps.append(
            "The comparison did not name a usable anchor and direction, so there is no bucket "
            "to drop into and the factor arithmetic stands."
        )
        return TechniqueOutcome(
            technique="bucket", name="Bucket System",
            points=framework.points,
            verdict=f"No usable comparison; the factor arithmetic's "
                    f"{framework.points} points stands.",
            framework_points=framework.points,
            detail={"anchors": anchors, "reasoning": reasoning},
            steps=steps, needs_human=True,
        )
    move = {"smaller": -1, "similar": 0, "larger": 1}[call]
    points = ladder_step(anchor_points, move)
    steps.append(
        f"Compared against the anchor \"{anchor.get('label', 'anchor')}\" at "
        f"{anchor_points} points."
    )
    steps.append(
        f"Judged {call}, so the bucket is {'the same rung' if move == 0 else 'one rung ' + ('up' if move > 0 else 'down')}"
        f" the ladder: {points} points."
    )
    divergence = ladder_index(points) - ladder_index(framework.points)
    if divergence:
        steps.append(
            f"The bucket and the factor arithmetic differ by {abs(divergence)} ladder step(s) "
            f"({points} against {framework.points}); both are reported."
        )
    return TechniqueOutcome(
        technique="bucket", name="Bucket System",
        points=points,
        verdict=f"Bucketed {call} than a {anchor_points}-point anchor → {points} points.",
        framework_points=framework.points,
        divergence=divergence,
        detail={
            "anchor": anchor, "relative": call, "reasoning": reasoning, "anchors": anchors,
            "buckets": list(FIBONACCI_POINTS),
        },
        steps=steps,
        needs_human=abs(divergence) >= 2,
    )


# ------------------------------------------------------------------------------------------
# The facilitator's note
# ------------------------------------------------------------------------------------------

def facilitator_note(outcome: TechniqueOutcome) -> str:
    """The sentence a facilitator would write in the ticket.

    Deterministic, because it is a statement of what happened rather than an opinion about it.
    A model narrating this would occasionally narrate something else.
    """
    parts = [f"{outcome.name}: {outcome.points} points."]
    if outcome.votes and outcome.spread:
        parts.append(
            f"The room spread {outcome.spread} ladder step(s) across "
            f"{len([v for v in outcome.votes if v.points is not None])} card(s)"
            + (f" over {outcome.rounds} rounds." if outcome.rounds > 1 else ".")
        )
    if outcome.divergence:
        parts.append(
            f"This technique and the factor arithmetic differ by {abs(outcome.divergence)} "
            f"step(s) — {outcome.points} against {outcome.framework_points}."
        )
    inferred = [item for item in outcome.votes if item.inferred]
    if inferred:
        parts.append(
            f"{len(inferred)} seat(s) did not answer and the baseline scorecard stood in for "
            "them; their dimensions are not first-hand judgements."
        )
    if outcome.needs_human:
        parts.append("A person should confirm this one.")
    return " ".join(parts)
