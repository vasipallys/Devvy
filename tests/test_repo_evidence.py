"""Repository intelligence for estimation — EAGLE §3 and §4.

The estimator used to read a story and nothing else, so every question the story left open was
answered with "the story does not say" and priced as unbounded. With a repository present the
first rung of the §6 ladder is available: the codebase can answer, and these tests pin that it
answers with facts it actually found rather than with plausible-sounding ones.

The sharpest risk here is not a wrong score, it is a convincing file path. A model shown a
repository listing will name `src/services/RiskClassifier.java` for a repository with no
`src/services`, and that path is then read as a finding by someone who was not in the room.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ["PHOENIX_ENABLED"] = "false"

import pytest

from backend.repo_evidence import (
    analyse_repository,
    counts,
    factor_findings,
    fallback_plan,
    prompt_block,
    validate_paths,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A small but realistic repository: manifests, modules, migrations, tests, auth."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fastapi", "sqlmodel", "pytest"]\n', encoding="utf-8"
    )
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "19"}}', encoding="utf-8")
    for relative, body in {
        "backend/orders.py": "def list_orders(): ...",
        "backend/customers.py": "def get_customer(): ...",
        "backend/auth/session.py": "def login(): ...",
        "backend/migrations/001_initial.py": "def upgrade(): ...",
        "backend/migrations/002_orders.py": "def upgrade(): ...",
        "backend/routes/orders_api.py": "router = ...",
        "tests/test_orders.py": "def test_orders(): ...",
        "docs/architecture.md": "# Architecture",
        "frontend/src/OrdersScreen.tsx": "export function OrdersScreen() {}",
    }.items():
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
    return tmp_path


# -- Reading the repository ---------------------------------------------------------------

def test_an_unreadable_path_is_reported_rather_than_guessed(tmp_path: Path):
    for value, expected in (
        ("", "No workspace path supplied."),
        ("relative/path", "The workspace path must be absolute."),
        (str(tmp_path / "nope"), "That path is not a directory on this machine."),
    ):
        evidence = analyse_repository(value, "any story")
        assert not evidence.reachable
        assert evidence.reason == expected


def test_stack_is_read_from_manifests_not_inferred(repo: Path):
    evidence = analyse_repository(str(repo), "orders")
    assert {"python", "javascript"} <= set(evidence.languages)
    assert {"FastAPI", "React", "SQLModel", "pytest"} <= set(evidence.frameworks)
    assert any(m.endswith("pyproject.toml") for m in evidence.manifests)


def test_modules_are_the_repository_top_level(repo: Path):
    evidence = analyse_repository(str(repo), "orders")
    assert {"backend", "frontend", "tests", "docs"} <= set(evidence.modules)


def test_a_signal_is_found_in_a_file_name_not_only_a_directory(tmp_path: Path):
    """`backend/migrations.py` is migration tooling whether or not anyone made a folder."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "migrations.py").write_text("def upgrade(): ...", encoding="utf-8")
    evidence = analyse_repository(str(tmp_path), "add a column")
    assert evidence.signal("migrations").present


def test_absent_signals_are_reported_as_absent(repo: Path):
    evidence = analyse_repository(str(repo), "orders")
    assert not evidence.signal("ci").present
    assert not evidence.signal("feature_flags").present
    assert evidence.signal("migrations").present


# -- Ranking the change surface -------------------------------------------------------------

def test_the_change_surface_is_ranked_by_the_story_own_words(repo: Path):
    evidence = analyse_repository(str(repo), "Add a status column to orders")
    paths = [item.path for item in evidence.candidates]
    assert any("orders" in path for path in paths)
    assert not any("customers" in path for path in paths)


def test_a_file_name_match_outranks_a_directory_match(repo: Path):
    evidence = analyse_repository(str(repo), "orders")
    top = evidence.candidates[0]
    assert "orders" in Path(top.path).stem
    assert top.matched_terms


def test_a_shared_stem_still_matches(repo: Path):
    """Exact intersection alone never matches "migrating" against `migrations`."""
    evidence = analyse_repository(str(repo), "migrating the orders table")
    assert any("migration" in item.path for item in evidence.candidates)


def test_tests_beside_the_change_surface_are_found(repo: Path):
    evidence = analyse_repository(str(repo), "orders")
    assert any(path.endswith("test_orders.py") for path in evidence.related_tests)


def test_a_prose_document_is_not_classified_as_a_test(repo: Path):
    (repo / "docs" / "orders_spec.md").write_text("# Orders spec", encoding="utf-8")
    evidence = analyse_repository(str(repo), "orders")
    assert not any(path.endswith(".md") for path in evidence.related_tests)


def test_a_story_unrelated_to_the_repository_matches_nothing(repo: Path):
    evidence = analyse_repository(str(repo), "Refactor the payroll ledger for tax year rollover")
    assert evidence.candidates == []


# -- What the repository can answer ---------------------------------------------------------

def test_existing_migrations_answer_the_data_model_factor(repo: Path):
    story = "Add a status column to the orders table"
    findings = {item.factor: item for item in factor_findings(analyse_repository(str(repo), story), story)}
    assert findings["data_model_change"].score == 4
    assert "migration" in findings["data_model_change"].reason.lower()
    assert findings["data_model_change"].evidence


def test_a_missing_migration_tool_costs_more_than_an_existing_one(tmp_path: Path, repo: Path):
    # A genuinely separate directory: the `repo` fixture is built from `tmp_path`, so writing
    # into `tmp_path` would be adding a file to the same repository rather than making a bare one.
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "app.py").write_text("x = 1", encoding="utf-8")
    story = "Add a status column to the orders table"
    without = {i.factor: i for i in factor_findings(analyse_repository(str(bare), story), story)}
    with_tool = {i.factor: i for i in factor_findings(analyse_repository(str(repo), story), story)}
    assert without["data_model_change"].score > with_tool["data_model_change"].score


def test_tests_beside_the_change_lower_the_test_effort(repo: Path):
    story = "Add a status column to orders"
    findings = {i.factor: i for i in factor_findings(analyse_repository(str(repo), story), story)}
    assert findings["test_effort"].score == 3
    assert "existing test" in findings["test_effort"].reason


def test_a_repository_with_no_tests_costs_the_most(tmp_path: Path):
    bare = tmp_path / "untested"
    bare.mkdir()
    (bare / "app.py").write_text("x = 1", encoding="utf-8")
    findings = {i.factor: i for i in factor_findings(analyse_repository(str(bare), "app"), "app")}
    assert findings["test_effort"].score == 5


def test_a_story_matching_nothing_raises_uncertainty(repo: Path):
    story = "Refactor the payroll ledger for tax year rollover"
    findings = {i.factor: i for i in factor_findings(analyse_repository(str(repo), story), story)}
    assert findings["uncertainty"].score == 4
    assert "matches any word" in findings["uncertainty"].reason


def test_every_reason_claims_only_what_was_inspected(repo: Path):
    """Names were read, not file contents. "No logging code exists" would be an overclaim."""
    story = "Add a status column to orders"
    for finding in factor_findings(analyse_repository(str(repo), story), story):
        lowered = finding.reason.lower()
        assert "no logging code exists" not in lowered
        if "named for" in lowered:
            assert "no file or directory in the repository is named for" in lowered


# -- Paths must be real ---------------------------------------------------------------------

def test_an_invented_path_is_rejected(repo: Path):
    evidence = analyse_repository(str(repo), "orders")
    plan = validate_paths(repo, [
        {"path": "backend/orders.py", "detail": "Add the status field."},
        {"path": "src/services/RiskClassifier.java", "detail": "Invented."},
    ], evidence)
    assert [item.path for item in plan.changes] == ["backend/orders.py"]
    assert plan.rejected_paths == ["src/services/RiskClassifier.java"]
    assert "discarded" in plan.note


def test_a_new_file_in_an_existing_directory_is_allowed_as_a_creation(repo: Path):
    evidence = analyse_repository(str(repo), "orders")
    plan = validate_paths(repo, [
        {"path": "backend/order_status.py", "detail": "New status enum."},
    ], evidence)
    assert plan.created == 1
    assert plan.changes[0].action == "create"


def test_an_existing_file_is_a_modification(repo: Path):
    evidence = analyse_repository(str(repo), "orders")
    plan = validate_paths(repo, [{"path": "backend/orders.py", "detail": "Edit."}], evidence)
    assert plan.modified == 1
    assert plan.changes[0].action == "modify"


def test_traversal_is_refused(repo: Path):
    evidence = analyse_repository(str(repo), "orders")
    plan = validate_paths(repo, [{"path": "../../etc/passwd", "detail": "no"}], evidence)
    assert plan.changes == []


def test_the_fallback_plan_says_it_is_only_a_ranking(repo: Path):
    evidence = analyse_repository(str(repo), "orders")
    plan = fallback_plan(evidence)
    assert plan.changes
    assert all(item.action == "modify" for item in plan.changes)
    assert "candidates rather than a plan" in plan.note


# -- What reaches the prompt -----------------------------------------------------------------

def test_the_prompt_block_states_present_and_absent_signals(repo: Path):
    block = prompt_block(analyse_repository(str(repo), "orders"))
    assert "Structural signals PRESENT" in block
    assert "Structural signals ABSENT" in block
    assert "Likely change surface" in block


def test_the_prompt_block_is_bounded(repo: Path):
    from backend.repo_evidence import REPO_CONTEXT_BUDGET

    assert len(prompt_block(analyse_repository(str(repo), "orders"))) <= REPO_CONTEXT_BUDGET


def test_an_unreachable_repository_contributes_nothing_to_the_prompt():
    assert prompt_block(analyse_repository("", "orders")) == ""


def test_counts_are_reported_for_the_evidence_record(repo: Path):
    figures = counts(analyse_repository(str(repo), "orders"))
    assert figures["source_files"] > 0
    assert figures["signals_present"] + figures["signals_absent"] == 13


# -- Determinism ------------------------------------------------------------------------------

def test_the_same_repository_and_story_produce_the_same_evidence(repo: Path):
    first = analyse_repository(str(repo), "Add a status column to orders")
    second = analyse_repository(str(repo), "Add a status column to orders")
    assert first.model_dump() == second.model_dump()
