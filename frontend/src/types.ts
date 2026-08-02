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
  edits: { action: 'create' | 'replace'; path: string; content: string; reason: string }[]
  findings: { severity: string; message: string; path?: string; suggestion?: string }[]
  diffs: Record<string, string>
  verification: { path: string; passed: boolean; detail: string }[]
  can_apply: boolean
  evidence?: {
    workspace: string
    files_considered: string[]
    context_characters: number
    context_budget: number
    selection: string
    write_policy: string
  }
}

export interface AgentEvent {
  run_id: string
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

export interface Story {
  title: string
  user_story: string
  acceptance_criteria: string[]
  technical_breakdown?: string
  existing_points?: number
  key?: string
  source: 'manual' | 'jira' | 'upload'
}
export interface EstimateResult extends Record<string, unknown> {
  story: Story
  points: 1 | 2 | 3 | 5 | 8 | 13
  tldr: string
  plain_language_why: string
  drivers: string[]
  drivers_explanation: string
  anchor_comparison: string
  points_derivation: string
  scorecard: { parameter: string; score: 'Low' | 'Medium' | 'High'; reason: string }[]
  hidden_tasks: { task: string; weight: string }[]
  risks: { risk: string; mitigation_or_assumption: string }[]
  assumptions: string[]
  spike_recommended: boolean
  spike_reason?: string
  split_recommendation: { split_recommended: boolean; rationale: string; proposed_stories: string[] }
  effort: { react: string; spring: string; existing_code: string; person_days: { optimistic: number; likely: number; pessimistic: number } }
  evidence?: { source: string; score_factors: string[]; anchors_considered: string[]; policy_checks: Record<string, boolean> }
}
