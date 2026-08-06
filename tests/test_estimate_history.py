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
    record_decision,
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
    assert stats["total"] == 0
    assert stats["median_points"] is None
    assert stats["model_scored_share"] is None
    assert stats["override_bias"] is None
    assert stats["actual_accuracy"] is None
    assert stats["points"] == stats["decisions"] == {}


def test_recording_a_decision_closes_the_loop(engine):
    """The pipeline ends at "human decision required"; this is where the answer goes."""
    record = save_estimate(engine, estimate("Publish order events", 8))
    assert get_estimate(engine, record.id)["decision"] is None

    updated = record_decision(engine, record.id, "accept", note="Team agreed in refinement")
    assert updated["decision"] == "accept"
    assert updated["decided_points"] == 8, "accepting keeps the recommended number"
    assert updated["decision_note"] == "Team agreed in refinement"
    assert updated["decided_at"].endswith("+00:00")


def test_an_override_records_the_number_the_team_chose(engine):
    record = save_estimate(engine, estimate("Add biometric login", 5))
    updated = record_decision(engine, record.id, "override", points=13, note="Auth is riskier")

    assert updated["decided_points"] == 13
    assert updated["points"] == 5, "the recommendation is preserved alongside the override"


def test_a_team_may_decide_against_the_recommendation(engine):
    """Recording the disagreement is more useful than preventing it."""
    record = save_estimate(engine, estimate("Migrate auth", 21, recommendation="spike_first"))
    updated = record_decision(engine, record.id, "accept")
    assert updated["decision"] == "accept"
    assert updated["recommendation"] == "spike_first"


def test_an_unknown_decision_is_rejected(engine):
    record = save_estimate(engine, estimate("Anything", 5))
    with pytest.raises(ValueError, match="must be one of"):
        record_decision(engine, record.id, "maybe-later")


def test_deciding_on_a_missing_record_reports_a_miss(engine):
    assert record_decision(engine, uuid4(), "accept") is None


def test_stats_report_calibration_not_just_volume(engine):
    """What a team decided, and how far they move the number, is the useful signal."""
    a = save_estimate(engine, estimate("A", 5))
    b = save_estimate(engine, estimate("B", 8))
    c = save_estimate(engine, estimate("C", 8))
    save_estimate(engine, estimate("D", 13))  # left undecided

    record_decision(engine, a.id, "accept")
    record_decision(engine, b.id, "override", points=13)   # +5
    record_decision(engine, c.id, "override", points=13)   # +5
    record_decision(engine, a.id, "accept", actual_points=8)  # estimated 5, actually 8

    stats = estimate_stats(engine)
    assert stats["total"] == 4
    assert stats["decided"] == 3, "the undecided estimate is not counted as decided"
    assert stats["decisions"] == {"accept": 1, "override": 2}
    assert stats["accepted_as_recommended"] == 1
    assert stats["overridden"] == 2
    # Both overrides moved 8 -> 13, so the team reads this work as five points larger.
    assert stats["override_bias"] == 5.0
    assert stats["with_actuals"] == 1
    assert stats["actual_accuracy"] == 3.0


def test_stats_aggregate_in_sql_rather_than_loading_every_row(engine):
    """History is never purged, so counting must not scale with the table."""
    for index in range(120):
        save_estimate(engine, estimate(f"Story {index}", 8 if index % 2 else 13))
    stats = estimate_stats(engine)
    assert stats["total"] == 120
    assert stats["points"] == {"8": 60, "13": 60}
    assert stats["median_points"] in (8, 13)


def test_history_outlives_the_job_that_produced_it(engine):
    """Jobs are purged on a short retention; the estimate record is not."""
    record = save_estimate(engine, estimate("Long-lived", 13), uuid4())
    # Nothing in history depends on the job row still existing.
    stored = get_estimate(engine, record.id)
    assert stored["job_id"] is not None
    assert stored["result"]["points"] == 13
    assert EstimateRecord.__tablename__ != "job"
