import type { Attachment, Conversation, Message, Mode, SmartCodeRequest, SystemStatus } from './types'

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
) {
  const response = await fetch(`${API}${path}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(payload), signal,
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

export const api = {
  systemStatus: () => json<SystemStatus>('/api/system/status'),
  conversations: () => json<Conversation[]>('/api/conversations'),
  create: () => json<Conversation>('/api/conversations', { method: 'POST' }),
  messages: (id: string) => json<Message[]>(`/api/conversations/${id}/messages`),
  remove: (id: string) => json<void>(`/api/conversations/${id}`, { method: 'DELETE' }),
  upload: async (file: File) => {
    const body = new FormData(); body.append('file', file)
    return json<Attachment>('/api/uploads', { method: 'POST', body })
  },
  stream: async (payload: { conversation_id?: string; message: string; attachment_ids: string[]; mode: Mode }, onEvent: (event: any) => void, signal?: AbortSignal) => {
    const response = await fetch(`${API}/api/chat/stream`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload), signal })
    if (!response.ok || !response.body) throw new Error(await response.text())
    const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
    while (true) {
      const { value, done } = await reader.read(); if (done) break
      buffer += decoder.decode(value, { stream: true })
      const chunks = buffer.split('\n\n'); buffer = chunks.pop() || ''
      for (const chunk of chunks) if (chunk.startsWith('data: ')) onEvent(JSON.parse(chunk.slice(6)))
    }
  },
  smartCodePreview: (payload: SmartCodeRequest, onEvent: (event: string, data: any) => void, signal?: AbortSignal) =>
    consumeSSE('/api/smart-code/preview', payload, onEvent, signal),
  smartCodeApply: (previewToken: string) => json<any>('/api/smart-code/apply', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ preview_token: previewToken, approved: true }),
  }),
  estimateConfig: () => json<any>('/api/estimate-code/config'),
  estimate: (story: unknown, onEvent: (event: string, data: any) => void, signal?: AbortSignal) =>
    consumeSSE('/api/estimate-code/estimate', { story }, onEvent, signal),
  estimateBatch: (stories: unknown[], onEvent: (event: string, data: any) => void, signal?: AbortSignal) =>
    consumeSSE('/api/estimate-code/batch', { stories }, onEvent, signal),
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
