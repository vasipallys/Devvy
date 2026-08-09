"""Every model-backed workflow must carry the same grounding contract.

A local 1B model's failure mode is not refusing to answer — it is answering anyway. Asked about
a field the story never mentions it will describe a plausible one, and inside an evidence-based
product that fabrication is indistinguishable from evidence: it lands in a scorecard, gets an
evidence id, and is read by someone who was not in the room.

These tests assert the contract is present and identical everywhere, because a rule that holds
in three of four prompts is a rule the fourth workflow silently does not have.
"""

from __future__ import annotations

import inspect
import os

os.environ["PHOENIX_ENABLED"] = "false"

import pytest

from backend.harness import GROUNDING_CONTRACT, GROUNDING_CONTRACT_BRIEF, NO_INFORMATION


def chat_prompt() -> str:
    from backend.agent import SYSTEM_PROMPT

    return SYSTEM_PROMPT


def talk_prompt() -> str:
    from backend.agent_graph import TALK_SYSTEM_PROMPT

    return TALK_SYSTEM_PROMPT


def estimate_prompt() -> str:
    from backend.estimate_code import Story, build_prompt
    from backend.estimation_framework import StackProfile

    prompt, _ = build_prompt(
        Story(title="A story", user_story="A description.", stack=StackProfile())
    )
    return prompt


def smart_code_source() -> str:
    import backend.smart_code

    return inspect.getsource(backend.smart_code)


ALL_PROMPTS = {
    "chat": chat_prompt,
    "talk": talk_prompt,
    "estimate": estimate_prompt,
}


# -- The four rules ----------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(ALL_PROMPTS))
def test_every_prompt_forbids_outside_facts(name: str):
    text = ALL_PROMPTS[name]().lower()
    assert "use only the facts directly stated" in text
    assert "do not use outside facts" in text


@pytest.mark.parametrize("name", sorted(ALL_PROMPTS))
def test_every_prompt_forbids_guessing_and_extrapolation(name: str):
    text = ALL_PROMPTS[name]().lower()
    assert "do not guess" in text
    assert "extrapolate" in text


@pytest.mark.parametrize("name", sorted(ALL_PROMPTS))
def test_every_prompt_supplies_the_exact_missing_information_response(name: str):
    """The wording is fixed so the answer is recognisable to a reader and to a test."""
    assert NO_INFORMATION in ALL_PROMPTS[name]()


@pytest.mark.parametrize("name", sorted(ALL_PROMPTS))
def test_every_prompt_asks_what_would_have_to_be_added(name: str):
    """A bare refusal is not useful to the person who wrote the story."""
    text = ALL_PROMPTS[name]().lower()
    assert "added to the story" in text or "would have to be added" in text


@pytest.mark.parametrize("name", sorted(ALL_PROMPTS))
def test_every_prompt_names_the_strict_extractor_stance(name: str):
    text = ALL_PROMPTS[name]().lower()
    assert "given words and numbers" in text


def test_smart_code_carries_the_contract():
    """Checked at the source rather than the rendered prompt: the file prompt is assembled per
    file inside a coroutine, and importing the model runtime to render one is not worth it."""
    source = smart_code_source()
    assert "GROUNDING_CONTRACT_BRIEF" in source
    assert "from backend.harness import" in source


# -- One rule, one string ----------------------------------------------------------------

def test_the_contract_is_defined_once():
    """Both forms say the same four things, so neither agent gets a weaker rule than another."""
    for phrase in ("do not guess", "given words and numbers"):
        assert phrase in GROUNDING_CONTRACT.lower()
        assert phrase in GROUNDING_CONTRACT_BRIEF.lower()
    for form in (GROUNDING_CONTRACT, GROUNDING_CONTRACT_BRIEF):
        assert NO_INFORMATION in form


def test_the_contract_forbids_inventing_concrete_artefacts():
    """The specific fabrications that are hardest to spot once they are in a scorecard."""
    for form in (GROUNDING_CONTRACT, GROUNDING_CONTRACT_BRIEF):
        lowered = form.lower()
        assert "endpoint" in lowered and "table" in lowered
