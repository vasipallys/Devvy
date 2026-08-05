import { useEffect, useState } from 'react'
import {
  ArrowLeft, BrainCircuit, Check, CircleAlert, Code2, LoaderCircle, MessageSquare, Mic,
  PlugZap, RotateCw, Square, Timer,
} from 'lucide-react'
import { api } from './api'
import { EvidencePanel } from './EvidencePanel'
import { SystemStatusChip } from './SystemStatusChip'
import { useJobs } from './useJobs'
import { isJobActive, type JobDetail, type JobKind, type JobStatus, type JobSummary } from './types'

const KIND_META: Record<JobKind, { label: string; Icon: typeof MessageSquare }> = {
  chat: { label: 'Chat', Icon: MessageSquare },
  estimate: { label: 'Estimate Code', Icon: BrainCircuit },
  'smart-code': { label: 'Smart Code', Icon: Code2 },
  talk: { label: 'Talk', Icon: Mic },
}

const STATUS_META: Record<JobStatus, { label: string; tone: string }> = {
  queued: { label: 'Queued', tone: 'wait' },
  running: { label: 'Running', tone: 'run' },
  succeeded: { label: 'Completed', tone: 'ok' },
  failed: { label: 'Failed', tone: 'bad' },
  cancelled: { label: 'Cancelled', tone: 'muted' },
  interrupted: { label: 'Interrupted', tone: 'bad' },
}

function StatusIcon({ status }: { status: JobStatus }) {
  if (status === 'running') return <LoaderCircle className="spin" size={14} />
  if (status === 'queued') return <Timer size={14} />
  if (status === 'succeeded') return <Check size={14} />
  if (status === 'interrupted') return <PlugZap size={14} />
  if (status === 'cancelled') return <Square size={13} />
  return <CircleAlert size={14} />
}

function elapsed(job: JobSummary): string {
  const start = new Date(job.started_at || job.created_at).getTime()
  const end = job.completed_at ? new Date(job.completed_at).getTime() : Date.now()
  const seconds = Math.max(0, Math.round((end - start) / 1000))
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`
}

/** Human-readable outcome per workflow, so the list is useful without opening anything. */
function outcome(job: JobDetail): string {
  const result = job.result
  if (!result) return job.error || job.output_preview || '—'
  if (job.kind === 'estimate') {
    const first = result.results?.[0]
    if (result.count > 1) return `${result.results?.length ?? 0} of ${result.count} stories estimated`
    return first ? `${first.points} points · ${first.confidence} confidence · ${String(first.recommendation).replaceAll('_', ' ')}` : '—'
  }
  if (job.kind === 'smart-code') {
    const preview = result.preview
    if (!preview) return '—'
    return preview.edits?.length
      ? `${preview.edits.length} file edit(s) · ${preview.can_apply ? 'awaiting approval' : 'verification failed'}`
      : `${preview.findings?.length ?? 0} review finding(s)`
  }
  return (result.response || result.message?.content || job.output_text || '').slice(0, 400)
}

function JobRow({ job, onOpen, onCancel, selected }: {
  job: JobSummary
  onOpen: () => void
  onCancel: () => void
  selected: boolean
}) {
  const meta = KIND_META[job.kind] ?? { label: job.kind, Icon: MessageSquare }
  const status = STATUS_META[job.status]
  return <div className={`job-row ${selected ? 'selected' : ''}`}>
    <button className="job-open" onClick={onOpen}>
      <span className={`job-status tone-${status.tone}`}><StatusIcon status={job.status} /></span>
      <span className="job-body">
        <b>{job.title || meta.label}</b>
        <small>
          <meta.Icon size={11} /> {meta.label} · {status.label} · {elapsed(job)}
          {job.progress && isJobActive(job.status) ? ` · ${job.progress}` : ''}
        </small>
        {job.error && <em className="job-error">{job.error}</em>}
      </span>
    </button>
    {isJobActive(job.status) &&
      <button className="job-cancel" onClick={onCancel} title="Cancel this request">Cancel</button>}
  </div>
}

export function ActivityScreen({ onHome, onOpenConversation }: {
  onHome: () => void
  onOpenConversation?: (conversationId: string) => void
}) {
  const { jobs, active, error, refresh, cancel } = useJobs()
  const [selected, setSelected] = useState<string>()
  const [detail, setDetail] = useState<JobDetail>()
  const [detailError, setDetailError] = useState('')

  useEffect(() => {
    if (!selected) { setDetail(undefined); return }
    let disposed = false
    const load = async () => {
      try {
        const payload = await api.job(selected)
        if (!disposed) { setDetail(payload); setDetailError('') }
      } catch (cause) {
        if (!disposed) setDetailError((cause as Error).message)
      }
    }
    load()
    // A selected running job refreshes alongside the list so its output grows in place.
    const timer = window.setInterval(load, 2500)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [selected, jobs])

  const running = jobs.filter(job => isJobActive(job.status))
  const finished = jobs.filter(job => !isJobActive(job.status))

  return <div className="product-screen activity-screen">
    <header className="product-header">
      <button onClick={onHome}><ArrowLeft size={17} /> Home</button>
      <div className="product-brand">
        <Timer /><span><b>Activity</b><small>Requests running and completed</small></span>
      </div>
      <SystemStatusChip />
    </header>

    <div className="activity-layout">
      <aside className="activity-list">
        <div className="activity-head">
          <span className="eyebrow">{active > 0 ? `${active} IN PROGRESS` : 'ALL CAUGHT UP'}</span>
          <button className="text-action" onClick={refresh}><RotateCw size={13} /> Refresh</button>
        </div>
        {error && <div className="product-error">{error}</div>}

        {running.length > 0 && <>
          <div className="activity-group">Running</div>
          {running.map(job => <JobRow key={job.id} job={job} selected={selected === job.id}
            onOpen={() => setSelected(job.id)} onCancel={() => cancel(job.id)} />)}
        </>}

        <div className="activity-group">Recent</div>
        {finished.length === 0 && running.length === 0 &&
          <p className="activity-empty">
            Nothing yet. Requests you start in Chat, Talk, Smart Code, or Estimate Code appear
            here — including any that were still running when you last closed the browser.
          </p>}
        {finished.map(job => <JobRow key={job.id} job={job} selected={selected === job.id}
          onOpen={() => setSelected(job.id)} onCancel={() => cancel(job.id)} />)}
      </aside>

      <main className="activity-detail">
        {!detail && !detailError && <div className="activity-placeholder">
          <Timer size={34} />
          <p>Select a request to see its status, evidence, and response.</p>
        </div>}
        {detailError && <div className="product-error">{detailError}</div>}
        {detail && <>
          <section className="activity-summary">
            <div>
              <span className="eyebrow">{KIND_META[detail.kind]?.label ?? detail.kind}</span>
              <h2>{detail.title}</h2>
              <p className={`activity-outcome tone-${STATUS_META[detail.status].tone}`}>
                <StatusIcon status={detail.status} /> {STATUS_META[detail.status].label} · {elapsed(detail)}
              </p>
            </div>
            {detail.kind === 'chat' && detail.conversation_id && onOpenConversation &&
              <button className="primary-action" onClick={() => onOpenConversation(detail.conversation_id!)}>
                Open conversation
              </button>}
          </section>

          {detail.status === 'interrupted' && <div className="product-error">
            This request was still running when the backend restarted. A generation cannot be
            resumed, so it was stopped rather than left claiming to be in progress. Run it again.
          </div>}

          <section className="activity-response">
            <span className="eyebrow">RESPONSE</span>
            <pre>{outcome(detail) || 'No output was produced.'}</pre>
          </section>

          {detail.output_text && detail.kind !== 'chat' && <section className="activity-response">
            <span className="eyebrow">STREAMED OUTPUT</span>
            <pre>{detail.output_text}</pre>
          </section>}

          <EvidencePanel events={detail.events} compact title="Run evidence" />
        </>}
      </main>
    </div>
  </div>
}
