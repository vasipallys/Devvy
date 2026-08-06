import { Check, ChevronDown, CircleAlert, Clock3, Database, LoaderCircle, RotateCw, ShieldCheck } from 'lucide-react'
import { Tooltip } from './Tooltip'
import type { AgentEvent } from './types'

function StatusIcon({ status }: { status: string }) {
  if (status === 'failed') return <CircleAlert />
  if (status === 'retrying') return <RotateCw className="spin" />
  if (status === 'running') return <LoaderCircle className="spin" />
  if (status === 'waiting') return <Clock3 />
  return <Check />
}

/** What each status means for the run, rather than just its name. */
const STATUS_MEANING: Record<string, { label: string; why: string }> = {
  running: { label: 'Running', why: 'This stage is working now. On a CPU model a single stage can take minutes; the run is not stuck.' },
  completed: { label: 'Completed', why: 'The stage finished and produced the evidence shown below it.' },
  validated: { label: 'Validated', why: 'The model returned output that satisfied the schema and the workflow rules, so no repair was needed.' },
  retrying: { label: 'Repairing', why: 'The output did not satisfy the contract. One repair attempt runs with the specific defect named — it cannot loop indefinitely.' },
  waiting: { label: 'Waiting for you', why: 'The workflow deliberately stops here. Nothing further happens until a person decides.' },
  failed: { label: 'Failed', why: 'This stage could not complete. Where possible the workflow degrades honestly rather than inventing a result.' },
}

const isUrl = (value: unknown): value is string =>
  typeof value === 'string' && /^https?:\/\//i.test(value)

/** Render one evidence value. Lists stay legible instead of collapsing to "N items":
 *  retrieved source URLs are the citable basis for an answer, so they are shown in full
 *  and made clickable rather than counted. */
function EvidenceValue({ value }: { value: unknown }) {
  if (Array.isArray(value)) {
    if (!value.length) return <b>none</b>
    if (value.every(isUrl)) {
      return <b className="evidence-links">{value.map(url =>
        <a key={url} href={url} target="_blank" rel="noreferrer noopener" title={url}>
          {new URL(url).hostname.replace(/^www\./, '')}
        </a>)}</b>
    }
    if (value.every(item => typeof item === 'string' || typeof item === 'number')) {
      return <b className="evidence-list">{value.map((item, index) =>
        <span key={`${item}-${index}`}>{String(item)}</span>)}</b>
    }
    return <b>{value.length} items</b>
  }
  if (isUrl(value)) {
    return <b><a href={value} target="_blank" rel="noreferrer noopener">{value}</a></b>
  }
  if (typeof value === 'boolean') return <b>{value ? 'yes' : 'no'}</b>
  if (typeof value === 'object' && value !== null) return <b>{JSON.stringify(value)}</b>
  return <b>{String(value)}</b>
}

export function EvidencePanel({ events, title = 'Run evidence', compact = false }: { events: AgentEvent[]; title?: string; compact?: boolean }) {
  const runId = events[0]?.run_id
  const failed = events.filter(event => event.status === 'failed').length
  return <aside className={`evidence-panel ${compact ? 'compact' : ''}`} aria-label="Agent run evidence">
    <div className="evidence-heading">
      <div><ShieldCheck/><span><b>{title}</b><small>{runId ? `Run ${runId.slice(0, 8)}${failed ? ` · ${failed} issue(s)` : ''}` : 'Starts with your next request'}</small></span></div>
      <span className="evidence-local"><Database/> Local</span>
    </div>
    {!events.length ? <div className="evidence-empty"><ShieldCheck/><p>Actions, context sources, validation loops, and approval gates will appear here. Hidden chain-of-thought is never shown or stored.</p></div>
      : <div className="evidence-timeline">{events.map((event, index) => <details key={`${event.stage}-${index}`} open={event.status === 'running' || event.status === 'failed' || index === events.length - 1}>
        <summary><Tooltip label={STATUS_MEANING[event.status]?.label ?? event.status}
          detail={STATUS_MEANING[event.status]?.why ?? 'A stage of the run reported this state.'}>
          <span className={`evidence-icon ${event.status}`}><StatusIcon status={event.status}/></span></Tooltip><span><b>{event.label}</b><small>{event.stage.replaceAll('_', ' ')} · {(event.elapsed_ms / 1000).toFixed(1)}s</small></span><ChevronDown/></summary>
        {(event.detail || event.evidence) && <div className="evidence-detail">{event.detail && <p>{event.detail}</p>}{event.evidence && Object.entries(event.evidence).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><EvidenceValue value={value}/></div>)}</div>}
      </details>)}</div>}
  </aside>
}
