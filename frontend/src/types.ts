export type Role = 'user' | 'assistant' | 'system' | 'tool'
export type Mode = 'auto' | 'chat' | 'code' | 'research' | 'image' | 'document'
export interface Attachment { id: string; name: string; content_type: string; size: number }
export interface Message { id: string; role: Role; content: string; created_at: string; attachments?: Attachment[]; metadata?: Record<string, unknown> }
export interface Conversation { id: string; title: string; created_at: string; updated_at: string }

export interface SmartCodeRequest {
  objective: string
  workspace_root: string
  mode: 'generate' | 'modify' | 'review'
  target_paths: string[]
  acceptance_criteria: string[]
  language?: string
  framework?: string
  risk: 'low' | 'medium' | 'high'
}
export interface SmartCodePreview {
  preview_token: string
  summary: string
  plan: string[]
  /** False when `plan`/`summary` are Devvy's stand-ins, not the model's own words. */
  plan_supplied?: boolean
  summary_supplied?: boolean
  /** How to deploy what this change produces. */
  deploy_steps?: string[]
  edits: { action: 'create' | 'replace'; path: string; content: string; reason: string }[]
  findings: { severity: string; message: string; path?: string; suggestion?: string }[]
  diffs: Record<string, string>
  verification: { path: string; passed: boolean; detail: string }[]
  can_apply: boolean
  evidence?: {
    workspace: string
    files_considered: string[]
    /** Per-file provenance: what was actually sent to the model, and what the budget cut. */
    context_manifest: { id: string; label: string; characters: number; truncated: boolean; trusted: boolean }[]
    context_characters: number
    context_budget: number
    truncated_files: string[]
    selection: string
    trust_policy: string
    write_policy: string
  }
}

/* Background jobs. Every model-backed request is a job that outlives the tab that made it. */

export type JobKind = 'chat' | 'estimate' | 'smart-code' | 'talk'

/** Which workspace a request belongs to.
 *
 *  Named rather than inlined at the call site because the two vocabularies do not match:
 *  the job kind is `estimate` and the page is `estimate-code`. TypeScript proves the page
 *  name is valid; only a mapping written down in one place makes it checkable that it is
 *  also the *right* one. */
export function workspacePageFor(kind: JobKind): 'chat' | 'talk' | 'smart-code' | 'estimate-code' {
  if (kind === 'smart-code') return 'smart-code'
  if (kind === 'estimate') return 'estimate-code'
  if (kind === 'talk') return 'talk'
  return 'chat'
}
export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'interrupted'

export const JOB_FINISHED: JobStatus[] = ['succeeded', 'failed', 'cancelled', 'interrupted']
export const isJobActive = (status: JobStatus) => status === 'queued' || status === 'running'

export interface JobSummary {
  id: string
  kind: JobKind
  status: JobStatus
  title: string
  progress: string
  created_at: string
  started_at: string | null
  completed_at: string | null
  conversation_id: string | null
  error: string | null
  has_result: boolean
  output_preview: string
}

export interface JobDetail extends JobSummary {
  request: Record<string, unknown>
  result: Record<string, any> | null
  output_text: string
  events: AgentEvent[]
  access?: { owner: boolean; permission: 'viewer' | 'editor' | null }
}

/** Messages delivered by `GET /api/jobs/{id}/stream`. */
export type JobStreamEvent =
  | { event: 'snapshot'; data: JobDetail }
  | { event: 'token'; data: { content: string; offset: number } }
  | { event: 'agent_event'; data: AgentEvent }
  | { event: 'status'; data: { message: string } }
  | { event: 'heartbeat'; data: unknown }
  | { event: 'done'; data: { status: JobStatus; result?: Record<string, any>; error?: string } }

export interface AgentEvent {
  run_id: string
  /** Monotonic per job; used to drop events present in both a snapshot and the live feed. */
  seq?: number
  stage: string
  status: 'running' | 'completed' | 'validated' | 'retrying' | 'waiting' | 'failed' | string
  label: string
  detail?: string
  elapsed_ms: number
  evidence?: Record<string, unknown>
}

export interface SystemStatus {
  app: { name: string; version: string; deployment: string }
  model: { id: string; loaded: boolean; error?: string; device: string; dtype: string; generation: string }
  capabilities: Record<string, boolean>
  trust: { privacy: string; data_dir: string; network: string[]; run_ledger: string }
  limits: Record<string, number>
}

export type UserRole = 'owner' | 'admin' | 'member'
export interface UserPreferences {
  default_workspace: string
  density: 'comfortable' | 'compact'
  evidence_expanded: boolean
  confirm_external_research: boolean
}
export interface WorkspaceUser {
  id: string
  email: string
  display_name: string
  role: UserRole
  active: boolean
  preferences: UserPreferences
  created_at: string
  last_login_at: string | null
}
export interface AuthState {
  authenticated: boolean
  needs_setup: boolean
  auth_enabled: boolean
  user: WorkspaceUser | null
  security: { session_cookie: string; csrf: string; secure_cookie: boolean }
}
export type ShareResource = 'conversation' | 'job' | 'estimate'
export interface ResourceShare {
  id: string
  resource_type: ShareResource
  resource_id: string
  permission: 'viewer' | 'editor'
  owner: WorkspaceUser | null
  recipient: WorkspaceUser | null
  created_at: string
}

/* Estimate Code — Agile Story Point Estimation Framework v2.0 (16 factors + stack layer).
   Shapes mirror backend/estimation_framework.py; the config endpoint serves the rubric. */

export type Level = 1 | 2 | 3 | 4 | 5
export type FrontendStack = 'react' | 'angular' | 'none' | 'other'
export type BackendStack = 'spring_boot' | 'flask' | 'fastapi' | 'none' | 'other'
export type Scenario = 'standard' | 'new_framework' | 'framework_upgrade' | 'framework_migration'
export type Recommendation = 'proceed' | 'decompose' | 'spike_first' | 'upgrade_framework_first' | 'epic_discovery'
export type Points = 3 | 5 | 8 | 13 | 21 | 34

export interface StackProfile {
  frontend: FrontendStack
  backend: BackendStack
  database: string
  maturity_level: Level
  team_experience: Level
  scenario: Scenario
  new_testing_layer: boolean
  new_observability_signal: boolean
  build_pattern_change: boolean
  additional_stacks: number
}

export interface FactorDefinition {
  id: string
  number: number
  label: string
  description: string
  low_anchor: string
  high_anchor: string
  group: 'scope' | 'delivery' | 'assurance' | 'risk'
}

export interface FactorScore {
  factor: string
  number: number
  label: string
  group: 'scope' | 'delivery' | 'assurance' | 'risk'
  score: Level
  reason: string
  /** `heuristic` means the model skipped this factor and the app derived it from story text. */
  provenance: 'model' | 'heuristic'
  stack_notes: string[]
}

/** One line of the replayable audit trail. `applied: false` rules are shown too — a
    penalty that was considered and did not fire is evidence in its own right. */
export interface CalculationStep {
  rule: string
  reference: string
  label: string
  applied: boolean
  delta: number
  running_total: number
}

export interface Calculation {
  base_sum: number
  base_adjustment_total: number
  stack_adjustment_total: number
  adjusted_score: number
  band: string
  mapped_points: Points
  maturity_cap: number
  cap_exceeded: boolean
  points: Points
  steps: CalculationStep[]
}

export interface PolicyCheck {
  rule: string
  reference: string
  label: string
  passed: boolean
  detail: string
}

export interface RiskFlag {
  source: 'factor' | 'stack'
  label: string
  score: number | null
  detail: string
}

export interface DetailedReasoning {
  conclusion: string
  formula: string
  group_contributions: {
    group: 'scope' | 'delivery' | 'assurance' | 'risk'
    label: string
    subtotal: number
    factor_count: number
    maximum: number
  }[]
  top_contributors: {
    factor: string
    label: string
    group: 'scope' | 'delivery' | 'assurance' | 'risk'
    score: Level
    reason: string
    provenance: 'model' | 'heuristic'
  }[]
  applied_adjustments: CalculationStep[]
  gate_path: PolicyCheck[]
  confidence_basis: string
  band_sensitivity: {
    current_points: number
    target_points: number | null
    target_adjusted_score: number | null
    reduction_required: number
    explanation: string
  }
  factor_sensitivity: {
    factor: string
    label: string
    current_score: Level
    trial_score: Level
    adjusted_score: number
    points: number
    recommendation: Recommendation
    changes_outcome: boolean
  }[]
}

export interface EstimationSuggestion {
  id: string
  priority: 'critical' | 'high' | 'medium'
  category: 'decision' | 'clarity' | 'scope' | 'risk' | 'assurance' | 'delivery' | 'stack'
  title: string
  action: string
  why: string
  evidence: string[]
  expected_outcome: string
  related_factors: string[]
}

export interface AgenticDimensionAssessment {
  factor: string
  label: string
  score_min: Level
  score_most_likely: Level
  score_max: Level
  rationale: string
  evidence_ids: string[]
  assumptions: string[]
  included_lifecycle_work: string[]
  why_not_lower: string
  why_not_higher: string
  confidence: 'High' | 'Medium' | 'Low'
  provenance: 'model' | 'heuristic'
}

export interface AgenticAssessment {
  role: 'PRIMARY_ESTIMATOR' | 'BLIND_REVIEWER'
  blind: boolean
  dimensions: AgenticDimensionAssessment[]
  point_cross_check: Points
  recommendation_cross_check: Recommendation
  model_scored: number
  heuristic_filled: number
}

export interface AgenticPipeline {
  version: string
  mode: 'COMPACT' | 'STANDARD' | 'HIGH_RISK'
  status: 'HUMAN_REVIEW'
  canonical_story: {
    title: string
    description: string
    acceptance_criteria: string[]
    technical_breakdown: string
    labels: string[]
    components: string[]
    source: Story['source']
    stack: StackProfile
    evidence: Record<string, string>
    input_hash: string
    missing_fields: string[]
    untrusted_instructions_detected: boolean
  }
  readiness: {
    decision: 'ESTIMATE' | 'ESTIMATE_WITH_ASSUMPTIONS' | 'SPIKE_REQUIRED' | 'DECOMPOSE_REQUIRED'
    checks: {
      area: string
      status: 'ready' | 'assumption' | 'blocked'
      detail: string
      evidence_ids: string[]
      question: string | null
    }[]
    assumptions: string[]
    targeted_questions: string[]
  }
  specialist_routes: {
    role: string
    label: string
    reason: string
    dimensions: string[]
    mandatory: boolean
  }[]
  specialist_findings: {
    role: string
    label: string
    summary: string
    assessed_dimensions: string[]
    evidence_ids: string[]
    material_risks: string[]
    open_questions: string[]
  }[]
  primary: AgenticAssessment
  reviewer: AgenticAssessment
  disagreements: {
    factor: string
    label: string
    primary_score: Level
    reviewer_score: Level
    delta: number
    material: boolean
    protected: boolean
    point_boundary_impact: boolean
    reasons: string[]
  }[]
  critic_challenges: {
    factor: string
    severity: 'material' | 'advisory'
    challenge: string
    evidence_needed: string
    possible_impact: string
  }[]
  arbitration: {
    factor: string
    primary_score: Level
    reviewer_score: Level
    selected_score: Level
    policy: string
    rationale: string
    human_approval_required: boolean
  }[]
  consistency_audit: {
    status: 'PASS' | 'PASS_WITH_WARNINGS' | 'HUMAN_REVIEW_REQUIRED'
    dimension_stability_index: number
    point_stability: { primary: Points; reviewer: Points; final: Points; same_boundary: boolean }
    risk_floor_stable: boolean
    material_disagreements: number
    protected_disagreements: number
    calculation_replay_passed: boolean
    warnings: string[]
  }
  final_report: {
    recommended_points: Points
    recommendation: Recommendation
    confidence: 'High' | 'Medium' | 'Low'
    why_selected: string
    why_not_lower: string
    why_not_higher: string
    assumptions: string[]
    human_authority: string
  }
  human_review: {
    required: boolean
    status: 'pending'
    options: ('accept' | 'override' | 'spike' | 'decompose')[]
    reason: string
  }
  prompt_versions: { primary: string; reviewer: string }
  model_policy: {
    model: string
    serialized: boolean
    independent_model_passes: number
    hidden_chain_of_thought_stored: boolean
  }
}

export interface EstimateConfig {
  model: string
  jira_configured: boolean
  jira_write_enabled: boolean
  framework: { name: string; version: string; document: string; fibonacci: Points[] }
  factors: FactorDefinition[]
  maturity_levels: { level: Level; name: string; definition: string; cap: number; action: string }[]
  stacks: {
    frontend: { id: FrontendStack; label: string }[]
    backend: { id: BackendStack; label: string }[]
    scenarios: { id: Scenario; label: string }[]
  }
}

/** A previously completed estimate. Durable, searchable, and independent of job retention. */
export interface EstimateHistoryEntry {
  id: string
  owner_id?: string | null
  access?: { owner: boolean; permission: 'viewer' | 'editor' | null }
  created_at: string
  job_id: string | null
  title: string
  issue_key: string | null
  source: 'manual' | 'jira' | 'upload'
  points: Points
  confidence: 'High' | 'Medium' | 'Low' | ''
  recommendation: Recommendation | ''
  base_sum: number
  adjusted_score: number
  band: string
  frontend: FrontendStack
  backend: BackendStack
  maturity_level: Level
  team_experience: Level
  model_scored: number
  heuristic_filled: number
  tldr: string
  /** What the team decided. Null until somebody actually answers. */
  decision: EstimateDecision | null
  decided_points: number | null
  decision_note: string | null
  decided_at: string | null
  actual_points: number | null
}

export type EstimateDecision = 'accept' | 'override' | 'spike' | 'decompose'

export interface EstimateHistoryPage {
  total: number
  limit: number
  offset: number
  items: EstimateHistoryEntry[]
}

export interface EstimateHistoryStats {
  total: number
  points: Record<string, number>
  recommendations: Record<string, number>
  confidence: Record<string, number>
  decisions: Record<string, number>
  median_points: number | null
  model_scored_share: number | null
  /** Calibration: what teams did with the recommendation, and how it held up. */
  decided: number
  accepted_as_recommended: number
  overridden: number
  /** Mean signed gap between the overridden number and the recommended one. */
  override_bias: number | null
  with_actuals: number
  /** Mean absolute gap between recommended points and what it actually took. */
  actual_accuracy: number | null
}

export interface Story {
  title: string
  user_story: string
  acceptance_criteria: string[]
  technical_breakdown?: string
  existing_points?: number
  key?: string
  source: 'manual' | 'jira' | 'upload'
  stack?: StackProfile
}

export interface EstimateResult extends Record<string, unknown> {
  framework: { name: string; version: string; document: string; factor_count: number }
  story: Story
  stack: StackProfile & {
    frontend_label: string
    backend_label: string
    maturity_name: string
    maturity_definition: string
    maturity_action: string
  }
  scorecard: FactorScore[]
  calculation: Calculation
  points: Points
  drivers: string[]
  drivers_explanation: string
  tldr: string
  plain_language_why: string
  confidence: 'High' | 'Medium' | 'Low'
  confidence_detail: string
  recommendation: Recommendation
  recommendation_detail: string
  detailed_reasoning: DetailedReasoning
  suggestions: EstimationSuggestion[]
  agentic_pipeline: AgenticPipeline
  risk_flags: RiskFlag[]
  anchor_comparison: string
  anchors_considered: { points: number; title: string; stack: string }[]
  effort: {
    frontend: string
    backend: string
    data: string
    assurance: string
    person_days: { optimistic: number; likely: number; pessimistic: number }
  }
  hidden_tasks: { task: string; weight: string }[]
  risks: { risk: string; mitigation_or_assumption: string }[]
  assumptions: string[]
  spike_recommended: boolean
  spike_reason?: string
  spike_definition?: {
    title: string
    objective: string
    timebox: string
    success_criteria: string[]
    deliverable: string
  }
  split_recommendation: { split_recommended: boolean; rationale: string; proposed_stories: string[] }
  evidence: {
    source: string
    context_manifest: { id: string; label: string; characters: number; truncated: boolean; trusted: boolean }[]
    policy_checks: PolicyCheck[]
    scoring_provenance: { model_scored: number; heuristic_filled: number; minimum_required: number }
    model_cross_check: {
      model_points: number | null
      calculated_points: Points
      agreement: 'agrees' | 'diverges' | 'not_offered'
      note: string
    }
    determinism: string
  }
}

/** What Smart Code found in a chosen folder. Counts and languages only — never file names. */
export interface WorkspaceInfo {
  exists: boolean
  reason?: string
  path?: string
  name?: string
  source_files?: number
  languages?: string[]
  suggested_mode?: 'generate' | 'modify'
  empty?: boolean
}
