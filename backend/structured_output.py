"""Small, provider-neutral helpers for schema-constrained local generations."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from backend.model import GemmaRuntime

T = TypeVar("T", bound=BaseModel)


#: Keys that only ever appear in a JSON *Schema*, never in an instance of one.
_SCHEMA_MARKERS = ("$defs", "$schema", "properties", "required", "additionalProperties")


def looks_like_schema_echo(payload: object) -> bool:
    """Whether the model returned the contract instead of an answer to it.

    A compact model shown a JSON Schema frequently copies it: the schema is the most recent
    and most structured JSON in its context, and reproducing it is the most literal way to
    satisfy "return one valid JSON object". The copy parses cleanly, and validates whenever
    the target's required fields have defaults — so it passes as an *empty* answer, fails the
    workflow rule instead of the contract, and burns both attempts reporting the wrong
    problem. Naming it is what lets the retry ask for the right thing.
    """
    if not isinstance(payload, dict):
        return False
    markers = sum(1 for marker in _SCHEMA_MARKERS if marker in payload)
    if markers >= 2:
        return True
    properties = payload.get("properties")
    return bool(
        markers
        and isinstance(properties, dict)
        and any(isinstance(item, dict) and "type" in item for item in properties.values())
    )


#: Shortest example string treated as "substantial" enough that reproducing it verbatim means
#: copying rather than answering. Generic scaffolding phrases ("Create the router module") sit
#: below it and are allowed; a body of sample code does not.
_COPY_LENGTH = 30


def _substantial_strings(value: object, found: set[str] | None = None) -> set[str]:
    """Every long string leaf anywhere in a JSON-like structure."""
    found = set() if found is None else found
    if isinstance(value, str):
        if len(value.strip()) >= _COPY_LENGTH:
            found.add(value.strip())
    elif isinstance(value, dict):
        for item in value.values():
            _substantial_strings(item, found)
    elif isinstance(value, list):
        for item in value:
            _substantial_strings(item, found)
    return found


def copied_from_example(payload: object, example: object) -> bool:
    """Whether the answer reuses the example's substantial text verbatim.

    Exact equality of the whole object is not enough. Observed against Gemma 3 1B: it wrote a
    genuine, objective-specific ``summary`` while pasting the example's file ``content``
    unchanged — a well-formed proposal to create a file that does not do what was asked. That
    is worse than an outright failure, because it looks like a real answer.
    """
    if payload == example:
        # Wholesale copy. Caught separately because an example made only of short or numeric
        # values has no substantial string to match on.
        return True
    borrowed = _substantial_strings(example)
    return bool(borrowed and _substantial_strings(payload) & borrowed)


def parse_json_object(text: str) -> dict:
    """Find the first valid JSON object in a model response."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```")
        cleaned = cleaned.removesuffix("```").strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(cleaned[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError("The local model did not return a valid JSON object.")


async def generate_structured(
    runtime: GemmaRuntime,
    schema: type[T],
    system: str,
    prompt: str,
    *,
    max_new_tokens: int,
    on_attempt: Callable[[dict[str, Any]], None] | None = None,
    validate_result: Callable[[T], str | None] | None = None,
    temperature: float | None = None,
    allow_degraded: bool = False,
    example: dict[str, Any] | None = None,
) -> T:
    """Generate and validate JSON and workflow semantics, with one repair attempt.

    ``allow_degraded`` returns the last answer that satisfied the *schema* even when the
    workflow rule it had to satisfy was never met. That distinction matters: a response the
    application can read but cannot act on still contains the model's plan and analysis, and
    handing that back — clearly labelled as incomplete — beats discarding several minutes of
    CPU work and leaving the user with nothing but an error. The caller decides what a
    degraded result may be used for; it MUST NOT be treated as a complete one.
    """
    # A worked example beats a schema for a small local model. Handed a JSON Schema, it
    # reproduces the schema — that is the most literal reading of "return one valid JSON
    # object", and the nearest structured text to copy. Handed a filled-in instance, it fills
    # in an instance. The schema is still the contract; it is simply not what gets shown when
    # the caller can supply something concrete to imitate.
    if example is not None:
        shape = json.dumps(example, indent=2)
        request = (
            f"{prompt}\n\nSHAPE ONLY — the JSON below illustrates the structure. Its values "
            "describe a different, unrelated task and MUST NOT appear in your answer:\n"
            f"{shape}\n\nNow reply with ONE JSON object using that same structure and your own "
            "values for the objective above. No markdown, no commentary, no schema, and do not "
            "copy the example."
        )
    else:
        contract = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        request = (
            f"{prompt}\n\nReturn ONLY one valid JSON object holding your answer. Do not repeat "
            "the schema and do not return field definitions — return DATA satisfying this "
            f"JSON Schema:\n{contract}"
        )
    last_error: Exception | None = None
    last_text = ""
    last_truncated = False
    #: Parsed and schema-valid, but failed the caller's workflow rule.
    degraded: T | None = None
    for attempt in range(2):
        if on_attempt:
            on_attempt(
                {
                    "attempt": attempt + 1,
                    "max_attempts": 2,
                    "status": "running",
                    "label": "Generating schema-constrained output",
                }
            )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": request},
        ]
        if attempt:
            # A run that was cut at the token ceiling did not get the contract wrong — it ran
            # out of room. Replaying its truncated output and asking for a correction invites
            # the same overflow, so the retry asks for something that fits instead.
            correction = (
                "Your previous answer was cut off because it exceeded the output limit. "
                "Return a smaller JSON object: keep it to a single file and the minimum "
                "code that satisfies the objective, with no commentary."
                if last_truncated
                else f"The response was invalid: {last_error}. Return a corrected JSON object only."
            )
            if not last_truncated:
                messages.append({"role": "assistant", "content": last_text})
            messages.append({"role": "user", "content": correction})
        # Only forwarded when a caller asked for it, so a runtime that does not take a
        # temperature — including every test double — keeps its existing signature.
        options = {} if temperature is None else {"temperature": temperature}
        completion: dict = {}
        last_text = await runtime.generate(
            messages, max_new_tokens=max_new_tokens, stats=completion, **options
        )
        last_truncated = bool(completion.get("truncated"))
        try:
            payload = parse_json_object(last_text)
            if example is not None and copied_from_example(payload, example):
                # Copying the example is the schema echo one step along: the model imitates
                # the nearest structured text instead of answering. It is the more dangerous
                # of the two, because the result is well-formed and plausible — it would be
                # presented as a real proposed change to a real repository.
                raise ValueError(
                    "You returned the example unchanged. It describes a different task. "
                    "Answer the stated objective using your own values."
                )
            if looks_like_schema_echo(payload):
                raise ValueError(
                    "You returned the JSON Schema itself instead of an answer. Return a JSON "
                    "object holding actual values, with no 'properties', '$defs', or "
                    "'required' keys."
                )
            validated = schema.model_validate(payload)
            semantic_error = validate_result(validated) if validate_result else None
            if semantic_error:
                degraded = validated
                raise ValueError(semantic_error)
            if on_attempt:
                on_attempt(
                    {
                        "attempt": attempt + 1,
                        "max_attempts": 2,
                        "status": "validated",
                        "label": "Structured output validated",
                    }
                )
            return validated
        except Exception as exc:
            last_error = exc
            if on_attempt:
                on_attempt(
                    {
                        "attempt": attempt + 1,
                        "max_attempts": 2,
                        "status": "retrying" if attempt == 0 else "failed",
                        "label": (
                            "Output cut at the token limit — retrying smaller" if last_truncated
                            else "Repairing invalid structured output" if attempt == 0
                            else "Structured output failed validation"
                        ),
                        "detail": str(exc)[:500],
                        # What the model actually produced. Without it a failure is only ever
                        # "invalid output", which is unactionable — the reader cannot tell a
                        # model that ignored the contract from one that ran out of room.
                        "evidence": {
                            "truncated": last_truncated,
                            "completion_tokens": completion.get("completion_tokens"),
                            "max_new_tokens": completion.get("max_new_tokens"),
                            "raw_output_preview": last_text[:400],
                        },
                    }
                )
    if allow_degraded and degraded is not None:
        if on_attempt:
            on_attempt(
                {
                    "attempt": 2,
                    "max_attempts": 2,
                    "status": "failed",
                    "label": "Returning an incomplete result rather than nothing",
                    "detail": str(last_error)[:500],
                    "evidence": {"degraded": True, "reason": str(last_error)[:300]},
                }
            )
        return degraded
    if last_truncated:
        raise ValueError(
            "The local model ran out of output room before completing valid JSON "
            f"(hit the {max_new_tokens}-token limit). Ask for a smaller change, target one "
            "file, or raise SMART_CODE_MAX_OUTPUT_TOKENS."
        )
    raise ValueError(f"The local model could not produce valid structured output: {last_error}")
