import { useState } from 'react'
import { Check, CircleAlert, LoaderCircle, RotateCw, UserCheck } from 'lucide-react'
import { api } from './api'
import { Tooltip } from './Tooltip'
import type { EstimateDecision, EstimateHistoryEntry } from './types'

/**
 * Where "human decision required" actually gets answered.
 *
 * The pipeline ends by declaring that the team owns the number — and for a long time that was
 * the last word on the page. The recommendation said a decision was pending, and there was
 * nowhere on that page to make one. The panel existed, but only in History, so answering the
 * question meant leaving the answer.
 *
 * It lives here now so the fresh result and the recalled record mount the same component and
 * behave identically. A decision recorded from either place is the same record.
 */

export function relativeTime(iso: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`
  if (seconds < 604_800) return `${Math.floor(seconds / 86_400)}d ago`
  return new Date(iso).toLocaleDateString()
}


const DECISIONS: { id: EstimateDecision; label: string; hint: string; why: string }[] = [
  { id: 'accept', label: 'Accept', hint: 'Commit to the recommended points',
    why: 'Records that the team took the number as calculated. Add the actual once the work lands and this story starts measuring whether the framework is right.' },
  { id: 'override', label: 'Override', hint: 'Commit to a different number',
    why: 'Records a different number and the reason. Overrides are not treated as errors — a consistent direction across stories is the signal that the rubric needs tuning for this team.' },
  { id: 'spike', label: 'Spike first', hint: 'Buy the missing knowledge, then re-estimate',
    why: 'For a story whose size is unknown rather than large. A timeboxed spike answers the open question, then the story is estimated again with real evidence.' },
  { id: 'decompose', label: 'Decompose', hint: 'Split the story and estimate the parts',
    why: 'For a story that is genuinely too big to commit to as one unit. The split guidance above proposes where the seams are.' },
]

/** Where the pipeline's "human decision required" actually gets answered.
 *
 *  Without this the recommendation is the last word and the calibration statistics can only
 *  ever report what was estimated, never whether the estimate held. */
export function DecisionPanel({ entry, onDecided }: {
  entry: EstimateHistoryEntry
  onDecided: (updated: EstimateHistoryEntry) => void
}) {
  const [choice, setChoice] = useState<EstimateDecision | undefined>(entry.decision ?? undefined)
  const [points, setPoints] = useState(String(entry.decided_points ?? entry.points))
  const [note, setNote] = useState(entry.decision_note ?? '')
  const [actual, setActual] = useState(entry.actual_points === null ? '' : String(entry.actual_points))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function save() {
    if (!choice) return
    setSaving(true); setError('')
    try {
      onDecided(await api.decideEstimate(entry.id, {
        decision: choice,
        points: choice === 'override' ? Number(points) : undefined,
        note,
        actual_points: actual.trim() ? Number(actual) : undefined,
      }))
    } catch (cause) { setError((cause as Error).message) }
    finally { setSaving(false) }
  }

  return <section className="decision-panel">
    <header>
      <UserCheck size={16} />
      <span>
        <b>{entry.decision ? 'Team decision' : 'This estimate is a recommendation'}</b>
        <small>{entry.decision
          ? `Recorded ${entry.decided_at ? relativeTime(entry.decided_at) : ''}`
          : 'Record what the team agreed so the estimate can be measured against reality.'}</small>
      </span>
      {entry.decision && <span className={`decision-chip ${entry.decision}`}>
        {DECISIONS.find(item => item.id === entry.decision)?.label}
        {entry.decided_points !== null && entry.decided_points !== entry.points
          ? ` · ${entry.decided_points} pts` : ''}
      </span>}
    </header>

    <div className="decision-options" role="group" aria-label="Team decision">
      {DECISIONS.map(item => <Tooltip key={item.id} label={item.label} detail={item.why}>
        <button
          className={choice === item.id ? 'active' : ''}
          aria-pressed={choice === item.id}
          onClick={() => setChoice(item.id)}>
          <b>{item.label}</b><small>{item.hint}</small>
        </button>
      </Tooltip>)}
    </div>

    <div className="decision-fields">
      {choice === 'override' && <label>Agreed points
        <select value={points} onChange={event => setPoints(event.target.value)}>
          {[3, 5, 8, 13, 21, 34].map(value => <option key={value} value={value}>{value}</option>)}
        </select>
      </label>}
      <label>Actual points <span>optional, after delivery</span>
        <input type="number" min={0} value={actual} placeholder="—"
          onChange={event => setActual(event.target.value)} />
      </label>
      <label className="decision-note">Note <span>optional</span>
        <input value={note} placeholder="Why the team decided this"
          onChange={event => setNote(event.target.value)} />
      </label>
    </div>

    {error && <div className="estimate-error"><CircleAlert size={15} />{error}</div>}
    <button className="estimate-action" disabled={!choice || saving} onClick={save}>
      {saving ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}
      {entry.decision ? 'Update decision' : 'Record decision'}
    </button>
  </section>
}


/** Re-run this story from scratch, or with a correction, carrying nothing from the last run.
 *
 *  Deliberately not a "refine" loop. An estimate that has been shown to a person anchors them,
 *  and an estimate that is fed its own previous answer anchors itself — the second number comes
 *  back a polite adjustment of the first, which is exactly the bias blind scoring exists to
 *  remove. So a correction is appended to the story as new evidence and the pipeline runs
 *  clean: same rubric, same rules, no memory of what it said last time.
 */
export function ReEstimatePanel({ onReEstimate }: {
  onReEstimate: (correction: string) => void | Promise<void>
}) {
  const [open, setOpen] = useState(false)
  const [correction, setCorrection] = useState('')
  const [busy, setBusy] = useState(false)

  async function run(text: string) {
    setBusy(true)
    try { await onReEstimate(text) } finally { setBusy(false) }
  }

  return <section className="reestimate-panel">
    <header>
      <RotateCw size={16} />
      <span>
        <b>Not right? Estimate it again.</b>
        <small>
          Re-running starts from the story, not from this result. The previous number is never
          shown to the model, so the second estimate is an independent reading rather than an
          adjustment of the first.
        </small>
      </span>
    </header>

    <div className="reestimate-actions">
      <Tooltip
        label="Run again unchanged"
        detail="Same story, same rubric, no memory of this result. Two runs that disagree are
          telling you the story is ambiguous — which is information, not a fault."
      >
        <button className="estimate-action" disabled={busy} onClick={() => run('')}>
          {busy ? <LoaderCircle className="spin" size={15} /> : <RotateCw size={15} />}
          Re-estimate from scratch
        </button>
      </Tooltip>
      <button className="text-action" onClick={() => setOpen(value => !value)}
        aria-expanded={open}>
        {open ? 'Cancel' : 'Add missing detail and re-estimate'}
      </button>
    </div>

    {open && <div className="reestimate-correction">
      <label>
        What did the story leave out?
        <textarea
          rows={3}
          value={correction}
          placeholder="e.g. the risk category is stored on the existing customer table; no new service is needed"
          onChange={event => setCorrection(event.target.value)}
        />
        <span>
          This is added to the story as evidence, not as feedback about the estimate. Anything
          the scorecard marked "the provided text does not contain this information" is worth
          answering here.
        </span>
      </label>
      <button className="estimate-action" disabled={busy || !correction.trim()}
        onClick={() => run(correction.trim())}>
        {busy ? <LoaderCircle className="spin" size={15} /> : <Check size={15} />}
        Re-estimate with this detail
      </button>
    </div>}
  </section>
}
