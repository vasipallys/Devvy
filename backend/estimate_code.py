"""Evidence-led story estimation using Devvy's one shared local model runtime."""

from __future__ import annotations

import csv
import io
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
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


class EstimateService:
    def __init__(self, runtime: GemmaRuntime, settings: Settings):
        self.runtime = runtime
        self.settings = settings

    async def estimate(self, story: Story) -> dict[str, Any]:
        prompt = f"""Estimate this software story for a regulated delivery team.
Score exactly these parameters once each: {', '.join(PARAMETERS)}.
Identify the 2-3 true drivers, compare named fixed anchors, then derive a modified
Fibonacci point value. A 13 must recommend a split. High uncertainty or a 13 must
recommend a spike. Never invent requirements. Keep every explanation concise and
evidence-based. The TLDR must begin with '<points> -'. Layer effort must cover React,
Spring, existing-code work and optimistic/likely/pessimistic person-days.

STORY:
{json.dumps(story.model_dump(), indent=2)}

FIXED CALIBRATION ANCHORS:
{json.dumps(ANCHORS, indent=2)}
"""
        output = await generate_structured(
            self.runtime,
            EstimateOutput,
            (
                "You are a senior full-stack agile estimator. Be cautious, concrete, "
                "and transparent. Use modified Fibonacci points only."
            ),
            prompt,
            max_new_tokens=self.settings.estimate_max_output_tokens,
        )
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
