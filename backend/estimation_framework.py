"""Agile Story Point Estimation Framework v2.0 — Full-Stack Edition.

This module is the *deterministic* half of Estimate Code. The local model is only ever
asked for semantic judgement (a 1-5 score and a short reason per factor). Every number
that reaches the user — the base sum, each adjustment, the Fibonacci band, the maturity
cap, the confidence level and the final recommendation — is computed here in plain
Python from those scores.

That split is the product's core evidence claim: a reader can replay the arithmetic by
hand from the scorecard and arrive at the same story points. Nothing about the result
depends on the model having been persuasive.

Section references (``§``) point at ``agile_story_point_estimation_framework_fullstack.md``,
which is the specification this module implements.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FRAMEWORK_VERSION = "2.0"
FRAMEWORK_DOCUMENT = "agile_story_point_estimation_framework_fullstack.md"

#: The modified Fibonacci ladder the framework maps onto (§9).
FIBONACCI_POINTS = (3, 5, 8, 13, 21, 34)

Level = Literal[1, 2, 3, 4, 5]
FrontendStack = Literal["react", "angular", "none", "other"]
BackendStack = Literal["spring_boot", "flask", "fastapi", "none", "other"]
Scenario = Literal["standard", "new_framework", "framework_upgrade", "framework_migration"]
Recommendation = Literal[
    "proceed", "decompose", "spike_first", "upgrade_framework_first", "epic_discovery"
]
Confidence = Literal["High", "Medium", "Low"]


# --------------------------------------------------------------------------------------
# §2 — The 16 core estimation factors
# --------------------------------------------------------------------------------------


class FactorDefinition(BaseModel):
    """One of the 16 factors, with the rubric the model and the reader both see."""

    id: str
    number: int
    label: str
    description: str
    low_anchor: str
    high_anchor: str
    group: Literal["scope", "delivery", "assurance", "risk"]


FACTORS: tuple[FactorDefinition, ...] = (
    FactorDefinition(
        id="requirements_clarity", number=1, label="Requirements Clarity", group="risk",
        description="How well understood the story is.",
        low_anchor="Crystal clear acceptance criteria and stakeholder alignment.",
        high_anchor="Vague, conflicting, or needs discovery before work can start.",
    ),
    FactorDefinition(
        id="technical_complexity", number=2, label="Technical Complexity", group="delivery",
        description="Difficulty of the implementation approach.",
        low_anchor="Straightforward use of an established pattern.",
        high_anchor="Novel algorithm, architectural change, or deep system knowledge.",
    ),
    FactorDefinition(
        id="integration_surface", number=3, label="Integration Surface", group="scope",
        description="Number of external touchpoints.",
        low_anchor="A single component with no external touchpoints.",
        high_anchor="Multiple services, APIs, third parties, or legacy systems.",
    ),
    FactorDefinition(
        id="data_model_change", number=4, label="Data Model Change", group="scope",
        description="Impact on data structures and persistence.",
        low_anchor="No data changes.",
        high_anchor="New schema, migration, backfill, and referential integrity work.",
    ),
    FactorDefinition(
        id="frontend_effort", number=5, label="Frontend Effort", group="delivery",
        description="UI and UX development workload.",
        low_anchor="No UI work.",
        high_anchor="Complex state management, animation, or an accessibility overhaul.",
    ),
    FactorDefinition(
        id="backend_effort", number=6, label="Backend Effort", group="delivery",
        description="Server-side development workload.",
        low_anchor="No server work.",
        high_anchor="Heavy business logic, async processing, distributed transactions.",
    ),
    FactorDefinition(
        id="test_effort", number=7, label="Test Effort", group="assurance",
        description="Testing scope and rigour required.",
        low_anchor="Simple unit tests are sufficient.",
        high_anchor="End-to-end, contract, chaos, or a multi-device matrix.",
    ),
    FactorDefinition(
        id="regulatory_compliance", number=8, label="Regulatory Compliance", group="assurance",
        description="Legal and compliance requirements.",
        low_anchor="No compliance touchpoint.",
        high_anchor="GDPR, SOX, HIPAA, PCI-DSS, or a mandated audit trail.",
    ),
    FactorDefinition(
        id="security_review", number=9, label="Security Review", group="assurance",
        description="Security assessment requirements.",
        low_anchor="Standard CRUD over non-sensitive data.",
        high_anchor="Auth changes, PII, encryption, or threat modelling.",
    ),
    FactorDefinition(
        id="observability_operations", number=10, label="Observability & Operations",
        group="assurance", description="Monitoring, alerting, and operational readiness.",
        low_anchor="No new monitoring needed.",
        high_anchor="Custom dashboards, SLOs, runbooks, and on-call playbooks.",
    ),
    FactorDefinition(
        id="cross_team_dependency", number=11, label="Cross-Team Dependency", group="risk",
        description="Reliance on other teams.",
        low_anchor="Fully autonomous within this team.",
        high_anchor="External APIs, shared resources, or prioritised blockers.",
    ),
    FactorDefinition(
        id="reversibility", number=12, label="Reversibility", group="risk",
        description="Ability to undo or roll back the change.",
        low_anchor="Feature flag or instant rollback.",
        high_anchor="Irreversible migration or public API deprecation.",
    ),
    FactorDefinition(
        id="uncertainty", number=13, label="Uncertainty / Unknown Unknowns", group="risk",
        description="Degree of unknowns in the work.",
        low_anchor="A clear path from start to done.",
        high_anchor="Requires a spike, a proof of concept, or an unexplored domain.",
    ),
    FactorDefinition(
        id="performance_scalability", number=14, label="Performance / Scalability",
        group="delivery", description="Load and performance requirements.",
        low_anchor="Current load handles it with no change.",
        high_anchor="Load testing, caching strategy, or infrastructure scaling.",
    ),
    FactorDefinition(
        id="documentation_knowledge_transfer", number=15, label="Documentation & Knowledge Transfer",
        group="assurance", description="Documentation and onboarding effort.",
        low_anchor="Self-documenting code.",
        high_anchor="Public API docs, ADRs, and training material.",
    ),
    FactorDefinition(
        id="dod_overhead", number=16, label="Definition of Done Overhead", group="delivery",
        description="Non-coding completion activities.",
        low_anchor="Code and merge only.",
        high_anchor="Demo, release notes, marketing sync, multi-environment promotion.",
    ),
)

FACTOR_IDS: tuple[str, ...] = tuple(factor.id for factor in FACTORS)
FACTOR_BY_ID: dict[str, FactorDefinition] = {factor.id: factor for factor in FACTORS}


# --------------------------------------------------------------------------------------
# §4 — Stack-specific scoring guidance
# --------------------------------------------------------------------------------------

#: Per-stack calibration notes, keyed by factor id (§4.1-4.5). Only the factors the
#: framework calls out are present; the prompt injects just the rows for the declared
#: stack so the context stays small on a CPU-bound local model.
STACK_GUIDANCE: dict[str, dict[str, str]] = {
    "spring_boot": {
        "technical_complexity": "Bean lifecycle, AOP proxy behaviour, WebFlux vs MVC, Hibernate N+1, multi-module builds.",
        "integration_surface": "Spring Cloud contracts, Feign resilience, Kafka listeners, JPA lock-in, Security filter ordering.",
        "data_model_change": "Flyway/Liquibase scripting, JPA cascades, second-level cache invalidation, dialect differences.",
        "backend_effort": "Service + repository + DTO mapping, exception hierarchy, @Transactional boundaries, @Scheduled tasks.",
        "test_effort": "@SpringBootTest context load time, TestContainers, @MockBean vs @SpyBean, MockMvc vs WebTestClient.",
        "security_review": "Security configurer chain, JWT filters, method-level @PreAuthorize, OAuth2 resource server, SPA CSRF.",
        "observability_operations": "Micrometer + Prometheus, Micrometer Tracing, Actuator exposure, custom HealthIndicators.",
        "performance_scalability": "JVM heap tuning, HikariCP sizing, GC impact, reactive backpressure, Caffeine/Redis caching.",
    },
    "flask": {
        "technical_complexity": "Blueprint registration order, extension compatibility, WSGI server choice, thread-local g/request.",
        "integration_surface": "Flask-RESTful vs Smorest vs plain routes, Celery, SQLAlchemy sessions, Jinja2 inheritance.",
        "backend_effort": "Manual request validation, error handler registration, before/after_request chains, manual JWT.",
        "test_effort": "pytest-flask fixtures, test client context, mocking SQLAlchemy sessions, coverage wiring.",
        "security_review": "Manual CSRF (Flask-WTF), session strategy, raw-query SQL injection, Jinja2 autoescape, rate limiting.",
        "observability_operations": "Manual Prometheus client, structlog setup, no built-in health checks, manual APM.",
        "performance_scalability": "WSGI synchronous limits, Gunicorn worker config, connection pooling, Flask-Caching.",
    },
    "fastapi": {
        "technical_complexity": "Pydantic validation edges, Depends graph, async/await throughout, BackgroundTasks vs Celery, WebSockets.",
        "integration_surface": "ASGI server choice, middleware ordering, async SQLAlchemy (asyncpg/aiomysql), Tortoise vs SQLAlchemy.",
        "backend_effort": "Path operation design, response model serialisation, exception handler inheritance, upload handling.",
        "test_effort": "TestClient vs AsyncClient, mocking async dependencies, pytest-asyncio config, async DB rollback.",
        "security_review": "OAuth2 password flow, JWT handling, CORS middleware, HTTPS redirect, auth via dependency injection.",
        "observability_operations": "OpenTelemetry instrumentation, ASGI metrics middleware, contextual logging, async tracing.",
        "performance_scalability": "ASGI concurrency model, async driver tuning, Pydantic v2 gains, response caching, pool management.",
    },
    "react": {
        "technical_complexity": "Hook rules and closure staleness, custom hook abstraction, concurrent features, RSC, reconciliation.",
        "frontend_effort": "Component composition, prop drilling vs context vs store, CSS-in-JS vs modules, breakpoint strategy.",
        "test_effort": "RTL query strategy, MSW/fetch mocking, renderHook, visual regression, Playwright/Cypress E2E.",
        "security_review": "dangerouslySetInnerHTML XSS, CSP nonces, OAuth2 PKCE in SPA, token storage, npm audit.",
        "observability_operations": "DevTools profiling, Web Vitals instrumentation, error boundaries, Sentry, RUM.",
        "performance_scalability": "Bundle analysis, React.lazy/Suspense splitting, memoisation overuse, list virtualisation, images.",
    },
    "angular": {
        "technical_complexity": "RxJS operator chains and leaks, NgRx store, DI hierarchy, JIT vs AOT, standalone vs NgModules.",
        "frontend_effort": "@Input/@Output vs signals, template control flow, Material theming, i18n $localize.",
        "test_effort": "Jasmine/Karma vs Jest, CDK component harnesses, service mocking, RxJS marbles, Cypress/Playwright.",
        "security_review": "bypassSecurityTrust usage, template XSS, route guards, auth interceptors, CSP with Angular CLI.",
        "observability_operations": "Angular DevTools, Chrome profiling, ErrorHandler, logging services, Universal SSR monitoring.",
        "performance_scalability": "OnPush change detection, trackBy, lazy modules, CLI build budgets, service worker caching.",
    },
}

#: Named, non-scoring hazards surfaced as risk flags when the stack is declared (§4.x).
STACK_RISKS: dict[str, tuple[str, ...]] = {
    "spring_boot": (
        "Starter transitive dependency conflicts",
        "Auto-configuration and proxy behaviour surprises",
        "Context startup time affects local and CI velocity",
    ),
    "flask": (
        "Micro-framework tax: auth, validation, logging are all manual",
        "Community extension fragmentation and version lag",
        "Thread-local g/request misuse in async or test contexts",
    ),
    "fastapi": (
        "Async contagion across the call graph",
        "Async SQLAlchemy patterns differ sharply from sync",
        "Younger ecosystem: thin coverage for novel integrations",
    ),
    "react": (
        "State management sprawl across competing libraries",
        "Build toolchain fragility between Vite/Webpack/Parcel",
        "npm dependency volatility on major upgrades",
    ),
    "angular": (
        "RxJS cognitive load even for simple features",
        "Six-month release cadence forces constant upkeep",
        "NgRx and form boilerplate inflates frontend and backend effort",
    ),
}

STACK_LABELS: dict[str, str] = {
    "spring_boot": "Spring Boot", "flask": "Flask", "fastapi": "FastAPI",
    "react": "ReactJS", "angular": "Angular", "none": "None", "other": "Other",
}

#: §11.1 — stack-specific reference anchors the estimate is compared against.
STACK_ANCHORS: dict[str, tuple[dict[str, object], ...]] = {
    "spring_boot": (
        {"points": 3, "title": "CRUD endpoint over an existing entity"},
        {"points": 5, "title": "New entity + service + repository + DTO + tests"},
        {"points": 8, "title": "Multi-service integration with event publishing and transactions"},
    ),
    "flask": (
        {"points": 3, "title": "Simple route returning JSON"},
        {"points": 5, "title": "REST endpoint with SQLAlchemy model, validation, and tests"},
        {"points": 8, "title": "Celery background task with file upload and error handling"},
    ),
    "fastapi": (
        {"points": 3, "title": "Path operation with a Pydantic model"},
        {"points": 5, "title": "Async endpoint with DB integration and dependency injection"},
        {"points": 8, "title": "WebSocket endpoint with auth, background task, and full tests"},
    ),
    "react": (
        {"points": 3, "title": "Presentational component driven by props"},
        {"points": 5, "title": "Form with validation, API call, and error handling"},
        {"points": 8, "title": "Dashboard with filtering, sorting, and real-time updates"},
    ),
    "angular": (
        {"points": 3, "title": "Standalone component with input and output"},
        {"points": 5, "title": "Reactive form with async validation and service integration"},
        {"points": 8, "title": "Feature module with NgRx store, effects, and route guards"},
    ),
}

#: Fallback anchors when neither stack is declared, so a comparison always exists.
GENERIC_ANCHORS: tuple[dict[str, object], ...] = (
    {"points": 3, "title": "Bounded single-layer change with established patterns"},
    {"points": 5, "title": "Cross-layer change with validation, persistence, and tests"},
    {"points": 8, "title": "External integration with regulatory rules and failure handling"},
    {"points": 13, "title": "New multi-service journey that must be split before delivery"},
)

#: §5 — framework maturity taxonomy, and the point ceiling each level allows (§9).
MATURITY_TAXONOMY: dict[int, dict[str, object]] = {
    5: {"name": "Bleeding Edge", "definition": "Released under 6 months, no LTS promise.",
        "cap": 5, "action": "Mandatory spike before any integration."},
    4: {"name": "Emerging", "definition": "6-18 months old, some production usage.",
        "cap": 8, "action": "Reference story required before higher estimates."},
    3: {"name": "Established", "definition": "2-5 years old, stable major version.",
        "cap": 13, "action": "Standard decomposition rules apply."},
    2: {"name": "Mature", "definition": "5+ years, LTS releases, enterprise adoption.",
        "cap": 21, "action": "Standard decomposition rules apply."},
    1: {"name": "Legacy / End-of-Life", "definition": "Maintenance mode, talent scarcity.",
        "cap": 8, "action": "Migration spike recommended."},
}


class StackProfile(BaseModel):
    """§7 — the technology stack declaration that drives the calibration layer."""

    frontend: FrontendStack = "none"
    backend: BackendStack = "none"
    database: str = Field(default="", max_length=80)
    maturity_level: Level = 3
    team_experience: Level = 3
    scenario: Scenario = "standard"
    new_testing_layer: bool = False
    new_observability_signal: bool = False
    build_pattern_change: bool = False
    additional_stacks: int = Field(default=0, ge=0, le=6)

    @property
    def declared(self) -> tuple[str, ...]:
        """The named stacks in play, used for guidance and risk lookup."""
        return tuple(
            name for name in (self.frontend, self.backend) if name in STACK_GUIDANCE
        )

    def guidance(self) -> dict[str, list[str]]:
        """Factor-id → the stack notes that apply, merged across declared stacks."""
        merged: dict[str, list[str]] = {}
        for stack in self.declared:
            for factor_id, note in STACK_GUIDANCE[stack].items():
                merged.setdefault(factor_id, []).append(f"{STACK_LABELS[stack]}: {note}")
        return merged

    def anchors(self) -> list[dict[str, object]]:
        """Reference stories for the declared stacks, falling back to generic anchors."""
        collected: list[dict[str, object]] = []
        for stack in self.declared:
            for anchor in STACK_ANCHORS.get(stack, ()):
                collected.append({**anchor, "stack": STACK_LABELS[stack]})
        return collected or [{**anchor, "stack": "Generic"} for anchor in GENERIC_ANCHORS]

    def risks(self) -> list[str]:
        return [risk for stack in self.declared for risk in STACK_RISKS.get(stack, ())]


# --------------------------------------------------------------------------------------
# §7-§9 — the deterministic calculation
# --------------------------------------------------------------------------------------


class FactorScore(BaseModel):
    """A single scored factor plus the provenance of where the score came from."""

    factor: str
    number: int
    label: str
    group: Literal["scope", "delivery", "assurance", "risk"]
    score: Level
    reason: str = Field(max_length=300)
    #: ``model`` when the local model supplied a usable score, ``heuristic`` when the
    #: application had to derive one from story evidence. Surfaced in the UI so a reader
    #: can tell judgement apart from fallback.
    provenance: Literal["model", "heuristic"] = "model"
    stack_notes: list[str] = Field(default_factory=list)


class CalculationStep(BaseModel):
    """One line of the audit trail: what rule ran, whether it fired, and the effect."""

    rule: str
    reference: str
    label: str
    applied: bool
    delta: int = 0
    running_total: int


class PolicyCheck(BaseModel):
    rule: str
    reference: str
    label: str
    passed: bool
    detail: str


class Calculation(BaseModel):
    """The complete, replayable arithmetic behind a story point value."""

    base_sum: int
    base_adjustment_total: int
    stack_adjustment_total: int
    adjusted_score: int
    band: str
    mapped_points: int
    maturity_cap: int
    cap_exceeded: bool
    points: int
    steps: list[CalculationStep]


def _band(score: int) -> tuple[str, int]:
    """§9 — map an adjusted score onto the modified Fibonacci ladder."""
    if score <= 24:
        return "16-24", 3
    if score <= 34:
        return "25-34", 5
    if score <= 44:
        return "35-44", 8
    if score <= 54:
        return "45-54", 13
    if score <= 64:
        return "55-64", 21
    return "65+", 34


def calculate(scores: dict[str, int], stack: StackProfile) -> Calculation:
    """Run §8 adjustments and §9 mapping over a complete 1-5 scorecard.

    ``scores`` must contain every factor id; callers normalise before reaching here.
    Every rule is recorded whether or not it fired, so the UI can show the reader which
    penalties were *considered and not applied* — absence of a penalty is evidence too.
    """
    base_sum = sum(scores[factor_id] for factor_id in FACTOR_IDS)
    steps: list[CalculationStep] = [
        CalculationStep(
            rule="base_sum", reference="§7", label="Sum of the 16 factor scores",
            applied=True, delta=base_sum, running_total=base_sum,
        )
    ]
    total = base_sum

    def record(rule: str, reference: str, label: str, fired: bool, delta: int) -> int:
        nonlocal total
        if fired:
            total += delta
        steps.append(
            CalculationStep(
                rule=rule, reference=reference, label=label, applied=fired,
                delta=delta if fired else 0, running_total=total,
            )
        )
        return delta if fired else 0

    # §8.1 — base adjustments.
    base_before = total
    record(
        "uncertainty_ge_4", "§8.1", "Uncertainty ≥ 4: unknowns compound",
        scores["uncertainty"] >= 4, 3,
    )
    record(
        "cross_team_ge_4", "§8.1", "Cross-team dependency ≥ 4: coordination overhead",
        scores["cross_team_dependency"] >= 4, 2,
    )
    record(
        "reversibility_ge_4", "§8.1", "Reversibility ≥ 4: safety mechanisms add work",
        scores["reversibility"] >= 4, 2,
    )
    record(
        "full_stack_tax", "§8.1", "Frontend and backend both ≥ 3: context-switching tax",
        scores["frontend_effort"] >= 3 and scores["backend_effort"] >= 3, 1,
    )
    record(
        "review_cycle_tax", "§8.1", "Regulatory or security ≥ 4: review cycle gates",
        scores["regulatory_compliance"] >= 4 or scores["security_review"] >= 4, 2,
    )
    base_adjustment_total = total - base_before

    # §8.2 — stack-specific adjustments.
    stack_before = total
    maturity = int(stack.maturity_level)
    record(
        "maturity_bleeding_edge", "§8.2", "Framework maturity 5 (Bleeding Edge)",
        maturity == 5, 3,
    )
    record("maturity_emerging", "§8.2", "Framework maturity 4 (Emerging)", maturity == 4, 2)
    record("maturity_legacy", "§8.2", "Framework maturity 1 (Legacy / EOL)", maturity == 1, 2)
    record(
        "team_experience_low", "§8.2", "Team experience ≤ 2: learning curve",
        int(stack.team_experience) <= 2, 2,
    )
    record(
        "new_testing_layer", "§8.2", "New testing layer introduced for this stack",
        stack.new_testing_layer, 1,
    )
    record(
        "new_observability_signal", "§8.2", "New observability signal introduced",
        stack.new_observability_signal, 1,
    )
    record(
        "build_pattern_change", "§8.2", "Build or deployment pattern changes",
        stack.build_pattern_change, 1,
    )
    record(
        "polyglot_boundary", "§8.2",
        f"Polyglot boundary across {stack.additional_stacks} additional stack(s)",
        stack.additional_stacks > 0, stack.additional_stacks,
    )
    stack_adjustment_total = total - stack_before

    band, mapped_points = _band(total)
    cap = int(MATURITY_TAXONOMY[maturity]["cap"])
    cap_exceeded = mapped_points > cap
    steps.append(
        CalculationStep(
            rule="fibonacci_map", reference="§9",
            label=f"Adjusted score {total} falls in band {band} → {mapped_points} points",
            applied=True, delta=0, running_total=total,
        )
    )
    steps.append(
        CalculationStep(
            rule="maturity_cap", reference="§9",
            label=(
                f"Maturity {maturity} allows at most {cap} points"
                + (" — exceeded, escalation required" if cap_exceeded else " — within cap")
            ),
            applied=cap_exceeded, delta=0, running_total=total,
        )
    )
    return Calculation(
        base_sum=base_sum,
        base_adjustment_total=base_adjustment_total,
        stack_adjustment_total=stack_adjustment_total,
        adjusted_score=total,
        band=band,
        mapped_points=mapped_points,
        maturity_cap=cap,
        cap_exceeded=cap_exceeded,
        # The mapped value is reported as-is. A cap breach escalates the *recommendation*
        # rather than silently shrinking the number, because quietly reporting fewer
        # points than the evidence supports would defeat the purpose of the framework.
        points=mapped_points,
        steps=steps,
    )


# --------------------------------------------------------------------------------------
# §10, §13.2, §13.4 — gates, confidence, and the decision
# --------------------------------------------------------------------------------------


def policy_checks(
    scores: dict[str, int], stack: StackProfile, calculation: Calculation
) -> list[PolicyCheck]:
    """Evaluate every spike/decomposition gate. ``passed`` means "no escalation needed"."""
    extremes = [FACTOR_BY_ID[key].label for key, value in scores.items() if value == 5]
    return [
        PolicyCheck(
            rule="uncertainty_max", reference="§10",
            label="Uncertainty below 5", passed=scores["uncertainty"] < 5,
            detail=(
                "Uncertainty is 5 — the framework forbids estimating; buy the knowledge first."
                if scores["uncertainty"] == 5
                else f"Uncertainty scored {scores['uncertainty']}; estimation is permitted."
            ),
        ),
        PolicyCheck(
            rule="maturity_max", reference="§10",
            label="Framework maturity below Bleeding Edge",
            passed=int(stack.maturity_level) < 5,
            detail=(
                "Maturity 5 requires a framework evaluation spike before estimation."
                if int(stack.maturity_level) == 5
                else f"Maturity {stack.maturity_level} "
                f"({MATURITY_TAXONOMY[int(stack.maturity_level)]['name']}) permits estimation."
            ),
        ),
        PolicyCheck(
            rule="knowledge_gap", reference="§10",
            label="Team experience matches technical complexity",
            passed=not (
                int(stack.team_experience) <= 2 and scores["technical_complexity"] >= 4
            ),
            detail=(
                "Team experience ≤ 2 with technical complexity ≥ 4 — spike or pair first."
                if int(stack.team_experience) <= 2 and scores["technical_complexity"] >= 4
                else "The knowledge gap is not large enough to force a spike."
            ),
        ),
        PolicyCheck(
            rule="multiple_extremes", reference="§10",
            label="Fewer than two factors at 5", passed=len(extremes) < 2,
            detail=(
                f"{len(extremes)} factors scored 5 ({', '.join(extremes)}) — decompose or spike."
                if len(extremes) >= 2
                else f"{len(extremes)} factor(s) scored 5; no decomposition is forced by this rule."
            ),
        ),
        PolicyCheck(
            rule="size_ceiling", reference="§13.4",
            label="Adjusted score within the 13-point ceiling",
            passed=calculation.adjusted_score <= 54,
            detail=(
                f"Adjusted score {calculation.adjusted_score} exceeds 54 — decompose before committing."
                if calculation.adjusted_score > 54
                else f"Adjusted score {calculation.adjusted_score} is inside the committable range."
            ),
        ),
        PolicyCheck(
            rule="maturity_cap", reference="§9",
            label="Points within the maturity cap", passed=not calculation.cap_exceeded,
            detail=(
                f"{calculation.mapped_points} points exceeds the {calculation.maturity_cap}-point cap "
                f"for maturity {stack.maturity_level}."
                if calculation.cap_exceeded
                else f"{calculation.mapped_points} points is within the "
                f"{calculation.maturity_cap}-point cap."
            ),
        ),
        PolicyCheck(
            rule="not_a_migration", reference="§4.6",
            label="Not a framework migration",
            passed=stack.scenario != "framework_migration",
            detail=(
                "Framework migrations are epics — run time-boxed discovery instead of estimating."
                if stack.scenario == "framework_migration"
                else "The declared scenario can be estimated as a story."
            ),
        ),
    ]


def decide(
    checks: list[PolicyCheck],
    stack: StackProfile,
    calculation: Calculation,
    scores: dict[str, int],
) -> tuple[Recommendation, str]:
    """§13.4 — walk the decision flowchart in order and return the first verdict."""
    failed = {check.rule for check in checks if not check.passed}
    if "not_a_migration" in failed:
        return (
            "epic_discovery",
            "A framework migration is an epic. Run time-boxed discovery sprints and estimate "
            "the resulting stories individually.",
        )
    if "maturity_max" in failed:
        return (
            "upgrade_framework_first",
            "The framework is Bleeding Edge (maturity 5). Evaluate it with a spike, or move to a "
            "more established version, before committing to a point value.",
        )
    if "uncertainty_max" in failed:
        return (
            "spike_first",
            "Uncertainty scored 5. A point value would be fiction — buy the knowledge with a "
            "time-boxed spike, then re-estimate the implementation.",
        )
    if "knowledge_gap" in failed:
        return (
            "spike_first",
            "The team's experience with this stack is too low for the technical complexity "
            "involved. Spike or pair before committing.",
        )
    if "size_ceiling" in failed or "multiple_extremes" in failed:
        return (
            "decompose",
            f"An adjusted score of {calculation.adjusted_score} is beyond what a single story "
            "should carry. Split it and estimate the parts.",
        )
    if "maturity_cap" in failed:
        # §13.4 offers "DECOMPOSE or SPIKE" here without choosing. The deciding evidence is
        # uncertainty: unknowns are bought with a spike, while sheer size is split. This is
        # what makes §12.3 (FastAPI, maturity 4, uncertainty 4) resolve to a spike.
        breach = (
            f"{calculation.mapped_points} points exceeds the {calculation.maturity_cap}-point cap "
            f"for a {MATURITY_TAXONOMY[int(stack.maturity_level)]['name']} framework. "
            f"{MATURITY_TAXONOMY[int(stack.maturity_level)]['action']}"
        )
        if scores["uncertainty"] >= 4:
            return (
                "spike_first",
                f"{breach} Uncertainty is {scores['uncertainty']}, so resolve the unknowns with a "
                "time-boxed spike before splitting or committing.",
            )
        return "decompose", breach
    return (
        "proceed",
        f"All gates pass. {calculation.points} points is a defensible commitment for this story.",
    )


def confidence(scores: dict[str, int], stack: StackProfile, calculation: Calculation) -> tuple[
    Confidence, str
]:
    """§13.2 — confidence follows the factor spread and the stack penalties."""
    elevated = [key for key, value in scores.items() if value >= 4]
    extremes = [key for key, value in scores.items() if value == 5]
    stack_penalty = calculation.stack_adjustment_total > 0
    if extremes or len(elevated) >= 3 or int(stack.maturity_level) >= 4:
        reasons = []
        if extremes:
            reasons.append(f"{len(extremes)} factor(s) at the maximum of 5")
        if len(elevated) >= 3:
            reasons.append(f"{len(elevated)} factors at 4 or above")
        if int(stack.maturity_level) >= 4:
            reasons.append(f"framework maturity {stack.maturity_level}")
        return "Low", "Low confidence: " + "; ".join(reasons) + "."
    if elevated or stack_penalty:
        detail = (
            f"{len(elevated)} factor(s) at 4" if elevated else "stack penalties apply"
        )
        return "Medium", f"Medium confidence: {detail}, but no factor reaches 5."
    return (
        "High",
        "High confidence: every factor scored 3 or below and no stack penalty applied.",
    )


def risk_flags(scores: dict[str, int], stack: StackProfile) -> list[dict[str, object]]:
    """§7 output item 7 — every factor at 4 or above, plus declared stack hazards."""
    flags: list[dict[str, object]] = [
        {
            "source": "factor",
            "label": FACTOR_BY_ID[key].label,
            "score": value,
            "detail": FACTOR_BY_ID[key].high_anchor,
        }
        for key, value in sorted(scores.items(), key=lambda item: -item[1])
        if value >= 4
    ]
    flags.extend(
        {"source": "stack", "label": risk, "score": None, "detail": "Known stack hazard."}
        for risk in stack.risks()
    )
    return flags


#: §12 walkthroughs use these person-day envelopes; they scale with the Fibonacci ladder.
EFFORT_DEFAULTS: dict[int, tuple[float, float, float]] = {
    3: (2, 3, 5), 5: (3, 5, 8), 8: (5, 8, 13),
    13: (8, 13, 21), 21: (13, 21, 34), 34: (21, 34, 55),
}


def spike_template(title: str, unknowns: list[str]) -> dict[str, object]:
    """§10 — a filled-in spike definition so escalation comes with a next action."""
    return {
        "title": f"SPIKE: {title} feasibility",
        "objective": (
            "Determine " + "; ".join(unknowns[:3])
            if unknowns
            else "Determine the unknowns blocking a defensible estimate"
        ),
        "timebox": "1 day",
        "success_criteria": [
            "Proof of concept compiles and runs",
            "Basic integration test passes",
            "Performance baseline established, if applicable",
            "Security review checklist completed",
            "Deployment to a target environment verified",
        ],
        "deliverable": "Decision record plus a re-estimated implementation story",
    }
