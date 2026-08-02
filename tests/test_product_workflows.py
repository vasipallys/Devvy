import pytest

from backend.config import Settings
from backend.estimate_code import EstimateOutput, EstimateService, Story, parse_upload
from backend.smart_code import (
    ProposedEdit,
    SmartCodeApplyRequest,
    SmartCodeModelOutput,
    SmartCodeRequest,
    SmartCodeService,
)


class RuntimeStub:
    pass


async def test_smart_code_preview_and_approved_atomic_apply(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")
    settings = Settings(app_data_dir=tmp_path / "data", phoenix_enabled=False)
    service = SmartCodeService(RuntimeStub(), settings)

    async def structured(*_args, **_kwargs):
        return SmartCodeModelOutput(
            summary="Update the configured value.",
            plan=["Replace the value", "Verify Python syntax"],
            edits=[
                ProposedEdit(
                    action="replace",
                    path="app.py",
                    content="value = 2\n",
                    reason="Requested behavior",
                )
            ],
        )

    monkeypatch.setattr("backend.smart_code.generate_structured", structured)
    preview = await service.preview(
        SmartCodeRequest(
            objective="Change the configured value",
            workspace_root=str(workspace),
            target_paths=[str(target)],
        )
    )
    assert preview["can_apply"] is True
    assert "-value = 1" in preview["diffs"]["app.py"]
    assert preview["evidence"]["selection"] == "explicit targets"
    assert target.read_text(encoding="utf-8") == "value = 1\n"

    result = service.apply(
        SmartCodeApplyRequest(preview_token=preview["preview_token"], approved=True)
    )
    assert target.read_text(encoding="utf-8") == "value = 2\n"
    assert result["applied"][0]["path"] == "app.py"


async def test_estimate_enforces_high_uncertainty_escalation(tmp_path, monkeypatch):
    scorecard = [
        {
            "parameter": name,
            "score": "High" if name == "uncertainty" else "Low",
            "reason": "Evidence supports this score.",
        }
        for name in (
            "complexity", "volume", "uncertainty", "react_scope", "spring_scope",
            "existing_code_scope", "dependencies", "nfrs", "testing",
            "compliance_audit", "familiarity", "dod_overhead",
        )
    ]
    model_output = EstimateOutput.model_validate(
        {
            "scorecard": scorecard,
            "drivers": ["uncertainty", "dependencies"],
            "drivers_explanation": "Unknown integration behavior dominates delivery risk.",
            "anchor_comparison": "Larger than the bounded preference anchor.",
            "anchor_titles": ["Entitlement-protected account preference"],
            "points": 8,
            "points_derivation": "The integration uncertainty moves this to 8.",
            "plain_language_why": "Unknown integration behavior makes this larger than a routine change.",
            "tldr": "8 - Integration uncertainty is the main driver.",
            "effort": {
                "react": "Small", "spring": "Medium", "existing_code": "Medium",
                "person_days": {"optimistic": 3, "likely": 5, "pessimistic": 8},
            },
            "hidden_tasks": [],
            "risks": [{"risk": "Unknown API", "mitigation_or_assumption": "Run a spike"}],
            "assumptions": ["The API is reachable"],
            "spike_recommended": False,
            "spike_reason": None,
            "split_recommendation": {
                "split_recommended": False, "rationale": "The story remains cohesive",
                "proposed_stories": [],
            },
        }
    )

    async def structured(*_args, **_kwargs):
        return model_output

    monkeypatch.setattr("backend.estimate_code.generate_structured", structured)
    service = EstimateService(
        RuntimeStub(), Settings(app_data_dir=tmp_path / "data", phoenix_enabled=False)
    )
    result = await service.estimate(Story(title="Integrate vendor API"))
    assert result["points"] == 8
    assert result["spike_recommended"] is True
    assert result["spike_reason"]
    assert result["evidence"]["policy_checks"]["high_uncertainty_forces_spike"] is True


async def test_estimate_normalizes_compact_local_model_output(tmp_path):
    class CompactRuntime:
        def __init__(self):
            self.calls = 0

        async def generate(self, _messages, max_new_tokens):
            assert max_new_tokens > 0
            self.calls += 1
            return """{
                "Score": 13,
                "EffortRange": {
                    "react": 5,
                    "spring": 5,
                    "existing_code": 5,
                    "person_days": 8
                },
                "ComplexityDrivers": ["security", "cross-stack integration"],
                "Explanation": "Biometric authentication has security and integration risk.",
                "HiddenTasks": ["Threat modelling", "Device compatibility testing"],
                "Risks": ["Biometric hardware variance", "Authentication failures"],
                "SplitRecommendation": true
            }"""

    runtime = CompactRuntime()
    service = EstimateService(
        runtime, Settings(app_data_dir=tmp_path / "data", phoenix_enabled=False)
    )
    result = await service.estimate(
        Story(
            title="Add biometric login",
            user_story="Users authenticate with device biometrics through the existing service.",
            acceptance_criteria=["Authentication failures are handled securely"],
        )
    )

    assert runtime.calls == 1
    assert result["points"] == 13
    assert len(result["scorecard"]) == 12
    assert result["effort"]["person_days"] == {
        "optimistic": 8.0,
        "likely": 8.0,
        "pessimistic": 21.0,
    }
    assert result["hidden_tasks"][0]["task"] == "Threat modelling"
    assert result["risks"][0]["risk"] == "Biometric hardware variance"
    assert result["spike_recommended"] is True
    assert result["split_recommendation"]["split_recommended"] is True


def test_estimate_csv_upload_mapping():
    payload = parse_upload(
        b"Title,Description,Acceptance Criteria\nAdd search,Filter records,Returns matches\n",
        "stories.csv",
    )
    assert payload["row_count"] == 1
    assert payload["suggested_mapping"]["title"] == "Title"


def test_smart_code_normalizes_compact_model_edit_aliases():
    output = SmartCodeModelOutput.model_validate(
        {
            "summary": "Create a greeting",
            "plan": ["Create the file"],
            "edits": [
                {
                    "operation": "create",
                    "path": "greeting.py",
                    "content": 'print("hello")\n',
                }
            ],
        }
    )
    assert output.edits[0].action == "create"
    assert output.edits[0].reason


async def test_smart_code_modify_can_seed_empty_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "empty-workspace"
    workspace.mkdir()
    service = SmartCodeService(
        RuntimeStub(), Settings(app_data_dir=tmp_path / "data", phoenix_enabled=False)
    )

    async def structured(*_args, **_kwargs):
        return SmartCodeModelOutput.model_validate(
            {
                "summary": "Create the first source file",
                "plan": ["Create main.py"],
                "edits": [
                    {"operation": "create", "path": "main.py", "content": "value = 1\n"}
                ],
            }
        )

    monkeypatch.setattr("backend.smart_code.generate_structured", structured)
    preview = await service.preview(
        SmartCodeRequest(
            objective="Create the initial Python module",
            workspace_root=str(workspace),
            mode="modify",
        )
    )
    assert preview["can_apply"] is True
    assert preview["edits"][0]["action"] == "create"


async def test_smart_code_repairs_empty_edit_response(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "app.py"
    target.write_text("value = 1\n", encoding="utf-8")

    class EmptyThenEditRuntime:
        def __init__(self):
            self.calls = 0

        async def generate(self, messages, max_new_tokens):
            assert max_new_tokens > 0
            self.calls += 1
            if self.calls == 1:
                return '{"summary":"Planned","plan":["Update value"],"edits":[]}'
            assert "requires at least one complete" in messages[-1]["content"]
            return """{
                "summary": "Updated value",
                "plan": ["Update value"],
                "files": {"app.py": "value = 2\\n"}
            }"""

    runtime = EmptyThenEditRuntime()
    service = SmartCodeService(
        runtime, Settings(app_data_dir=tmp_path / "data", phoenix_enabled=False)
    )
    preview = await service.preview(
        SmartCodeRequest(
            objective="Change value to 2",
            workspace_root=str(workspace),
            target_paths=[str(target)],
        )
    )

    assert runtime.calls == 2
    assert preview["can_apply"] is True
    assert preview["edits"][0]["action"] == "replace"
    assert "+value = 2" in preview["diffs"]["app.py"]


async def test_smart_code_rejects_workspace_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = SmartCodeService(
        RuntimeStub(), Settings(app_data_dir=tmp_path / "data", phoenix_enabled=False)
    )
    with pytest.raises(ValueError, match="outside"):
        # Validation happens before the model is called.
        await service.preview(
            SmartCodeRequest(
                objective="Review the file",
                workspace_root=str(workspace),
                mode="review",
                target_paths=[str(tmp_path / "outside.py")],
            )
        )
