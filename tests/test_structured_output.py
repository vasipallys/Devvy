import json

from pydantic import BaseModel

from backend.structured_output import generate_structured


class Output(BaseModel):
    value: int


class RepairRuntime:
    def __init__(self):
        self.calls = 0

    async def generate(self, _messages, max_new_tokens, **_options):
        assert max_new_tokens == 64
        self.calls += 1
        return '{"wrong": true}' if self.calls == 1 else '{"value": 7}'


async def test_structured_generation_emits_bounded_repair_trajectory():
    runtime = RepairRuntime()
    events = []
    result = await generate_structured(
        runtime,
        Output,
        "Return data",
        "Build output",
        max_new_tokens=64,
        on_attempt=events.append,
    )
    assert result.value == 7
    assert runtime.calls == 2
    assert [event["status"] for event in events] == [
        "running", "retrying", "running", "validated"
    ]


async def test_structured_generation_repairs_semantically_invalid_output():
    runtime = RepairRuntime()
    runtime.generate = _semantic_generate(runtime)
    result = await generate_structured(
        runtime,
        Output,
        "Return data",
        "Build output",
        max_new_tokens=64,
        validate_result=lambda output: "Value must be positive" if output.value <= 0 else None,
    )
    assert result.value == 7
    assert runtime.calls == 2


def _semantic_generate(runtime):
    async def generate(_messages, max_new_tokens, **_options):
        assert max_new_tokens == 64
        runtime.calls += 1
        return '{"value": 0}' if runtime.calls == 1 else '{"value": 7}'

    return generate


def test_a_schema_echo_is_recognised():
    """The failure that took a real Smart Code run down.

    Shown a JSON Schema, Gemma 3 1B returned the schema itself — `$defs`, `properties`,
    `required` and all. It parsed, and it validated, because every field the target required
    had a default; so it arrived as an *empty* answer and the run failed reporting a missing
    edit rather than a copied contract.
    """
    from backend.structured_output import looks_like_schema_echo

    echoed = {
        "$defs": {"ProposedEdit": {"properties": {"path": {"type": "string"}}}},
        "properties": {"summary": {"title": "Summary", "type": "string"}},
        "required": ["summary", "plan"],
        "title": "SmartCodeModelOutput",
        "type": "object",
    }
    assert looks_like_schema_echo(echoed) is True


def test_a_real_answer_is_not_mistaken_for_a_schema():
    """The detector must not reject answers that merely talk about schemas."""
    from backend.structured_output import looks_like_schema_echo

    answer = {
        "summary": "Added the endpoint.",
        "plan": ["Create the router"],
        "edits": [{"action": "create", "path": "app/routes.py", "content": "x = 1\n"}],
        "findings": [],
    }
    assert looks_like_schema_echo(answer) is False
    # A finding that discusses required properties is still an answer, not a schema.
    assert looks_like_schema_echo(
        {"summary": "The request schema is missing required fields", "plan": ["Fix it"]}
    ) is False


async def test_an_example_is_shown_to_the_model_instead_of_the_schema():
    """With an example available, the prompt must not contain a schema to copy."""
    seen: dict = {}

    class CapturingRuntime:
        async def generate(self, messages, max_new_tokens, **_options):
            seen["prompt"] = messages[-1]["content"]
            return '{"value": 3}'

    await generate_structured(
        CapturingRuntime(), Output, "system", "do the thing",
        max_new_tokens=64, example={"value": 1},
    )
    assert '"value": 1' in seen["prompt"], "the example is shown"
    assert "$defs" not in seen["prompt"] and "properties" not in seen["prompt"], (
        "no schema is shown when an example is available"
    )


async def test_a_copied_example_is_rejected_and_repaired():
    """The schema echo one step along — and the more dangerous of the two.

    Replacing the schema with a worked example moved the failure rather than removing it: the
    model copied the example verbatim, producing a well-formed, plausible answer about a file
    the objective never mentioned. Unchecked, that would have been shown to a user as a real
    proposed change to a real repository.
    """
    example = {"value": 1}
    calls: list[str] = []

    class CopycatRuntime:
        async def generate(self, messages, max_new_tokens, **_options):
            calls.append(messages[-1]["content"])
            return '{"value": 1}' if len(calls) == 1 else '{"value": 9}'

    result = await generate_structured(
        CopycatRuntime(), Output, "system", "answer the objective",
        max_new_tokens=64, example=example,
    )

    assert result.value == 9, "the retry supplies a real answer"
    assert len(calls) == 2, "the copied example does not pass as an answer"
    assert "returned the example unchanged" in calls[1], "the repair names the real mistake"


async def test_reusing_the_examples_code_verbatim_is_rejected():
    """A partial copy is the dangerous case, not the wholesale one.

    Observed against Gemma 3 1B: it wrote a genuine, objective-specific summary while pasting
    the example's file content unchanged — a well-formed proposal to create a file that does
    not do what was asked. Whole-object equality never fires on that.
    """
    borrowed = "from fastapi import APIRouter and build a router here"
    example = {"value": 1, "note": borrowed}
    calls: list[str] = []

    class PartialCopycat:
        async def generate(self, messages, max_new_tokens, **_options):
            calls.append(messages[-1]["content"])
            if len(calls) == 1:
                # Own value, borrowed body — the shape of the real failure.
                return json.dumps({"value": 4, "note": borrowed})
            return json.dumps({"value": 4, "note": "a genuine answer about /omganesha"})

    result = await generate_structured(
        PartialCopycat(), Output, "system", "add /omganesha",
        max_new_tokens=64, example=example,
    )

    assert result.value == 4
    assert len(calls) == 2, "borrowed sample code must not pass as an answer"
    assert "returned the example unchanged" in calls[1]


async def test_short_shared_phrases_do_not_trip_the_copy_check():
    """Generic scaffolding wording is allowed; only substantial text counts as copying."""
    from backend.structured_output import copied_from_example

    example = {"plan": ["Create the module"], "note": "x" * 40}
    assert copied_from_example({"plan": ["Create the module"], "note": "genuinely new"}, example) is False
    assert copied_from_example({"plan": ["Something else"], "note": "x" * 40}, example) is True
