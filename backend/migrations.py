"""Explicit, ordered schema migrations for the local SQLite database.

``SQLModel.metadata.create_all`` only ever *creates* missing tables. It will not add a
column to a table that already exists, so a released build that gains a field would read a
user's existing database and fail at query time — silently, and only for people who already
had data. That is the failure mode this module exists to prevent.

Alembic is the usual answer and is deliberately not used here: it brings a migration
environment, revision files, and an offline/online split that a single-file, single-user,
local SQLite database does not need. What is needed is that every schema change is recorded,
applied exactly once, and applied in order. That is small enough to own directly.

Adding a migration:

1. Append a ``Migration`` to ``MIGRATIONS`` with the next version number.
2. Never edit or renumber an existing entry — released databases have already recorded it.
3. Keep each step idempotent where cheap, so a partially applied upgrade can be retried.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Migration:
    version: int
    description: str
    apply: Callable[[Connection], None]


def _columns(connection: Connection, table: str) -> set[str]:
    rows = connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
    return {row[1] for row in rows}


def _tables(connection: Connection) -> set[str]:
    rows = connection.execute(
        text("SELECT name FROM sqlite_master WHERE type = 'table'")
    ).fetchall()
    return {row[0] for row in rows}


def add_column(table: str, column: str, definition: str) -> Callable[[Connection], None]:
    """Add a column when the table exists and the column does not.

    Both guards matter: the table may not exist yet on a fresh install (``create_all`` will
    have made it with the column already present), and the column may exist if a previous
    run failed after the ALTER but before recording the version.
    """

    def step(connection: Connection) -> None:
        if table not in _tables(connection):
            return
        if column in _columns(connection, table):
            return
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))

    return step


def _add_estimate_decision_columns(connection: Connection) -> None:
    """v2 — record what the team actually decided, and what it actually cost.

    Without these the pipeline ends at "human decision required" with nowhere to put the
    answer, and calibration statistics can only ever report what was estimated, never
    whether the estimate was any good.
    """
    for column, definition in (
        ("decision", "VARCHAR"),
        ("decided_points", "INTEGER"),
        ("decision_note", "VARCHAR"),
        ("decided_at", "DATETIME"),
        ("actual_points", "INTEGER"),
    ):
        add_column("estimaterecord", column, definition)(connection)


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        description="Baseline: tables are created by SQLModel metadata",
        apply=lambda _connection: None,
    ),
    Migration(
        version=2,
        description="Estimate history records the human decision and the actual outcome",
        apply=_add_estimate_decision_columns,
    ),
)

SCHEMA_VERSION = max(item.version for item in MIGRATIONS)


def current_version(connection: Connection) -> int:
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  description VARCHAR,"
            "  applied_at DATETIME DEFAULT CURRENT_TIMESTAMP"
            ")"
        )
    )
    row = connection.execute(text("SELECT MAX(version) FROM schema_version")).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def run_migrations(engine) -> list[int]:
    """Apply every migration newer than the recorded version. Returns what was applied.

    Each migration runs in its own transaction and records its version in the same
    transaction, so an interrupted upgrade never leaves a version marked as applied when its
    changes are not.
    """
    applied: list[int] = []
    with engine.begin() as connection:
        version = current_version(connection)
    for migration in sorted(MIGRATIONS, key=lambda item: item.version):
        if migration.version <= version:
            continue
        with engine.begin() as connection:
            migration.apply(connection)
            connection.execute(
                text(
                    "INSERT INTO schema_version (version, description) VALUES (:version, :description)"
                ),
                {"version": migration.version, "description": migration.description},
            )
        applied.append(migration.version)
        logger.info("Applied schema migration %d: %s", migration.version, migration.description)
    return applied
