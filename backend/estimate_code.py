"""Evidence-led story estimation using Devvy's one shared local model runtime."""

from __future__ import annotations

import csv
import io
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.config import Settings
from backend.model import GemmaRuntime
from backend.structured_output import generate_structured

PARAMETERS = [
    "complexity", "volume", "uncertainty", "react_scope", "spring_scope",
    "existing_code_scope", "dependencies", "nfrs", "testing", "compliance_audit",
    "familiarity", "dod_overhead",
]

ANCHORS = [
    {"title": "Inline validation on a React payment form", "points": 3,
     "rationale": "React-only, established patterns, modest tests."},
    {"title": "Entitlement-protected account preference", "points": 5,
     "rationale": "Bounded cross-stack change with entitlement and audit coverage."},
    {"title": "Search and filter an existing transaction endpoint", "points": 5,
     "rationale": "Cross-stack but bounded database, UI, and performance work."},
    {"title": "Cross-market eKYC status integration", "points": 8,
     "rationale": "External integration, regulatory rules, failure handling, and audit."},
    {"title": "Transaction-wide AI summary with audit", "points": 8,
     "rationale": "Broad data work with consistency, compliance, and operational uncertainty."},
    {"title": "New multi-market payment orchestration journey", "points": 13,
     "rationale": "Multiple new layers and dependencies; must be split before delivery."},
]


class Story(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    user_story: str = Field(default="", max_length=20_000)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=50)
    technical_breakdown: str | None = Field(default=None, max_length=20_000)
    existing_points: float | None = None
    key: str | None = None
    status: str | None = None
    labels: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    source: Literal["manual", "jira", "upload"] = "manual"

    @field_validator("acceptance_criteria", mode="before")
    @classmethod
    def normalize_criteria(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [item.strip() for item in value.replace(";", "\n").splitlines() if item.strip()]
        return [str(item).strip() for item in value if str(item).strip()]


class EstimateRequest(BaseModel):
    story: Story


class BatchEstimateRequest(BaseModel):
    stories: list[Story] = Field(min_length=1, max_length=100)


class JiraWriteRequest(BaseModel):
    points: Literal[1, 2, 3, 5, 8, 13]
    confirm: bool = False


class ParameterScore(BaseModel):
    parameter: Literal[
        "complexity", "volume", "uncertainty", "react_scope", "spring_scope",
        "existing_code_scope", "dependencies", "nfrs", "testing", "compliance_audit",
        "familiarity", "dod_overhead",
    ]
    score: Literal["Low", "Medium", "High"]
    reason: str = Field(min_length=3, max_length=240)


class EffortRange(BaseModel):
    optimistic: float = Field(ge=0)
    likely: float = Field(ge=0)
    pessimistic: float = Field(ge=0)


class LayerEffort(BaseModel):
    react: str
    spring: str
    existing_code: str
    person_days: EffortRange


class HiddenTask(BaseModel):
    task: str
    weight: str


class Risk(BaseModel):
    risk: str
    mitigation_or_assumption: str


class SplitRecommendation(BaseModel):
    split_recommended: bool
    rationale: str
    proposed_stories: list[str] = Field(default_factory=list, max_length=6)


class EstimateOutput(BaseModel):
    scorecard: list[ParameterScore] = Field(min_length=12, max_length=12)
    drivers: list[str] = Field(min_length=2, max_length=3)
    drivers_explanation: str
    anchor_comparison: str
    anchor_titles: list[str] = Field(min_length=1, max_length=3)
    points: Literal[1, 2, 3, 5, 8, 13]
    points_derivation: str
    plain_language_why: str
    tldr: str
    effort: LayerEffort
    hidden_tasks: list[HiddenTask]
    risks: list[Risk] = Field(min_length=1, max_length=3)
    assumptions: list[str]
    spike_recommended: bool
    spike_reason: str | None = None
    split_recommendation: SplitRecommendation

    @model_validator(mode="after")
    def validate_scorecard(self) -> "EstimateOutput":
        found = {item.parameter for item in self.scorecard}
        if found != set(PARAMETERS):
            raise ValueError("Scorecard must contain each of the 12 parameters exactly once")
        return self


class EstimateDraft(BaseModel):
    """Small, tolerant boundary contract for a resource-constrained local model.

    The model supplies semantic signals; application code below owns the stable API
    shape and all delivery policy. This prevents harmless key casing or compact list
    choices from turning a useful estimate into a failed request.
    """

    scores: Any = Field(default_factory=dict)
    drivers: Any = Field(default_factory=list)
    points: Any = None
    rationale: Any = ""
    anchor_titles: Any = Field(default_factory=list)
    effort: Any = Field(default_factory=dict)
    hidden_tasks: Any = Field(default_factory=list)
    risks: Any = Field(default_factory=list)
    assumptions: Any = Field(default_factory=list)
    spike_recommended: Any = False
    spike_reason: Any = None
    split_recommended: Any = False
    split_rationale: Any = ""
    proposed_stories: Any = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def accept_compact_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = {
            re.sub(r"[^a-z0-9]", "", str(key).lower()): item
            for key, item in value.items()
        }

        def pick(*names: str, default: Any = None) -> Any:
            for name in names:
                key = re.sub(r"[^a-z0-9]", "", name.lower())
                if key in normalized:
                    return normalized[key]
            return default

        split = pick("split_recommendation", "split", default={})
        split_data = split if isinstance(split, dict) else {}
        return {
            "scores": pick("scores", "scorecard", "parameter_scores", default={}),
            "drivers": pick(
                "drivers", "complexity_drivers", "key_drivers", default=[]
            ),
            "points": pick("points", "score", "story_points"),
            "rationale": pick(
                "rationale", "explanation", "plain_language_why", "reason", default=""
            ),
            "anchor_titles": pick("anchor_titles", "anchors", default=[]),
            "effort": pick("effort", "effort_range", "effortrange", default={}),
            "hidden_tasks": pick("hidden_tasks", "hiddentasks", "tasks", default=[]),
            "risks": pick("risks", "risk", default=[]),
            "assumptions": pick("assumptions", default=[]),
            "spike_recommended": pick("spike_recommended", "spike", default=False),
            "spike_reason": pick("spike_reason"),
            "split_recommended": split_data.get(
                "split_recommended", split if isinstance(split, bool) else False
            ),
            "split_rationale": split_data.get(
                "rationale", pick("split_rationale", default="")
            ),
            "proposed_stories": split_data.get(
                "proposed_stories", pick("proposed_stories", default=[])
            ),
        }

    @model_validator(mode="after")
    def require_useful_signal(self) -> "EstimateDraft":
        if self.points is None and not self.drivers and not self.rationale and not self.scores:
            raise ValueError("At least one estimation signal is required")
        return self


def _items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _text(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    if isinstance(value, dict):
        for key in ("reason", "rationale", "description", "text", "title", "name"):
            if value.get(key):
                return str(value[key]).strip()
        return fallback
    result = str(value).strip()
    return result or fallback


def _flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"true", "yes", "y", "1", "recommended"}


def _score(value: Any) -> Literal["Low", "Medium", "High"] | None:
    if isinstance(value, dict):
        value = value.get("score", value.get("value", value.get("level")))
    cleaned = str(value).strip().lower()
    if cleaned in {"low", "l", "1", "2"}:
        return "Low"
    if cleaned in {"medium", "med", "m", "3"}:
        return "Medium"
    if cleaned in {"high", "h", "4", "5"}:
        return "High"
    return None


def _story_evidence(story: Story) -> str:
    return " ".join(
        [
            story.title,
            story.user_story,
            " ".join(story.acceptance_criteria),
            story.technical_breakdown or "",
            " ".join(story.labels),
            " ".join(story.components),
        ]
    ).lower()


def _fallback_score(parameter: str, story: Story) -> tuple[Literal["Low", "Medium", "High"], str]:
    evidence = _story_evidence(story)
    groups = {
        "react_scope": ("react", "frontend", "ui", "screen", "form"),
        "spring_scope": ("spring", "backend", "api", "service", "database"),
        "existing_code_scope": ("existing", "legacy", "migration", "refactor"),
        "dependencies": ("vendor", "integration", "external", "dependency", "third-party"),
        "nfrs": ("performance", "availability", "latency", "scale", "resilien"),
        "testing": ("test", "qa", "regression", "automation"),
        "compliance_audit": (
            "compliance", "audit", "security", "biometric", "auth", "regulated"
        ),
        "dod_overhead": ("deploy", "monitor", "document", "release", "rollout"),
    }
    if parameter == "uncertainty":
        sparse = not story.acceptance_criteria or len(evidence.strip()) < 120
        return (
            ("High", "Acceptance evidence is incomplete; uncertainty is kept explicit.")
            if sparse
            else ("Medium", "Story evidence exists, but implementation unknowns remain.")
        )
    if parameter == "complexity":
        matched_layers = sum(
            any(term in evidence for term in groups[name])
            for name in ("react_scope", "spring_scope", "dependencies", "compliance_audit")
        )
        if matched_layers >= 3:
            return "High", "The story crosses several technical or assurance boundaries."
        if matched_layers >= 1:
            return "Medium", "The story names at least one non-trivial delivery boundary."
    if parameter == "volume":
        if any(term in evidence for term in ("bulk", "batch", "all users", "large", "multi-")):
            return "High", "The story indicates broad or high-volume behavior."
    if parameter == "familiarity":
        return "Medium", "Team familiarity is not evidenced; a neutral score is used."
    terms = groups.get(parameter, ())
    if terms and any(term in evidence for term in terms):
        return "Medium", f"The story explicitly indicates {parameter.replace('_', ' ')} work."
    return "Low", f"No explicit {parameter.replace('_', ' ')} evidence was provided."


def _scorecard(draft: EstimateDraft, story: Story) -> list[ParameterScore]:
    supplied: dict[str, Any] = {}
    if isinstance(draft.scores, dict):
        supplied = {
            re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_"): value
            for key, value in draft.scores.items()
        }
    else:
        for item in _items(draft.scores):
            if not isinstance(item, dict):
                continue
            name = item.get("parameter", item.get("name", ""))
            key = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
            if key:
                supplied[key] = item
    result = []
    for parameter in PARAMETERS:
        provided = supplied.get(parameter)
        level = _score(provided)
        if level:
            reason = _text(provided, f"The local analysis rated {parameter} {level}.")
        else:
            level, reason = _fallback_score(parameter, story)
        result.append(ParameterScore(parameter=parameter, score=level, reason=reason[:240]))
    return result


def _points(value: Any, scorecard: list[ParameterScore]) -> Literal[1, 2, 3, 5, 8, 13]:
    if isinstance(value, dict):
        value = value.get("points", value.get("score", value.get("value")))
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    if match:
        number = float(match.group())
    else:
        weights = {"Low": 1, "Medium": 2, "High": 3}
        number = sum(weights[item.score] for item in scorecard) / len(scorecard)
        number = 3 if number < 1.5 else 5 if number < 2 else 8
    allowed = (1, 2, 3, 5, 8, 13)
    return min(allowed, key=lambda candidate: (abs(candidate - number), candidate))


def _effort(draft: EstimateDraft, points: int, scorecard: list[ParameterScore]) -> LayerEffort:
    raw = draft.effort
    data = raw if isinstance(raw, dict) else {}
    days = data.get("person_days", data.get("personDays", data))
    if isinstance(days, dict):
        optimistic = days.get("optimistic", days.get("min"))
        likely = days.get("likely", days.get("expected", days.get("most_likely")))
        pessimistic = days.get("pessimistic", days.get("max"))
    else:
        likely = days if isinstance(days, (int, float)) else None
        optimistic = pessimistic = None
    defaults = {
        1: (0.5, 1, 2), 2: (1, 2, 3), 3: (2, 3, 5),
        5: (3, 5, 8), 8: (5, 8, 13), 13: (8, 13, 21),
    }[points]
    likely = float(likely) if isinstance(likely, (int, float)) else defaults[1]
    optimistic = (
        float(optimistic) if isinstance(optimistic, (int, float)) else min(defaults[0], likely)
    )
    pessimistic = (
        float(pessimistic) if isinstance(pessimistic, (int, float)) else max(defaults[2], likely)
    )
    by_parameter = {item.parameter: item.score for item in scorecard}

    def layer(name: str, parameter: str) -> str:
        value = data.get(name)
        return _text(value, f"{by_parameter[parameter]} scope")

    return LayerEffort(
        react=layer("react", "react_scope"),
        spring=layer("spring", "spring_scope"),
        existing_code=layer("existing_code", "existing_code_scope"),
        person_days=EffortRange(
            optimistic=max(0, optimistic),
            likely=max(0, likely),
            pessimistic=max(0, pessimistic),
        ),
    )


def _normalize_draft(draft: EstimateDraft, story: Story) -> EstimateOutput:
    scorecard = _scorecard(draft, story)
    points = _points(draft.points, scorecard)
    high_parameters = [item.parameter for item in scorecard if item.score == "High"]
    drivers = [_text(item) for item in _items(draft.drivers) if _text(item)][:3]
    for parameter in high_parameters + ["complexity", "uncertainty"]:
        readable = parameter.replace("_", " ")
        if len(drivers) >= 2:
            break
        if readable not in drivers:
            drivers.append(readable)
    rationale = _text(
        draft.rationale,
        f"The evidence and fixed calibration anchors support a {points}-point estimate.",
    )
    anchors = [_text(item) for item in _items(draft.anchor_titles) if _text(item)][:3]
    if not anchors:
        nearest = min(ANCHORS, key=lambda anchor: abs(anchor["points"] - points))
        anchors = [nearest["title"]]
    hidden_tasks = []
    for item in _items(draft.hidden_tasks)[:8]:
        data = item if isinstance(item, dict) else {}
        task = _text(data.get("task", data.get("title", item)))
        if task:
            hidden_tasks.append(
                HiddenTask(task=task, weight=_text(data.get("weight"), "Supporting work"))
            )
    risks = []
    for item in _items(draft.risks)[:3]:
        data = item if isinstance(item, dict) else {}
        risk = _text(data.get("risk", data.get("title", item)))
        if risk:
            risks.append(
                Risk(
                    risk=risk,
                    mitigation_or_assumption=_text(
                        data.get("mitigation_or_assumption", data.get("mitigation")),
                        "Validate this risk before delivery commitment.",
                    ),
                )
            )
    if not risks:
        risks = [
            Risk(
                risk="Estimate uncertainty",
                mitigation_or_assumption="Confirm assumptions and acceptance criteria before commitment.",
            )
        ]
    assumptions = [_text(item) for item in _items(draft.assumptions) if _text(item)][:8]
    if not assumptions:
        assumptions = ["The estimate uses only the supplied story evidence and fixed anchors."]
    uncertainty = next(item.score for item in scorecard if item.parameter == "uncertainty")
    spike = _flag(draft.spike_recommended) or points == 13 or uncertainty == "High"
    split = _flag(draft.split_recommended) or points == 13
    proposed = [_text(item) for item in _items(draft.proposed_stories) if _text(item)][:6]
    split_rationale = _text(
        draft.split_rationale,
        "A 13-point story must be split before delivery." if points == 13
        else "The story can remain cohesive at its current estimated size.",
    )
    return EstimateOutput(
        scorecard=scorecard,
        drivers=drivers[:3],
        drivers_explanation=rationale,
        anchor_comparison=f"Compared with: {', '.join(anchors)}.",
        anchor_titles=anchors,
        points=points,
        points_derivation=f"Model signals were normalized to modified Fibonacci value {points}.",
        plain_language_why=rationale,
        tldr=f"{points} - {rationale}",
        effort=_effort(draft, points, scorecard),
        hidden_tasks=hidden_tasks,
        risks=risks,
        assumptions=assumptions,
        spike_recommended=spike,
        spike_reason=(
            _text(
                draft.spike_reason,
                "High uncertainty or size requires discovery before commitment.",
            )
            if spike else None
        ),
        split_recommendation=SplitRecommendation(
            split_recommended=split,
            rationale=split_rationale,
            proposed_stories=proposed,
        ),
    )


class EstimateService:
    def __init__(self, runtime: GemmaRuntime, settings: Settings):
        self.runtime = runtime
        self.settings = settings

    async def estimate(
        self,
        story: Story,
        progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if progress:
            progress(
                {
                    "stage": "prepare_context",
                    "status": "completed",
                    "label": "Story evidence normalized",
                    "evidence": {
                        "source": story.source,
                        "criteria": len(story.acceptance_criteria),
                        "anchors": len(ANCHORS),
                        "factors": len(PARAMETERS),
                    },
                }
            )
        prompt = f"""Estimate this software story for a regulated delivery team.
Score exactly these parameters once each: {', '.join(PARAMETERS)}.
Identify the 2-3 true drivers, compare named fixed anchors, then derive a modified
Fibonacci point value. A 13 must recommend a split. High uncertainty or a 13 must
recommend a spike. Never invent requirements. Keep every explanation concise and
evidence-based. The TLDR must begin with '<points> -'. Layer effort must cover React,
Spring, existing-code work and optimistic/likely/pessimistic person-days.

Return compact JSON using these keys: scores (parameter-to-Low/Medium/High object),
drivers, points, rationale, anchor_titles, effort, hidden_tasks, risks, assumptions,
spike_recommended, spike_reason, split_recommended, split_rationale, proposed_stories.
Unknown values must be omitted or described as unknown, never invented.

STORY:
{json.dumps(story.model_dump(), indent=2)}

FIXED CALIBRATION ANCHORS:
{json.dumps(ANCHORS, indent=2)}
"""
        draft = await generate_structured(
            self.runtime,
            EstimateDraft,
            (
                "You are a senior full-stack agile estimator. Be cautious, concrete, "
                "and transparent. Use modified Fibonacci points only."
            ),
            prompt,
            max_new_tokens=self.settings.estimate_max_output_tokens,
            on_attempt=(
                lambda event: progress({"stage": "structured_loop", **event}) if progress else None
            ),
        )
        output = draft if isinstance(draft, EstimateOutput) else _normalize_draft(draft, story)
        uncertainty = next(
            item.score for item in output.scorecard if item.parameter == "uncertainty"
        )
        spike = output.spike_recommended or output.points == 13 or uncertainty == "High"
        spike_reason = output.spike_reason
        if spike and not spike_reason:
            spike_reason = "High uncertainty or size requires discovery before commitment."
        split = output.split_recommendation
        if output.points == 13 and not split.split_recommended:
            split = split.model_copy(update={"split_recommended": True})
        result = output.model_dump()
        result.update(
            {
                "story": story.model_dump(),
                "spike_recommended": spike,
                "spike_reason": spike_reason,
                "split_recommendation": split.model_dump(),
                "evidence": {
                    "source": story.source,
                    "score_factors": PARAMETERS,
                    "anchors_considered": [item["title"] for item in ANCHORS],
                    "policy_checks": {
                        "modified_fibonacci": True,
                        "exact_scorecard": True,
                        "high_uncertainty_forces_spike": True,
                        "thirteen_forces_split": True,
                    },
                },
            }
        )
        if progress:
            progress(
                {
                    "stage": "policy_gate",
                    "status": "completed",
                    "label": "Deterministic estimation policy applied",
                    "evidence": {"points": output.points, "spike": spike, "split": split.split_recommended},
                }
            )
        return result


TARGET_ALIASES = {
    "title": ["title", "summary", "story title", "issue", "name"],
    "user_story": ["user story", "description", "story", "details", "requirement"],
    "acceptance_criteria": ["acceptance criteria", "acs", "ac", "criteria"],
    "technical_breakdown": ["technical breakdown", "technical notes", "implementation"],
    "existing_points": ["existing points", "story points", "points", "sp", "estimate"],
}


def _mapping(columns: list[str]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    used: set[str] = set()
    for target, aliases in TARGET_ALIASES.items():
        scored: list[tuple[float, str]] = []
        for column in columns:
            if column in used:
                continue
            cleaned = re.sub(r"[^a-z0-9]+", " ", column.lower()).strip()
            score = max(
                1.0 if cleaned == alias else 0.9 if alias in cleaned else
                SequenceMatcher(None, cleaned, alias).ratio()
                for alias in aliases
            )
            scored.append((score, column))
        score, column = max(scored, default=(0.0, ""))
        result[target] = column if score >= 0.55 else None
        if result[target]:
            used.add(column)
    return result


def parse_upload(content: bytes, filename: str) -> dict[str, Any]:
    suffix = Path(filename).suffix.lower()
    if len(content) > 15 * 1024 * 1024:
        raise ValueError("File exceeds the 15 MB upload limit.")
    rows: list[dict[str, Any]]
    if suffix == ".csv":
        text = content.decode("utf-8-sig", errors="replace")
        rows = [dict(row) for row in csv.DictReader(io.StringIO(text))]
    elif suffix == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise ValueError("Excel support is not installed. Run the setup script again.") from exc
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        sheet = workbook.active
        values = sheet.iter_rows(values_only=True)
        headers = [str(value or "") for value in next(values, [])]
        rows = [
            {headers[index]: "" if value is None else value for index, value in enumerate(row)}
            for row in values
        ]
    else:
        raise ValueError("Use a .csv or .xlsx file.")
    columns = list(rows[0].keys()) if rows else []
    return {
        "columns": columns,
        "suggested_mapping": _mapping(columns),
        "preview": rows[:20],
        "rows": rows[:100],
        "row_count": len(rows),
    }


def rows_to_stories(rows: list[dict[str, Any]], mapping: dict[str, str | None]) -> list[Story]:
    title_column = mapping.get("title")
    if not title_column:
        raise ValueError("Map a source column to Title before estimating.")
    stories: list[Story] = []
    for row in rows[:100]:
        title = str(row.get(title_column, "")).strip()
        if not title:
            continue
        raw_points = row.get(mapping.get("existing_points") or "", "")
        try:
            points = float(raw_points) if str(raw_points).strip() else None
        except (TypeError, ValueError):
            points = None
        stories.append(
            Story(
                title=title,
                user_story=str(row.get(mapping.get("user_story") or "", "")).strip(),
                acceptance_criteria=row.get(mapping.get("acceptance_criteria") or "", ""),
                technical_breakdown=(
                    str(row.get(mapping.get("technical_breakdown") or "", "")).strip() or None
                ),
                existing_points=points,
                source="upload",
            )
        )
    if not stories:
        raise ValueError("No valid story rows remain after mapping.")
    return stories


async def jira_issues(settings: Settings, project: str, query: str = "") -> list[dict]:
    if not (settings.jira_base_url and settings.jira_email and settings.jira_api_token):
        raise ValueError("Jira is not configured in the Devvy environment.")
    jql = f'project = "{project.replace(chr(34), "")}"'
    if query.strip():
        jql += f' AND text ~ "{query.replace(chr(34), "")}"'
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{settings.jira_base_url.rstrip('/')}/rest/api/3/search",
            params={
                "jql": jql,
                "maxResults": 100,
                "fields": (
                    "summary,description,status,labels,components,"
                    f"{settings.jira_story_points_field}"
                ),
            },
            auth=(settings.jira_email, settings.jira_api_token),
        )
        response.raise_for_status()
    issues = []
    for issue in response.json().get("issues", []):
        fields = issue.get("fields", {})
        issues.append(
            Story(
                title=fields.get("summary") or issue["key"],
                user_story=json.dumps(fields.get("description") or ""),
                existing_points=fields.get(settings.jira_story_points_field),
                key=issue["key"],
                status=(fields.get("status") or {}).get("name"),
                labels=fields.get("labels") or [],
                components=[item.get("name", "") for item in fields.get("components") or []],
                source="jira",
            ).model_dump()
        )
    return issues


async def write_jira_points(settings: Settings, issue_key: str, points: int) -> None:
    if not settings.jira_write_enabled:
        raise ValueError("Jira write-back is disabled by configuration.")
    if not (settings.jira_base_url and settings.jira_email and settings.jira_api_token):
        raise ValueError("Jira is not configured in the Devvy environment.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]+-\d+", issue_key):
        raise ValueError("Invalid Jira issue key.")
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.put(
            f"{settings.jira_base_url.rstrip('/')}/rest/api/3/issue/{issue_key}",
            json={"fields": {settings.jira_story_points_field: points}},
            auth=(settings.jira_email, settings.jira_api_token),
        )
        response.raise_for_status()
