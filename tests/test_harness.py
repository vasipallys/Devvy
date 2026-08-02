import json

from backend.harness import ContextSource, RunLedger, assemble_context


def test_context_assembly_is_priority_ordered_bounded_and_labeled():
    context, manifest = assemble_context(
        [
            ContextSource("low", "Lower priority", "low evidence", priority=10),
            ContextSource("high", "Higher priority", "high evidence", priority=90),
        ],
        max_chars=240,
    )
    assert len(context) <= 240
    assert context.index("high evidence") < context.index("low evidence")
    assert "UNTRUSTED EVIDENCE" in context
    assert manifest[0]["id"] == "high"


def test_run_ledger_records_trajectory_without_prompt_content(tmp_path):
    ledger = RunLedger(tmp_path)
    run = ledger.start("chat", metadata={"mode": "document"})
    run.event("context", "completed", "Context assembled", evidence={"documents": 2})
    run.finish("completed", summary={"mode": "document"})

    payload = json.loads(next(ledger.directory.glob("*.jsonl")).read_text(encoding="utf-8"))
    assert payload["workflow"] == "chat"
    assert payload["trajectory"][0]["stage"] == "context"
    assert "prompt" not in payload
    assert "response" not in payload
