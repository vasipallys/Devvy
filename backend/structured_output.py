"""Small, provider-neutral helpers for schema-constrained local generations."""

from __future__ import annotations

import json
from typing import TypeVar

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
) -> T:
    """Generate and validate JSON, with one concise repair attempt."""
    contract = json.dumps(schema.model_json_schema(), separators=(",", ":"))
    request = (
        f"{prompt}\n\nReturn ONLY one valid JSON object. No markdown or commentary. "
        f"It must satisfy this JSON Schema:\n{contract}"
    )
    last_error: Exception | None = None
    last_text = ""
    for attempt in range(2):
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
        last_text = await runtime.generate(messages, max_new_tokens=max_new_tokens)
        try:
            return schema.model_validate(parse_json_object(last_text))
        except Exception as exc:
            last_error = exc
    raise ValueError(f"The local model could not produce valid structured output: {last_error}")
