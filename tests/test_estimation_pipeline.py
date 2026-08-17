import json

from backend.config import Settings
from backend.estimate_code import EstimateService, Story
from backend.estimation_framework import FACTOR_IDS, StackProfile


#: A plausible spread rather than one value repeated sixteen times. A scorecard where every
#: factor carries the same middle score is rejected by `_validate_draft` — it is valid JSON
#: carrying no information, and it is the single largest cause of clustered estimates. Fixtures
#: that used one were not exercising the path a real model takes.
_BASELINE = (2, 3, 2, 1, 2, 3, 3, 1, 2, 2, 1, 2, 2, 2, 2, 3)


def scorecard(**overrides: int) -> dict[str, dict[str, object]]:
    defaults = dict(zip(FACTOR_IDS, _BASELINE, strict=True))
    return {
        factor: {
            "score": overrides.get(factor, defaults[factor]),
            "why": f"Evidence supports level {overrides.get(factor, defaults[factor])} for {factor}.",
        }
        for factor in FACTOR_IDS
    }


class SequencedRuntime:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.messages: list[list[dict[str, str]]] = []

    async def generate(self, messages, max_new_tokens, **_options):
        assert max_new_tokens > 0
        self.messages.append(messages)
        return json.dumps(self.responses[len(self.messages) - 1])


async def test_agentic_pipeline_keeps_blind_review_independent_and_auditable(tmp_path):
    # About the two core passes, so the session is pinned to the cheapest technique and its
    # single call is accounted for rather than folded into the total. Planning Poker seats a
    # whole squad; that behaviour is pinned in `test_estimation_techniques.py`.
    response = {"scores": scorecard(), "drivers": ["technical_complexity"]}
    runtime = SequencedRuntime([response, response, {"size": "M", "why": "moderate"}])
    service = EstimateService(
        runtime, Settings(app_data_dir=tmp_path / "data", phoenix_enabled=False)
    )

    result = await service.estimate(
        Story(
            title="Expose order status",
            technique="tshirt",
            user_story="As a buyer, I can view order status from the existing API.",
            acceptance_criteria=["The current status is visible", "API failures are explained"],
            technical_breakdown="Add one FastAPI read endpoint and contract tests.",
            stack=StackProfile(backend="fastapi"),
        )
    )

    pipeline = result["agentic_pipeline"]
    assert len(runtime.messages) == 3  # primary, blind, and the one sizing call
    assert pipeline["reviewer"]["blind"] is True
    assert pipeline["specialist_findings"]
    assert all(item["evidence_ids"] for item in pipeline["specialist_findings"])
    assert "blind technical estimator" in runtime.messages[1][0]["content"].lower()
    assert pipeline["canonical_story"]["input_hash"].startswith("sha256:")
    assert pipeline["consistency_audit"]["calculation_replay_passed"] is True
    assert pipeline["human_review"]["status"] == "pending"
    assert pipeline["model_policy"]["hidden_chain_of_thought_stored"] is False


async def test_protected_disagreement_uses_conservative_policy_and_requires_human_review(
    tmp_path,
):
    primary = {"scores": scorecard(uncertainty=3), "drivers": ["uncertainty"]}
    reviewer = {"scores": scorecard(uncertainty=5), "drivers": ["uncertainty"]}
    service = EstimateService(
        SequencedRuntime([primary, reviewer]),
        Settings(app_data_dir=tmp_path / "data", phoenix_enabled=False),
    )

    result = await service.estimate(
        Story(
            title="Integrate an unknown vendor API",
            user_story="Connect the order workflow to an external vendor API.",
            acceptance_criteria=["Successful requests are recorded", "Failures are retryable"],
            technical_breakdown="The vendor contract and sandbox behavior are not yet validated.",
            stack=StackProfile(backend="fastapi"),
        )
    )

    pipeline = result["agentic_pipeline"]
    uncertainty = next(
        item for item in pipeline["arbitration"] if item["factor"] == "uncertainty"
    )
    assert uncertainty["selected_score"] == 5
    assert uncertainty["policy"] == "conservative protected-risk arbitration"
    assert uncertainty["human_approval_required"] is True
    assert pipeline["consistency_audit"]["status"] == "HUMAN_REVIEW_REQUIRED"
    assert result["recommendation"] == "spike_first"
