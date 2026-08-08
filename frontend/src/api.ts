import type {
  AgentEvent, Attachment, Conversation, EstimateDecision, EstimateHistoryEntry, EstimateHistoryPage,
  EstimateHistoryStats, JobDetail, JobStatus, JobSummary, Message, Mode,
  SmartCodeRequest, SystemStatus, WorkspaceInfo,
  AuthState, ResourceShare, ShareResource, WorkspaceUser,
} from './types'

// Match the page hostname so session cookies remain first-party in development. Using
// localhost for the UI and 127.0.0.1 for the API creates a cross-site cookie boundary in
// modern browsers even though both addresses point to this machine.
export const API = import.meta.env.VITE_API_URL || `${window.location.protocol}//${window.location.hostname}:8765`

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  const method = (init?.method || 'GET').toUpperCase()
  if (['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) {
    const csrf = document.cookie.split('; ').find(item => item.startsWith('devvy_csrf='))
      ?.split('=').slice(1).join('=')
    if (csrf) headers.set('X-CSRF-Token', decodeURIComponent(csrf))
  }
  const response = await fetch(`${API}${path}`, { ...init, headers, credentials: 'include' })
  if (!response.ok) {
    const text = await response.text()
    if (response.status === 401 && !path.startsWith('/api/auth/')) {
      window.dispatchEvent(new CustomEvent('devvy:authentication-required'))
    }
    try { throw new Error(JSON.parse(text).detail || `Request failed (${response.status})`) }
    catch (error) { if (error instanceof SyntaxError) throw new Error(text || `Request failed (${response.status})`); throw error }
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

export async function consumeSSE(
  path: string,
  payload: unknown,
  onEvent: (event: string, data: any) => void,
  signal?: AbortSignal,
  method: 'GET' | 'POST' = 'POST',
) {
  const response = await fetch(`${API}${path}`, {
    method,
    headers: method === 'POST'
      ? { 'Content-Type': 'application/json', Accept: 'text/event-stream' }
      : { Accept: 'text/event-stream' },
    body: method === 'POST' ? JSON.stringify(payload) : undefined,
    signal,
    credentials: 'include',
  })
  if (!response.ok || !response.body) throw new Error(await response.text())
  const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const blocks = buffer.split(/\r?\n\r?\n/); buffer = blocks.pop() || ''
    for (const block of blocks) {
      let event = 'message'; const lines: string[] = []
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim()
        if (line.startsWith('data:')) lines.push(line.slice(5).trim())
      }
      if (lines.length) onEvent(event, JSON.parse(lines.join('\n')))
    }
    if (done) break
  }
}

/** Attach to a job and rebuild its state from a snapshot plus live deltas.
 *
 *  Safe to call at any point in a job's life, including after it finished — the snapshot
 *  carries everything already produced, and `offset` lets us drop deltas the snapshot
 *  already contains, so reconnecting never duplicates text. */
export async function attachToJob(
  jobId: string,
  handlers: {
    onSnapshot?: (job: JobDetail) => void
    onText?: (text: string) => void
    onEvent?: (event: AgentEvent) => void
    onStatus?: (message: string) => void
    /** A result the job has produced but not finished with — a batch emits one per story. */
    onPartial?: (result: Record<string, any>) => void
    onDone?: (status: JobStatus, result?: Record<string, any>, error?: string) => void
  },
  signal?: AbortSignal,
) {
  let text = ''
  let lastSeq = 0
  // Tokens arrive faster than a screen can usefully change. Calling onText for each one
  // makes React re-render the whole transcript per token — roughly a thousand renders for
  // one answer, all but sixty of which are discarded by the compositor anyway. Coalescing
  // to one update per animation frame keeps the text exactly as live to the eye while the
  // main thread stays free to scroll and paint.
  let frame = 0
  let pendingText = false
  const flushText = () => {
    frame = 0
    if (!pendingText) return
    pendingText = false
    handlers.onText?.(text)
  }
  const scheduleText = () => {
    pendingText = true
    if (!frame) frame = requestAnimationFrame(flushText)
  }
  await consumeSSE(`/api/jobs/${jobId}/stream`, undefined, (event, data) => {
    if (event === 'snapshot') {
      text = data.output_text || ''
      handlers.onSnapshot?.(data as JobDetail)
      handlers.onText?.(text)   // immediate: this is the initial paint, not a delta
      if (data.result) handlers.onPartial?.(data.result)
      for (const item of (data.events ?? []) as AgentEvent[]) {
        lastSeq = Math.max(lastSeq, item.seq ?? 0)
      }
    } else if (event === 'token') {
      // Deltas already inside the snapshot start before its length; skip those.
      if (data.offset >= text.length) {
        text += data.content
        scheduleText()
      }
    } else if (event === 'agent_event') {
      // Same overlap as tokens: the snapshot already carried everything up to lastSeq.
      const seq = (data as AgentEvent).seq ?? 0
      if (seq === 0 || seq > lastSeq) {
        lastSeq = Math.max(lastSeq, seq)
        handlers.onEvent?.(data as AgentEvent)
      }
    } else if (event === 'partial') {
      handlers.onPartial?.(data.result)
    } else if (event === 'status') {
      handlers.onStatus?.(data.message)
    } else if (event === 'done') {
      // Never let the final characters wait on a frame that may not come — a backgrounded
      // tab stops firing them, and the last words of an answer would be missing.
      if (frame) cancelAnimationFrame(frame)
      flushText()
      handlers.onDone?.(data.status, data.result, data.error)
    }
  }, signal, 'GET')
  if (frame) cancelAnimationFrame(frame)
  flushText()
}

export const api = {
  authSession: () => json<AuthState>('/api/auth/session'),
  login: (email: string, password: string, remember = true) => json<AuthState>('/api/auth/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password, remember }),
  }),
  register: (payload: {
    display_name: string; email: string; password: string; invite_token?: string; remember?: boolean
  }) => json<AuthState>('/api/auth/register', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
  }),
  logout: (activeJobAction: 'keep' | 'cancel') => json<{
    signed_out: boolean; active_job_action: string; cancelled_jobs: number
  }>('/api/auth/logout', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ active_job_action: activeJobAction }),
  }),
  updateMe: (payload: { display_name?: string; preferences?: Partial<WorkspaceUser['preferences']> }) =>
    json<WorkspaceUser>('/api/auth/me', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }),
  changePassword: (currentPassword: string, newPassword: string) => json<{
    changed: boolean; other_sessions_revoked: number
  }>('/api/auth/me/password', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  }),
  users: () => json<WorkspaceUser[]>('/api/auth/users'),
  updateUser: (id: string, payload: { role?: 'admin' | 'member'; active?: boolean }) =>
    json<WorkspaceUser>(`/api/auth/users/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }),
  invite: (email: string, role: 'admin' | 'member') => json<{
    id: string; email: string; role: string; expires_at: string; invite_token: string; invite_route: string
  }>('/api/auth/invitations', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, role }),
  }),
  shares: (incoming = false) => json<ResourceShare[]>(`/api/access/shares?incoming=${incoming}`),
  share: (resourceType: ShareResource, resourceId: string, recipientEmail: string,
    permission: 'viewer' | 'editor') => json<ResourceShare>('/api/access/shares', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ resource_type: resourceType, resource_id: resourceId,
        recipient_email: recipientEmail, permission }),
    }),
  revokeShare: (id: string) => json<void>(`/api/access/shares/${id}`, { method: 'DELETE' }),
  systemStatus: () => json<SystemStatus>('/api/system/status'),
  jobs: (limit = 50) => json<{ jobs: JobSummary[]; active: number }>(`/api/jobs?limit=${limit}`),
  job: (id: string) => json<JobDetail>(`/api/jobs/${id}`),
  cancelJob: (id: string) => json<{ status: string }>(`/api/jobs/${id}/cancel`, { method: 'POST' }),
  conversations: () => json<Conversation[]>('/api/conversations'),
  create: () => json<Conversation>('/api/conversations', { method: 'POST' }),
  messages: (id: string) => json<Message[]>(`/api/conversations/${id}/messages`),
  remove: (id: string) => json<void>(`/api/conversations/${id}`, { method: 'DELETE' }),
  upload: async (file: File) => {
    const body = new FormData(); body.append('file', file)
    return json<Attachment>('/api/uploads', { method: 'POST', body })
  },
  /** Submit a chat turn. Returns as soon as the turn is persisted and queued. */
  submitChat: (payload: { conversation_id?: string; message: string; attachment_ids: string[]; mode: Mode }) =>
    json<{ job_id: string; conversation_id: string; message: Message }>('/api/chat/jobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }),
  submitSmartCode: (payload: SmartCodeRequest) =>
    json<{ job_id: string }>('/api/smart-code/jobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    }),
  /** What kind of folder is this? Drives the detected mode, so the user is not asked. */
  inspectWorkspace: (path: string) =>
    json<WorkspaceInfo>(`/api/smart-code/workspace?${new URLSearchParams({ path })}`),
  /** Continue a finished run: the previous failures become the brief, plus your instruction. */
  fixSmartCode: (jobId: string, instruction: string) =>
    json<{ job_id: string; corrected_from: string }>(`/api/smart-code/jobs/${jobId}/fix`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instruction }),
    }),
  smartCodeApply: (previewToken: string) => json<any>('/api/smart-code/apply', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ preview_token: previewToken, approved: true }),
  }),
  estimateConfig: () => json<any>('/api/estimate-code/config'),
  submitEstimate: (story: unknown) =>
    json<{ job_id: string; count: number }>('/api/estimate-code/jobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ story }),
    }),
  submitEstimateBatch: (stories: unknown[]) =>
    json<{ job_id: string; count: number }>('/api/estimate-code/batch-jobs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ stories }),
    }),
  estimateHistory: (params: {
    query?: string; source?: string; points?: number; recommendation?: string
    limit?: number; offset?: number
  } = {}) => {
    const search = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '' && value !== null) search.set(key, String(value))
    }
    return json<EstimateHistoryPage>(`/api/estimate-code/history?${search}`)
  },
  estimateHistoryStats: () => json<EstimateHistoryStats>('/api/estimate-code/history/stats'),
  estimateHistoryDetail: (id: string) =>
    json<EstimateHistoryEntry & { result: any }>(`/api/estimate-code/history/${id}`),
  decideEstimate: (id: string, payload: {
    decision: EstimateDecision; points?: number; note?: string; actual_points?: number
  }) => json<EstimateHistoryEntry & { result: any }>(
    `/api/estimate-code/history/${id}/decision`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) },
  ),
  deleteEstimateHistory: (id: string) =>
    json<void>(`/api/estimate-code/history/${id}`, { method: 'DELETE' }),
  clearEstimateHistory: () => json<{ deleted: number }>('/api/estimate-code/history/clear', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirm: true }),
  }),
  parseEstimateUpload: async (file: File) => {
    const body = new FormData(); body.append('file', file)
    return json<any>('/api/estimate-code/upload/parse', { method: 'POST', body })
  },
  jiraIssues: (project: string, query = '') => json<any[]>(`/api/estimate-code/jira/issues?${new URLSearchParams({ project, query })}`),
  writeJiraPoints: (issueKey: string, points: number) => json<any>(`/api/estimate-code/jira/${encodeURIComponent(issueKey)}/points`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ points, confirm: true }),
  }),
}
