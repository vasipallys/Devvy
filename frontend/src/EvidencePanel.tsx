import { Check, ChevronDown, CircleAlert, Clock3, Database, LoaderCircle, RotateCw, ShieldCheck } from 'lucide-react'
import type { AgentEvent } from './types'

function StatusIcon({ status }: { status: string }) {
  if (status === 'failed') return <CircleAlert />
  if (status === 'retrying') return <RotateCw className="spin" />
  if (status === 'running') return <LoaderCircle className="spin" />
  if (status === 'waiting') return <Clock3 />
  return <Check />
}

function valueText(value: unknown): string {
  if (Array.isArray(value)) return value.length > 3 ? `${value.length} items` : value.join(', ')
  if (typeof value === 'object' && value !== null) return JSON.stringify(value)
  return String(value)
}

export function EvidencePanel({ events, title = 'Run evidence', compact = false }: { events: AgentEvent[]; title?: string; compact?: boolean }) {
  const runId = events[0]?.run_id
  return <aside className={`evidence-panel ${compact ? 'compact' : ''}`} aria-label="Agent run evidence">
    <div className="evidence-heading"><div><ShieldCheck/><span><b>{title}</b><small>{runId ? `Run ${runId.slice(0, 8)}` : 'Starts with your next request'}</small></span></div><span className="evidence-local"><Database/> Local</span></div>
    {!events.length ? <div className="evidence-empty"><ShieldCheck/><p>Actions, context sources, validation loops, and approval gates will appear here. Hidden chain-of-thought is never shown or stored.</p></div>
      : <div className="evidence-timeline">{events.map((event, index) => <details key={`${event.stage}-${index}`} open={event.status === 'running' || event.status === 'failed' || index === events.length - 1}>
        <summary><span className={`evidence-icon ${event.status}`}><StatusIcon status={event.status}/></span><span><b>{event.label}</b><small>{event.stage.replaceAll('_', ' ')} · {(event.elapsed_ms / 1000).toFixed(1)}s</small></span><ChevronDown/></summary>
        {(event.detail || event.evidence) && <div className="evidence-detail">{event.detail && <p>{event.detail}</p>}{event.evidence && Object.entries(event.evidence).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><b>{valueText(value)}</b></div>)}</div>}
      </details>)}</div>}
  </aside>
}
