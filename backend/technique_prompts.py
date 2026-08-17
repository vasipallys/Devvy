"""What each seat at the table is actually asked, and the shape it answers in.

Every prompt here is deliberately small. The lesson the rest of this pipeline learned the hard
way is that a 1B model asked for sixteen scored objects in one response holds the shape and
loses the content — it answers "2" sixteen times. A squad sidesteps that by construction: the
frontend engineer is asked about one dimension, the security engineer about two. Nobody is
asked to hold the whole rubric, which is both how a real team works and the only way this size
of model produces judgement rather than filler.

None of these schemas contains a story-point field. The model is never given the opportunity to
name the answer; it supplies the judgement its seat is qualified for, and published arithmetic
does the rest.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.estimation_framework import FACTOR_BY_ID
from backend.estimation_techniques import (
    DOTS_PER_MEMBER,
    SquadMember,
    TSHIRT_SIZES,
)
from backend.harness import GROUNDING_CONTRACT_BRIEF


# ------------------------------------------------------------------------------------------
# Schemas
# ------------------------------------------------------------------------------------------

class SeatScore(BaseModel):
    factor: str = ""
    score: int = 0
    why: str = ""


class SeatVote(BaseModel):
    """One discipline's assessment of the dimensions it owns.

    `scores` is deliberately `list[Any]`, read by `seat_scores()` rather than by the validator.
    A small model asked for a list of `{factor, score, why}` will sometimes send
    `{"requirements_clarity": 4}` instead, or a bare list of factor names, and all three carry
    the judgement that was asked for. Rejecting the last two throws away a model call — minutes
    of CPU — over a shape the reader can perfectly well understand, and leaves the seat empty
    for no reason the user would accept.
    """

    scores: list[Any] | dict[str, Any] = Field(default_factory=list)
    #: One sentence the facilitator could read out. Not chain-of-thought: a conclusion.
    note: str = ""


def seat_scores(answer: "SeatVote", owned: set[str]) -> dict[str, str]:
    """The seat's scores for the dimensions it owns, from whichever shape came back.

    Returns factor -> reason for the ones that parsed; the caller reads the score from the
    same items. Anything for a dimension this seat does not own is dropped rather than
    honoured — a frontend engineer may have an opinion about the migration, but writing it
    into the estimate is somebody else's job.
    """
    found: dict[str, tuple[int, str]] = {}

    def take(factor: Any, score: Any, why: Any = "") -> None:
        key = str(factor or "").strip()
        if key not in owned:
            return
        try:
            value = int(str(score).strip())
        except (TypeError, ValueError):
            return
        if 1 <= value <= 5:
            found[key] = (value, str(why or "").strip())

    raw = answer.scores
    if isinstance(raw, dict):
        for factor, value in raw.items():
            if isinstance(value, dict):
                take(factor, value.get("score"), value.get("why"))
            else:
                take(factor, value)
    else:
        for item in raw or []:
            if isinstance(item, dict):
                take(item.get("factor"), item.get("score"), item.get("why"))
            elif hasattr(item, "factor"):
                take(item.factor, item.score, getattr(item, "why", ""))
    return {factor: f"{value}|{why}" for factor, (value, why) in found.items()}


class SizeVote(BaseModel):
    size: str = ""
    why: str = ""


class DotVote(BaseModel):
    """Which dimensions this seat spends its scarce dots on."""

    dots: list[str] = Field(default_factory=list)
    why: str = ""


class AnchorVote(BaseModel):
    anchor: str = ""
    relative: str = ""
    why: str = ""


class ClusterVote(BaseModel):
    why: str = ""


# ------------------------------------------------------------------------------------------
# Prompts
# ------------------------------------------------------------------------------------------

_SEAT_SYSTEM = (
    "You are one engineer in a refinement session, speaking only for your own discipline. "
    "You assess the work in front of you; you never compute story points — the team's "
    "published arithmetic does that. Answer only about the dimensions you are given."
)


def seat_prompt(member: SquadMember, story_block: str, round_two: str = "") -> tuple[str, str]:
    """(system, user) for one seat's turn. Short by design — see the module docstring."""
    dimensions = "\n".join(
        f"  {factor} — {FACTOR_BY_ID[factor].label}: {FACTOR_BY_ID[factor].description}\n"
        f"      1 means: {FACTOR_BY_ID[factor].low_anchor}\n"
        f"      5 means: {FACTOR_BY_ID[factor].high_anchor}"
        for factor in member.owns
    )
    system = (
        f"{_SEAT_SYSTEM}\n\nYou are the {member.label} engineer. You are answerable for "
        f"{member.charter}."
    )
    revisit = ""
    if round_two:
        revisit = (
            "\nSECOND ROUND. Your card was at one extreme of the room. Here is what the "
            f"others said:\n{round_two}\n"
            "Re-score your own dimensions in light of it. Holding your position is a "
            "legitimate answer — say why in your note if you do.\n"
        )
    user = (
        f"{story_block}\n\n"
        f"Score ONLY these dimensions, 1 to 5, from your discipline's point of view:\n"
        f"{dimensions}\n{revisit}\n"
        "Score what the story actually says. Where it says nothing about your area, score the "
        "uncertainty that silence creates rather than assuming the work is small — an "
        "unmentioned migration is not an absent one.\n\n"
        f"{GROUNDING_CONTRACT_BRIEF}"
    )
    return system, user


def seat_example(member: SquadMember) -> SeatVote:
    """A filled instance, never a schema. A model shown a schema returns the schema."""
    return SeatVote(
        scores=[
            SeatScore(
                factor=factor,
                score=3,
                why="what the story says about this, in one clause",
            )
            for factor in member.owns
        ],
        note="one sentence you would say out loud in the session",
    )


def size_prompt(story_block: str) -> tuple[str, str]:
    system = (
        "You are an experienced delivery lead sizing a backlog quickly. You give a T-shirt "
        "size on perceived effort and nothing else. You never give story points."
    )
    user = (
        f"{story_block}\n\n"
        f"Give one size from {', '.join(TSHIRT_SIZES)}.\n"
        "XS is a change one person finishes inside a day. XXL is work that should have been "
        "split before anyone tried to size it.\n"
        "Judge the whole story at a glance — this technique is deliberately not a scored "
        "breakdown. If the story is too vague to size, say so in `why` and choose the size "
        "that reflects the uncertainty rather than the optimistic reading.\n\n"
        f"{GROUNDING_CONTRACT_BRIEF}"
    )
    return system, user


def dot_prompt(member: SquadMember, story_block: str, catalogue: str) -> tuple[str, str]:
    system = (
        f"{_SEAT_SYSTEM}\n\nYou are the {member.label} engineer, answerable for "
        f"{member.charter}."
    )
    user = (
        f"{story_block}\n\n"
        f"You have {DOTS_PER_MEMBER} dots. Spend them on the dimensions you believe will "
        f"dominate the work on this story — from any part of the list, not only your own.\n"
        f"{catalogue}\n\n"
        f"Return at most {DOTS_PER_MEMBER} dimension ids. Spend fewer if fewer genuinely "
        "worry you; dots are not a quota. Name only what you would raise in the session.\n\n"
        f"{GROUNDING_CONTRACT_BRIEF}"
    )
    return system, user


def anchor_prompt(story_block: str, anchors: list[dict]) -> tuple[str, str]:
    listing = "\n".join(
        f"  {item.get('label', 'anchor')} — {item.get('pts')} points: "
        f"{item.get('detail') or item.get('description') or 'reference story'}"
        for item in anchors
    )
    system = (
        "You are placing a story into a bucket by comparing it with reference work already "
        "delivered on this stack. You never give story points; you name an anchor and say "
        "whether this story is smaller, similar, or larger."
    )
    user = (
        f"{story_block}\n\nReference anchors:\n{listing}\n\n"
        "Pick the ONE anchor this story most resembles in shape of work, then say whether it "
        "is `smaller`, `similar`, or `larger` than that anchor.\n"
        "Resemblance means the kind and breadth of work, not the subject matter: a payments "
        "change and a reporting change can be the same size.\n\n"
        f"{GROUNDING_CONTRACT_BRIEF}"
    )
    return system, user


def cluster_prompt(story_block: str, neighbours: list[dict]) -> tuple[str, str]:
    listing = "\n".join(
        f"  \"{item.get('title', 'untitled')}\" — delivered at {item.get('points')} points, "
        f"{float(item.get('similarity') or 0):.0%} similar on factor scores"
        for item in neighbours[:5]
    ) or "  (no past stories available)"
    system = (
        "You are grouping a new story with past work of similar shape. The similarity figures "
        "are already computed from factor scores; you explain whether the grouping is sound. "
        "You never give story points."
    )
    user = (
        f"{story_block}\n\nNearest past stories:\n{listing}\n\n"
        "In one or two sentences: is this story genuinely the same shape of work as those, or "
        "does the similarity look like a coincidence? Say which, and why.\n\n"
        f"{GROUNDING_CONTRACT_BRIEF}"
    )
    return system, user
