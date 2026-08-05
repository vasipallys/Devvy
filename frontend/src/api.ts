import type {
  AgentEvent, Attachment, Conversation, JobDetail, JobStatus, JobSummary, Message, Mode,
  SmartCodeRequest, SystemStatus,
} from './types'

export const API = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8765'

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, init)
  if (!response.ok) {
    const text = await response.text()
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
    onDone?: (status: JobStatus, result?: Record<string, any>, error?: string) => void
  },
  signal?: AbortSignal,
) {
  let text = ''
  let lastSeq = 0
  await consumeSSE(`/api/jobs/${jobId}/stream`, undefined, (event, data) => {
    if (event === 'snapshot') {
      text = data.output_text || ''
      handlers.onSnapshot?.(data as JobDetail)
      handlers.onText?.(text)
      for (const item of (data.events ?? []) as AgentEvent[]) {
        lastSeq = Math.max(lastSeq, item.seq ?? 0)
      }
    } else if (event === 'token') {
      // Deltas already inside the snapshot start before its length; skip those.
      if (data.offset >= text.length) {
        text += data.content
        handlers.onText?.(text)
      }
    } else if (event === 'agent_event') {
      // Same overlap as tokens: the snapshot already carried everything up to lastSeq.
      const seq = (data as AgentEvent).seq ?? 0
      if (seq === 0 || seq > lastSeq) {
        lastSeq = Math.max(lastSeq, seq)
        handlers.onEvent?.(data as AgentEvent)
      }
    } else if (event === 'status') {
      handlers.onStatus?.(data.message)
    } else if (event === 'done') {
      handlers.onDone?.(data.status, data.result, data.error)
    }
  }, signal, 'GET')
}

export const api = {
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
