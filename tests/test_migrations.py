"""Schema migrations against a database that already exists.

`SQLModel.metadata.create_all` only creates missing tables. The failure this guards is
specific and nasty: a released build gains a column, an existing user's database keeps the
old table untouched, and queries fail only for the people who already had data.
"""

from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from backend.estimate_history import EstimateRecord, record_decision, save_estimate
from backend.migrations import MIGRATIONS, SCHEMA_VERSION, add_column, run_migrations


def make_engine(tmp_path, name="app.db"):
    return create_engine(f"sqlite:///{(tmp_path / name).as_posix()}")


def columns(engine, table):
    with engine.begin() as connection:
        return {row[1] for row in connection.execute(text(f"PRAGMA table_info({table})"))}


def test_a_fresh_database_records_the_current_version(tmp_path):
    engine = make_engine(tmp_path)
    SQLModel.metadata.create_all(engine)
    applied = run_migrations(engine)
    try:
        assert applied == [item.version for item in MIGRATIONS]
        with engine.begin() as connection:
            version = connection.execute(text("SELECT MAX(version) FROM schema_version")).scalar()
        assert version == SCHEMA_VERSION
    finally:
        engine.dispose()


def test_migrations_are_applied_exactly_once(tmp_path):
    engine = make_engine(tmp_path)
    SQLModel.metadata.create_all(engine)
    try:
        assert run_migrations(engine), "first run applies"
        assert run_migrations(engine) == [], "second run is a no-op"
        with engine.begin() as connection:
            rows = connection.execute(text("SELECT COUNT(*) FROM schema_version")).scalar()
        assert rows == len(MIGRATIONS), "no duplicate version rows"
    finally:
        engine.dispose()


def test_an_older_database_gains_the_new_columns(tmp_path):
    """The real upgrade path: a table that predates the decision columns."""
    engine = make_engine(tmp_path)
    with engine.begin() as connection:
        # A v1-era table: everything the record had before decisions existed.
        connection.execute(text(
            "CREATE TABLE estimaterecord ("
            " id VARCHAR PRIMARY KEY, created_at DATETIME, job_id VARCHAR, title VARCHAR,"
            " issue_key VARCHAR, source VARCHAR, points INTEGER, confidence VARCHAR,"
            " recommendation VARCHAR, base_sum INTEGER, adjusted_score INTEGER, band VARCHAR,"
            " frontend VARCHAR, backend VARCHAR, maturity_level INTEGER, team_experience INTEGER,"
            " model_scored INTEGER, heuristic_filled INTEGER, tldr VARCHAR, result JSON)"
        ))
    try:
        before = columns(engine, "estimaterecord")
        assert "decision" not in before, "precondition: the old shape"

        run_migrations(engine)

        after = columns(engine, "estimaterecord")
        assert {"decision", "decided_points", "decision_note", "decided_at", "actual_points"} <= after
        # create_all would have left this table exactly as it was.
        assert before < after
    finally:
        engine.dispose()


def test_existing_rows_survive_the_upgrade(tmp_path):
    """An upgrade must not cost the user their history."""
    engine = make_engine(tmp_path)
    SQLModel.metadata.create_all(engine)
    run_migrations(engine)
    record = save_estimate(engine, {"story": {"title": "Before upgrade"}, "points": 8})

    # Re-running migrations is what a restart does; the row must be untouched.
    run_migrations(engine)
    try:
        with Session(engine) as session:
            rows = session.exec(select(EstimateRecord)).all()
        assert [item.title for item in rows] == ["Before upgrade"]
        assert record_decision(engine, record.id, "accept")["decided_points"] == 8
    finally:
        engine.dispose()


def test_add_column_is_idempotent_and_skips_absent_tables(tmp_path):
    """A retried upgrade must not fail on work it already did."""
    engine = make_engine(tmp_path)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE demo (id INTEGER PRIMARY KEY)"))
        step = add_column("demo", "note", "VARCHAR")
        step(connection)
        step(connection)  # second call is a no-op rather than an error
        add_column("not_a_table", "note", "VARCHAR")(connection)
    try:
        assert columns(engine, "demo") == {"id", "note"}
    finally:
        engine.dispose()


def test_versions_are_unique_and_ordered():
    versions = [item.version for item in MIGRATIONS]
    assert versions == sorted(versions), "migrations must be declared in order"
    assert len(versions) == len(set(versions)), "a version number is never reused"
