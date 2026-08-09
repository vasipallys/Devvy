"""Durable record of every completed estimate.

Jobs already persist a run, but a job is the wrong shape for this and the wrong lifetime:
it is keyed by execution rather than by story, and it is purged on a short retention so the
job table does not grow without bound.

An estimate is different. It is the artefact a team refers back to — to answer "what did we
say about this story", to compare a new story against what they actually sized before, and
to see whether their own scoring drifts. So it is stored separately, indexed by the fields
people search on, and it is not purged on a timer.

The full result payload is kept verbatim so a historical entry renders through exactly the
same view as a fresh one, including its calculation ledger. A stored estimate is evidence;
summarising it into a few columns would defeat the point.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Column, JSON
from sqlmodel import Field, Session, SQLModel, col, delete, func, select

from backend.auth import User  # noqa: F401 — registers the foreign-key target in metadata
from backend.db import utc_iso


def now() -> datetime:
    return datetime.now(timezone.utc)


class EstimateRecord(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
    owner_id: UUID | None = Field(default=None, foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=now, index=True)
    #: The run that produced this. Kept for traceability even after the job is purged.
    job_id: UUID | None = Field(default=None, index=True)

    title: str = Field(index=True)
    issue_key: str | None = Field(default=None, index=True)
    source: str = Field(default="manual", index=True)

    # Denormalised for listing, filtering, and calibration stats without loading every blob.
    points: int = Field(index=True)
    confidence: str = ""
    recommendation: str = Field(default="", index=True)
    base_sum: int = 0
    adjusted_score: int = 0
    band: str = ""
    frontend: str = "none"
    backend: str = "none"
    maturity_level: int = 3
    team_experience: int = 3
    model_scored: int = 0
    heuristic_filled: int = 0
    tldr: str = ""

    # What the team actually decided. The pipeline ends at "human decision required"; without
    # somewhere to put the answer, the loop never closes and calibration can only ever report
    # what was estimated, never whether the estimate was any good.
    decision: str | None = Field(default=None, index=True)
    decided_points: int | None = None
    decision_note: str | None = None
    decided_at: datetime | None = None
    #: Filled in after delivery. This is what turns history into calibration.
    actual_points: int | None = None

    #: The complete estimate payload, rendered by the same component as a live result.
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))


def record_summary(item: EstimateRecord) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "owner_id": str(item.owner_id) if item.owner_id else None,
        "created_at": utc_iso(item.created_at),
        "job_id": str(item.job_id) if item.job_id else None,
        "title": item.title,
        "issue_key": item.issue_key,
        "source": item.source,
        "points": item.points,
        "confidence": item.confidence,
        "recommendation": item.recommendation,
        "base_sum": item.base_sum,
        "adjusted_score": item.adjusted_score,
        "band": item.band,
        "frontend": item.frontend,
        "backend": item.backend,
        "maturity_level": item.maturity_level,
        "team_experience": item.team_experience,
        "model_scored": item.model_scored,
        "heuristic_filled": item.heuristic_filled,
        "tldr": item.tldr,
        "decision": item.decision,
        "decided_points": item.decided_points,
        "decision_note": item.decision_note,
        "decided_at": utc_iso(item.decided_at),
        "actual_points": item.actual_points,
    }


#: What a team may do with a recommendation. `accept` takes the recommended number as-is;
#: the rest are the framework's own escalation options plus an explicit override.
DECISIONS = ("accept", "override", "spike", "decompose")


def record_decision(
    engine,
    record_id: UUID,
    decision: str,
    *,
    points: int | None = None,
    note: str = "",
    actual_points: int | None = None,
    owner_id: UUID | None = None,
) -> dict[str, Any] | None:
    """Record the human decision on an estimate. Returns the updated record, or None.

    The decision is deliberately not validated against the recommendation: a team is allowed
    to accept a number the framework wanted decomposed, and recording that disagreement is
    more useful than preventing it. What matters is that the choice is captured.
    """
    if decision not in DECISIONS:
        raise ValueError(f"Decision must be one of {', '.join(DECISIONS)}.")
    with Session(engine) as session:
        item = session.get(EstimateRecord, record_id)
        if item is None or (owner_id is not None and item.owner_id != owner_id):
            return None
        item.decision = decision
        # An override carries its own number; accepting keeps the recommended one.
        item.decided_points = points if decision == "override" else item.points
        item.decision_note = (note or "").strip()[:1000] or None
        item.decided_at = now()
        if actual_points is not None:
            item.actual_points = actual_points
        session.add(item)
        session.commit()
        session.refresh(item)
        return {**record_summary(item), "result": item.result}


def save_estimate(
    engine,
    result: dict[str, Any],
    job_id: UUID | None = None,
    owner_id: UUID | None = None,
) -> EstimateRecord:
    """Persist one completed estimate. Tolerates partial payloads rather than raising.

    History is a side effect of estimating; a schema surprise here must never fail the
    estimate the user is waiting for.
    """
    story = result.get("story") or {}
    stack = result.get("stack") or {}
    calculation = result.get("calculation") or {}
    provenance = (result.get("evidence") or {}).get("scoring_provenance") or {}
    record = EstimateRecord(
        owner_id=owner_id,
        job_id=job_id,
        title=str(story.get("title") or "Untitled story")[:500],
        issue_key=(str(story["key"])[:50] if story.get("key") else None),
        source=str(story.get("source") or "manual"),
        points=int(result.get("points") or 0),
        confidence=str(result.get("confidence") or ""),
        recommendation=str(result.get("recommendation") or ""),
        base_sum=int(calculation.get("base_sum") or 0),
        adjusted_score=int(calculation.get("adjusted_score") or 0),
        band=str(calculation.get("band") or ""),
        frontend=str(stack.get("frontend") or "none"),
        backend=str(stack.get("backend") or "none"),
        maturity_level=int(stack.get("maturity_level") or 3),
        team_experience=int(stack.get("team_experience") or 3),
        model_scored=int(provenance.get("model_scored") or 0),
        heuristic_filled=int(provenance.get("heuristic_filled") or 0),
        tldr=str(result.get("tldr") or "")[:500],
        result=result,
    )
    with Session(engine) as session:
        session.add(record)
        session.commit()
        session.refresh(record)
    return record


def list_estimates(
    engine,
    *,
    query: str = "",
    source: str | None = None,
    points: int | None = None,
    recommendation: str | None = None,
    limit: int = 50,
    offset: int = 0,
    owner_id: UUID | None = None,
) -> dict[str, Any]:
    """Search history, newest first, with the total so the UI can paginate honestly."""
    with Session(engine) as session:
        statement = select(EstimateRecord)
        counter = select(func.count()).select_from(EstimateRecord)
        clauses = []
        if owner_id is not None:
            clauses.append(col(EstimateRecord.owner_id) == owner_id)
        if query.strip():
            pattern = f"%{query.strip()}%"
            clauses.append(
                col(EstimateRecord.title).ilike(pattern)
                | col(EstimateRecord.issue_key).ilike(pattern)
                | col(EstimateRecord.tldr).ilike(pattern)
            )
        if source:
            clauses.append(col(EstimateRecord.source) == source)
        if points:
            clauses.append(col(EstimateRecord.points) == points)
        if recommendation:
            clauses.append(col(EstimateRecord.recommendation) == recommendation)
        for clause in clauses:
            statement = statement.where(clause)
            counter = counter.where(clause)
        total = session.exec(counter).one()
        rows = session.exec(
            statement.order_by(col(EstimateRecord.created_at).desc())
            .offset(max(0, offset))
            .limit(max(1, min(limit, 200)))
        ).all()
        return {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "items": [record_summary(item) for item in rows],
        }


def get_estimate(
    engine, record_id: UUID, owner_id: UUID | None = None
) -> dict[str, Any] | None:
    with Session(engine) as session:
        item = session.get(EstimateRecord, record_id)
        if item is None or (owner_id is not None and item.owner_id != owner_id):
            return None
        return {**record_summary(item), "result": item.result}


def delete_estimate(engine, record_id: UUID, owner_id: UUID | None = None) -> bool:
    with Session(engine) as session:
        item = session.get(EstimateRecord, record_id)
        if item is None or (owner_id is not None and item.owner_id != owner_id):
            return False
        session.delete(item)
        session.commit()
        return True


def clear_estimates(engine, owner_id: UUID | None = None) -> int:
    with Session(engine) as session:
        counter = select(func.count()).select_from(EstimateRecord)
        statement = delete(EstimateRecord)
        if owner_id is not None:
            counter = counter.where(EstimateRecord.owner_id == owner_id)
            statement = statement.where(EstimateRecord.owner_id == owner_id)
        removed = session.exec(counter).one()
        session.exec(statement)
        session.commit()
        return int(removed)


def _tally(session, column, owner_id: UUID | None = None) -> dict[str, int]:
    """Count rows per distinct value, in SQL."""
    statement = select(column, func.count()).where(col(column).is_not(None))
    if owner_id is not None:
        statement = statement.where(EstimateRecord.owner_id == owner_id)
    rows = session.exec(statement.group_by(column)).all()
    return {str(value): int(count) for value, count in rows if value not in (None, "")}


#: How many past estimates the reference comparator may anchor against. The result blob is
#: loaded for each one, so this is capped: an anchor is only useful if it is genuinely similar,
#: and the hundredth-most-recent story is not going to be the closest match to this one.
REFERENCE_CORPUS_LIMIT = 60


def reference_corpus(
    engine, owner_id: UUID | None = None, limit: int = REFERENCE_CORPUS_LIMIT
) -> list[dict[str, Any]]:
    """The minimum a story needs to be compared against (EAGLE §10).

    Only the factor vector, the points and the stack come back — not the whole estimate. The
    comparator needs the shape of the work, and carrying the rest would make anchoring cost
    more than estimating.
    """
    with Session(engine) as session:
        statement = select(EstimateRecord).order_by(EstimateRecord.created_at.desc())
        if owner_id is not None:
            statement = statement.where(EstimateRecord.owner_id == owner_id)
        rows = session.exec(statement.limit(limit)).all()
    corpus = []
    for item in rows:
        scorecard = (item.result or {}).get("scorecard") or []
        if not scorecard:
            continue
        corpus.append(
            {
                "id": str(item.id),
                "title": item.title,
                "tldr": item.tldr,
                "points": item.decided_points or item.points,
                "frontend": item.frontend,
                "backend": item.backend,
                "result": {
                    "scorecard": [
                        {"factor": entry.get("factor"), "score": entry.get("score")}
                        for entry in scorecard
                        if entry.get("factor")
                    ]
                },
            }
        )
    return corpus


def estimate_stats(engine, owner_id: UUID | None = None) -> dict[str, Any]:
    """Aggregates that turn a log into a calibration record.

    Seeing that a team's 8s outnumber everything else, or that a third of estimates end in
    "spike first", says more about their estimating than any single story does.

    Aggregation happens in SQL. History is deliberately never purged, so loading every row
    to count it in Python would degrade steadily with use — precisely for the teams who have
    used the tool most and whose calibration data is worth the most.
    """
    with Session(engine) as session:
        owned = [] if owner_id is None else [col(EstimateRecord.owner_id) == owner_id]
        total = int(
            session.exec(select(func.count()).select_from(EstimateRecord).where(*owned)).one()
        )
        if not total:
            return {
                "total": 0, "points": {}, "recommendations": {}, "confidence": {},
                "decisions": {}, "median_points": None, "model_scored_share": None,
                "decided": 0, "accepted_as_recommended": 0, "overridden": 0,
                "override_bias": None, "with_actuals": 0, "actual_accuracy": None,
            }

        points = _tally(session, EstimateRecord.points, owner_id)
        recommendations = _tally(session, EstimateRecord.recommendation, owner_id)
        confidence = _tally(session, EstimateRecord.confidence, owner_id)
        decisions = _tally(session, EstimateRecord.decision, owner_id)

        scored_query = select(
            func.sum(EstimateRecord.model_scored), func.sum(EstimateRecord.heuristic_filled)
        )
        if owner_id is not None:
            scored_query = scored_query.where(EstimateRecord.owner_id == owner_id)
        scored, filled = session.exec(scored_query).one()
        factors = int(scored or 0) + int(filled or 0)

        # Median without loading the table: skip to the middle row.
        median_query = select(EstimateRecord.points)
        if owner_id is not None:
            median_query = median_query.where(EstimateRecord.owner_id == owner_id)
        median = session.exec(
            median_query.order_by(col(EstimateRecord.points))
            .offset(total // 2)
            .limit(1)
        ).first()

        decided_query = (
            select(func.count()).select_from(EstimateRecord)
            .where(col(EstimateRecord.decision).is_not(None))
        )
        if owner_id is not None:
            decided_query = decided_query.where(EstimateRecord.owner_id == owner_id)
        decided = int(session.exec(decided_query).one())
        overridden = decisions.get("override", 0)
        # How far the team moves the number when they disagree with it. A persistent
        # positive bias means the framework is reading their work as smaller than it is.
        bias_query = select(func.avg(EstimateRecord.decided_points - EstimateRecord.points)).where(
            col(EstimateRecord.decision) == "override"
        )
        if owner_id is not None:
            bias_query = bias_query.where(EstimateRecord.owner_id == owner_id)
        bias = session.exec(bias_query).one() if overridden else None

        actual_count_query = (
            select(func.count()).select_from(EstimateRecord)
            .where(col(EstimateRecord.actual_points).is_not(None))
        )
        accuracy_query = select(
            func.avg(func.abs(EstimateRecord.actual_points - EstimateRecord.points))
        ).where(col(EstimateRecord.actual_points).is_not(None))
        if owner_id is not None:
            actual_count_query = actual_count_query.where(EstimateRecord.owner_id == owner_id)
            accuracy_query = accuracy_query.where(EstimateRecord.owner_id == owner_id)
        with_actuals = int(session.exec(actual_count_query).one())
        accuracy = session.exec(accuracy_query).one() if with_actuals else None

    return {
        "total": total,
        "points": points,
        "recommendations": recommendations,
        "confidence": confidence,
        "decisions": decisions,
        "median_points": int(median) if median is not None else None,
        "model_scored_share": round(scored / factors, 3) if factors else None,
        "decided": decided,
        "accepted_as_recommended": decisions.get("accept", 0),
        "overridden": overridden,
        "override_bias": round(float(bias), 2) if bias is not None else None,
        "with_actuals": with_actuals,
        "actual_accuracy": round(float(accuracy), 2) if accuracy is not None else None,
    }
