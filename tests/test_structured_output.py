from pydantic import BaseModel

from backend.structured_output import generate_structured


class Output(BaseModel):
    value: int


class RepairRuntime:
    def __init__(self):
        self.calls = 0

    async def generate(self, _messages, max_new_tokens):
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
    async def generate(_messages, max_new_tokens):
        assert max_new_tokens == 64
        runtime.calls += 1
        return '{"value": 0}' if runtime.calls == 1 else '{"value": 7}'

    return generate
