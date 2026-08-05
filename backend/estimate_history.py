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

from backend.db import utc_iso


def now() -> datetime:
    return datetime.now(timezone.utc)


class EstimateRecord(SQLModel, table=True):
    id: UUID = Field(default_factory=uuid4, primary_key=True)
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

    #: The complete estimate payload, rendered by the same component as a live result.
    result: dict = Field(default_factory=dict, sa_column=Column(JSON))


def record_summary(item: EstimateRecord) -> dict[str, Any]:
    return {
        "id": str(item.id),
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
    }


def save_estimate(engine, result: dict[str, Any], job_id: UUID | None = None) -> EstimateRecord:
    """Persist one completed estimate. Tolerates partial payloads rather than raising.

    History is a side effect of estimating; a schema surprise here must never fail the
    estimate the user is waiting for.
    """
    story = result.get("story") or {}
    stack = result.get("stack") or {}
    calculation = result.get("calculation") or {}
    provenance = (result.get("evidence") or {}).get("scoring_provenance") or {}
    record = EstimateRecord(
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
) -> dict[str, Any]:
    """Search history, newest first, with the total so the UI can paginate honestly."""
    with Session(engine) as session:
        statement = select(EstimateRecord)
        counter = select(func.count()).select_from(EstimateRecord)
        clauses = []
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


def get_estimate(engine, record_id: UUID) -> dict[str, Any] | None:
    with Session(engine) as session:
        item = session.get(EstimateRecord, record_id)
        if item is None:
            return None
        return {**record_summary(item), "result": item.result}


def delete_estimate(engine, record_id: UUID) -> bool:
    with Session(engine) as session:
        item = session.get(EstimateRecord, record_id)
        if item is None:
            return False
        session.delete(item)
        session.commit()
        return True


def clear_estimates(engine) -> int:
    with Session(engine) as session:
        removed = session.exec(select(func.count()).select_from(EstimateRecord)).one()
        session.exec(delete(EstimateRecord))
        session.commit()
        return int(removed)


def estimate_stats(engine) -> dict[str, Any]:
    """Aggregates that turn a log into a calibration record.

    Seeing that a team's 8s outnumber everything else, or that a third of estimates end in
    "spike first", says more about their estimating than any single story does.
    """
    with Session(engine) as session:
        rows = session.exec(
            select(
                EstimateRecord.points,
                EstimateRecord.recommendation,
                EstimateRecord.confidence,
                EstimateRecord.model_scored,
                EstimateRecord.heuristic_filled,
            )
        ).all()
    if not rows:
        return {
            "total": 0, "points": {}, "recommendations": {}, "confidence": {},
            "median_points": None, "model_scored_share": None,
        }
    points: dict[str, int] = {}
    recommendations: dict[str, int] = {}
    confidence: dict[str, int] = {}
    scored = filled = 0
    for row in rows:
        points[str(row[0])] = points.get(str(row[0]), 0) + 1
        if row[1]:
            recommendations[row[1]] = recommendations.get(row[1], 0) + 1
        if row[2]:
            confidence[row[2]] = confidence.get(row[2], 0) + 1
        scored += int(row[3] or 0)
        filled += int(row[4] or 0)
    ordered = sorted(int(row[0]) for row in rows)
    total_factors = scored + filled
    return {
        "total": len(rows),
        "points": points,
        "recommendations": recommendations,
        "confidence": confidence,
        "median_points": ordered[len(ordered) // 2],
        "model_scored_share": round(scored / total_factors, 3) if total_factors else None,
    }
