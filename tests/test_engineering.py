"""The engineering discipline: prove before modify, trace everything, claim nothing unobserved.

The enterprise pipeline specification describes twenty-three agents. These tests pin the parts
of it that are decidable from evidence, because those are the parts that can be enforced rather
than merely asked for. A rule that lives only in a prompt is a rule a model can skip while
reporting that it followed it.
"""

from __future__ import annotations

import os

os.environ["PHOENIX_ENABLED"] = "false"

from backend.engineering import (
    NOT_EXECUTED,
    NOT_VERIFIED,
    analyse_requirement,
    assess_necessity,
    final_decision,
    traceability,
)


class Edit:
    """The shape `assess_necessity` reads from a proposed edit."""

    def __init__(self, path: str, action: str, reason: str):
        self.path, self.action, self.reason = path, action, reason


OBJECTIVE = (
    "Store an order status against each order. The status must be persisted and existing "
    "order responses must keep working."
)
CRITERIA = ["Status is stored on the order", "Existing order API is unchanged"]


# -- Agent 6: requirements are numbered and sourced ----------------------------------------

def test_every_requirement_is_numbered_and_says_where_it_came_from():
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    assert [item.id for item in spec.functional] == ["FR-001", "FR-002"]
    assert all(item.source for item in spec.functional)
    assert all(item.id.startswith("NFR-") for item in spec.non_functional)


def test_a_non_functional_requirement_is_recorded_only_when_the_text_raises_it():
    """Otherwise every story acquires the same eight concerns and none of them mean anything."""
    quiet = analyse_requirement("Rename the field customerName to customer_name.")
    assert quiet.non_functional == []
    loud = analyse_requirement("Encrypt the stored token and write an audit trail.")
    assert {item.statement for item in loud.non_functional}


def test_acceptance_criteria_attach_to_the_requirement_they_verify():
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    assert any(item.acceptance for item in spec.functional)


def test_a_missing_criterion_becomes_a_recorded_assumption_not_a_silent_decision():
    spec = analyse_requirement("Add a status field.")
    assert any(item.about == "acceptance criteria" for item in spec.assumptions)
    assert all(item.because for item in spec.assumptions)


def test_undefined_edge_cases_are_raised_as_questions():
    spec = analyse_requirement("Add a status field.")
    assert spec.open_questions
    assert all(item.endswith("?") for item in spec.open_questions)


# -- Agent 10: prove before modify ----------------------------------------------------------

def test_an_edit_that_serves_no_requirement_is_dropped():
    """The rule that does the work. A file being about the same subject is not a reason."""
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    report = assess_necessity([
        Edit("backend/orders.py", "replace", "Persist the order status"),
        Edit("frontend/theme.css", "replace", "Tidy the spacing while we are here"),
    ], spec)
    assert report.dropped == ["frontend/theme.css"]
    assert [item.path for item in report.necessary] == ["backend/orders.py"]


def test_a_kept_edit_names_the_requirement_and_the_evidence():
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    verdict = assess_necessity(
        [Edit("backend/orders.py", "replace", "Persist the order status")], spec
    ).verdicts[0]
    assert verdict.necessary
    assert verdict.requirement == "FR-001"
    assert "term(s) with FR-001" in verdict.evidence


def test_a_dropped_edit_says_what_to_do_instead():
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    verdict = assess_necessity([Edit("frontend/theme.css", "replace", "tidy")], spec).verdicts[0]
    assert not verdict.necessary
    assert verdict.alternative


def test_a_new_file_is_kept_but_flagged_as_unmatched():
    """A path that does not exist yet cannot be matched, and creating one is a positive act."""
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    verdict = assess_necessity(
        [Edit("backend/brand_new_thing.py", "create", "something entirely unrelated")], spec
    ).verdicts[0]
    assert verdict.necessary
    assert verdict.requirement is None
    assert verdict.evidence == NOT_VERIFIED


def test_files_reviewed_and_left_alone_are_reported():
    """The clearest evidence a change is minimal: what was considered and rejected."""
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    report = assess_necessity(
        [Edit("backend/orders.py", "replace", "Persist the order status")], spec,
        candidates=["backend/orders.py", "backend/customers.py", "backend/billing.py"],
    )
    paths = [item["path"] for item in report.reviewed_unchanged]
    assert paths == ["backend/customers.py", "backend/billing.py"]
    assert all(item["reason"] for item in report.reviewed_unchanged)


# -- Agent 23: trace everything, claim nothing unobserved -----------------------------------

def test_a_requirement_with_no_implementation_is_listed_as_uncovered():
    """A matrix that only shows what was done cannot show what was forgotten."""
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    report = assess_necessity([Edit("backend/orders.py", "replace", "Persist the status")], spec)
    rows = traceability(spec, report)
    assert {row.requirement for row in rows} == set(spec.ids)
    assert any(row.status == "uncovered" for row in rows)


def test_implementation_without_a_test_is_untested_not_covered():
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    report = assess_necessity([Edit("backend/orders.py", "replace", "Persist the status")], spec)
    row = next(item for item in traceability(spec, report) if item.requirement == "FR-001")
    assert row.status == "untested"


def test_implementation_with_a_test_is_covered():
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    report = assess_necessity([
        Edit("backend/orders.py", "replace", "Persist the order status"),
        Edit("tests/test_order_status.py", "create", "Cover the order status"),
    ], spec)
    row = next(item for item in traceability(spec, report) if item.requirement == "FR-001")
    assert row.status == "covered"
    assert row.tests == ["tests/test_order_status.py"]


def test_the_build_and_test_status_are_never_claimed():
    """Nothing here executes the generated code, so there is only one honest answer."""
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    report = assess_necessity([Edit("backend/orders.py", "replace", "status")], spec)
    decision = final_decision(spec, traceability(spec, report), report, [])
    assert decision.build_status == NOT_EXECUTED
    assert decision.test_status == NOT_EXECUTED


def test_a_structural_failure_blocks_rather_than_needing_a_fix():
    spec = analyse_requirement(OBJECTIVE, CRITERIA)
    report = assess_necessity([Edit("backend/orders.py", "replace", "status")], spec)
    decision = final_decision(
        spec, traceability(spec, report), report, ["backend/orders.py: invalid syntax"]
    )
    assert decision.decision == "BLOCKED"
    assert not decision.ready_for_pull_request


def test_full_coverage_still_does_not_approve_without_a_green_build():
    """The specification forbids approving when the build has not passed. It has not run."""
    spec = analyse_requirement("Add a status field.", ["Status is stored"])
    report = assess_necessity([
        Edit("backend/status.py", "replace", "Add the status field"),
        Edit("tests/test_status.py", "create", "Cover the status field"),
    ], spec)
    rows = traceability(spec, report)
    decision = final_decision(spec, rows, report, [])
    assert decision.decision != "APPROVED"
    assert not decision.ready_for_pull_request
    assert "NOT EXECUTED" in decision.reasoning


def test_the_decision_carries_the_unresolved_assumptions():
    spec = analyse_requirement("Add a status field.")
    report = assess_necessity([], spec)
    decision = final_decision(spec, traceability(spec, report), report, [])
    assert decision.remaining_assumptions
    assert all("ASSUMPTION-" in item for item in decision.remaining_assumptions)


# -- The contract that carries these rules to the model -------------------------------------

def test_the_engineering_contract_states_the_minimum_change_principle():
    from backend.harness import ENGINEERING_CONTRACT

    lowered = ENGINEERING_CONTRACT.lower()
    assert "minimum necessary change" in lowered
    assert "prove before you modify" in lowered
    assert "reuse before creating" in lowered
    assert "backward compatibility" in lowered
    assert "not executed" in lowered and "not verified" in lowered


def test_smart_code_carries_the_engineering_contract():
    import inspect

    import backend.smart_code

    assert "ENGINEERING_CONTRACT" in inspect.getsource(backend.smart_code)
