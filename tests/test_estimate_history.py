"""Estimate history: the durable record a team refers back to.

History outlives the job that produced it, keeps the full result so a stored entry renders
exactly like a fresh one, and is searchable by the fields people actually look for.
"""

from datetime import datetime, timezone

import pytest
from sqlmodel import SQLModel, create_engine
from uuid import uuid4

from backend.estimate_history import (
    EstimateRecord,
    clear_estimates,
    delete_estimate,
    estimate_stats,
    get_estimate,
    list_estimates,
    save_estimate,
)


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'history.db').as_posix()}")
    SQLModel.metadata.create_all(engine)
    yield engine
    engine.dispose()


def estimate(title: str, points: int = 8, **overrides) -> dict:
    """A result payload shaped like the one EstimateService returns."""
    return {
        "story": {"title": title, "source": overrides.get("source", "manual"),
                  "key": overrides.get("key")},
        "points": points,
        "confidence": overrides.get("confidence", "Medium"),
        "recommendation": overrides.get("recommendation", "proceed"),
        "tldr": overrides.get("tldr", f"{points} points for {title}"),
        "calculation": {"base_sum": 40, "adjusted_score": 42, "band": "35-44",
                        "steps": [{"rule": "base_sum", "delta": 40}]},
        "stack": {"frontend": "react", "backend": "spring_boot",
                  "maturity_level": 3, "team_experience": 4},
        "scorecard": [{"factor": "uncertainty", "score": 3}],
        "evidence": {"scoring_provenance": {"model_scored": 12, "heuristic_filled": 4}},
    }


def test_a_saved_estimate_keeps_its_full_payload(engine):
    """A stored estimate is evidence; summarising it away would defeat the point."""
    job_id = uuid4()
    record = save_estimate(engine, estimate("Publish order events", 13), job_id)

    stored = get_estimate(engine, record.id)
    assert stored["title"] == "Publish order events"
    assert stored["points"] == 13
    assert stored["job_id"] == str(job_id)
    assert stored["adjusted_score"] == 42
    assert stored["band"] == "35-44"
    assert stored["model_scored"] == 12
    # The calculation ledger survives verbatim, so history renders like a live result.
    assert stored["result"]["calculation"]["steps"] == [{"rule": "base_sum", "delta": 40}]
    assert stored["result"]["scorecard"][0]["factor"] == "uncertainty"


def test_timestamps_are_emitted_as_utc_instants(engine):
    """SQLite drops tzinfo; a bare ISO string is read by browsers as *local* time.

    Without the offset every displayed timestamp shifts by the viewer's UTC offset, which
    is how a record created seconds ago rendered as "5h ago".
    """
    record = save_estimate(engine, estimate("Just now", 5))
    created = get_estimate(engine, record.id)["created_at"]
    assert created.endswith("+00:00"), created
    parsed = datetime.fromisoformat(created)
    assert parsed.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 60


def test_a_partial_payload_is_stored_rather_than_rejected(engine):
    """History must never fail the estimate a user is waiting for."""
    record = save_estimate(engine, {"points": 5})
    stored = get_estimate(engine, record.id)
    assert stored["title"] == "Untitled story"
    assert stored["points"] == 5
    assert stored["source"] == "manual"
    assert stored["issue_key"] is None


def test_search_matches_title_key_and_summary(engine):
    save_estimate(engine, estimate("Publish order events to Kafka", 13))
    save_estimate(engine, estimate("Add biometric login", 8, key="AUTH-42"))
    save_estimate(engine, estimate("Refactor billing", 5, tldr="Touches the Kafka consumer"))

    assert list_estimates(engine, query="kafka")["total"] == 2, "title and summary both match"
    assert list_estimates(engine, query="AUTH-4")["total"] == 1
    assert [item["title"] for item in list_estimates(engine, query="biometric")["items"]] == [
        "Add biometric login"
    ]
    assert list_estimates(engine, query="nothing here")["total"] == 0


def test_filters_and_pagination_report_an_honest_total(engine):
    for index in range(7):
        save_estimate(engine, estimate(f"Story {index}", 8 if index % 2 else 13))
    save_estimate(engine, estimate("Spiky one", 21, recommendation="spike_first"))

    page = list_estimates(engine, limit=3, offset=0)
    assert len(page["items"]) == 3
    assert page["total"] == 8, "total counts every match, not just this page"

    second = list_estimates(engine, limit=3, offset=3)
    assert {item["id"] for item in page["items"]} & {item["id"] for item in second["items"]} == set()

    assert list_estimates(engine, points=13)["total"] == 4
    assert list_estimates(engine, recommendation="spike_first")["total"] == 1


def test_history_is_newest_first(engine):
    save_estimate(engine, estimate("First", 3))
    save_estimate(engine, estimate("Second", 5))
    save_estimate(engine, estimate("Third", 8))
    assert [item["title"] for item in list_estimates(engine)["items"]] == [
        "Third", "Second", "First",
    ]


def test_delete_removes_one_entry_and_reports_a_miss(engine):
    record = save_estimate(engine, estimate("Temporary", 5))
    save_estimate(engine, estimate("Keeper", 8))

    assert delete_estimate(engine, record.id) is True
    assert get_estimate(engine, record.id) is None
    assert delete_estimate(engine, record.id) is False, "deleting twice is not an error path"
    assert [item["title"] for item in list_estimates(engine)["items"]] == ["Keeper"]


def test_clear_reports_how_many_it_removed(engine):
    for index in range(4):
        save_estimate(engine, estimate(f"Story {index}"))
    assert clear_estimates(engine) == 4
    assert list_estimates(engine)["total"] == 0
    assert clear_estimates(engine) == 0


def test_stats_turn_a_log_into_a_calibration_record(engine):
    save_estimate(engine, estimate("A", 5, confidence="High"))
    save_estimate(engine, estimate("B", 8, confidence="Medium"))
    save_estimate(engine, estimate("C", 8, confidence="Low", recommendation="spike_first"))

    stats = estimate_stats(engine)
    assert stats["total"] == 3
    assert stats["points"] == {"5": 1, "8": 2}
    assert stats["recommendations"] == {"proceed": 2, "spike_first": 1}
    assert stats["confidence"] == {"High": 1, "Medium": 1, "Low": 1}
    assert stats["median_points"] == 8
    # 12 model-scored of 16 factors, on every record.
    assert stats["model_scored_share"] == 0.75


def test_stats_on_empty_history_does_not_divide_by_zero(engine):
    stats = estimate_stats(engine)
    assert stats == {
        "total": 0, "points": {}, "recommendations": {}, "confidence": {},
        "median_points": None, "model_scored_share": None,
    }


def test_history_outlives_the_job_that_produced_it(engine):
    """Jobs are purged on a short retention; the estimate record is not."""
    record = save_estimate(engine, estimate("Long-lived", 13), uuid4())
    # Nothing in history depends on the job row still existing.
    stored = get_estimate(engine, record.id)
    assert stored["job_id"] is not None
    assert stored["result"]["points"] == 13
    assert EstimateRecord.__tablename__ != "job"
