"""Small, provider-neutral helpers for schema-constrained local generations."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel

from backend.model import GemmaRuntime

T = TypeVar("T", bound=BaseModel)


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
) -> T:
    """Generate and validate JSON and workflow semantics, with one repair attempt."""
    contract = json.dumps(schema.model_json_schema(), separators=(",", ":"))
    request = (
        f"{prompt}\n\nReturn ONLY one valid JSON object. No markdown or commentary. "
        f"It must satisfy this JSON Schema:\n{contract}"
    )
    last_error: Exception | None = None
    last_text = ""
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
            messages.extend(
                [
                    {"role": "assistant", "content": last_text},
                    {
                        "role": "user",
                        "content": (
                            "The response was invalid: "
                            f"{last_error}. Return a corrected JSON object only."
                        ),
                    },
                ]
            )
        # Only forwarded when a caller asked for it, so a runtime that does not take a
        # temperature — including every test double — keeps its existing signature.
        options = {} if temperature is None else {"temperature": temperature}
        last_text = await runtime.generate(messages, max_new_tokens=max_new_tokens, **options)
        try:
            validated = schema.model_validate(parse_json_object(last_text))
            semantic_error = validate_result(validated) if validate_result else None
            if semantic_error:
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
                        "label": "Repairing invalid structured output" if attempt == 0 else "Structured output failed validation",
                        "detail": str(exc)[:500],
                    }
                )
    raise ValueError(f"The local model could not produce valid structured output: {last_error}")
