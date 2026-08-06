import { useCallback, useEffect, useState } from 'react'
import {
  ArrowLeft, ChartNoAxesColumn, Check, CircleAlert, History, LoaderCircle, RotateCw, Search,
  Trash2, UserCheck,
} from 'lucide-react'
import { api } from './api'
import { EstimateResultView, RECOMMENDATIONS } from './EstimateResultView'
import type {
  EstimateConfig, EstimateDecision, EstimateHistoryEntry, EstimateHistoryStats,
  EstimateResult, Points,
} from './types'
import { Tooltip } from './Tooltip'

const PAGE_SIZE = 25
const POINT_FILTERS: Points[] = [3, 5, 8, 13, 21, 34]

function relativeTime(iso: string): string {
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000))
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h ago`
  if (seconds < 604_800) return `${Math.floor(seconds / 86_400)}d ago`
  return new Date(iso).toLocaleDateString()
}

/** Calibration summary. A list of past estimates is a log; the distribution is what tells a
 *  team something about how they estimate. */
function StatsBar({ stats }: { stats: EstimateHistoryStats }) {
  if (!stats.total) return null
  const busiest = Math.max(...Object.values(stats.points), 1)
  return <section className="history-stats">
    <Tooltip label="Stories on record" detail="Every completed estimate is kept, keyed by story. History is not purged on a timer — an estimate is the artefact a team refers back to."><div className="history-stat">
      <span>Estimated</span><b>{stats.total}</b><small>stories on record</small>
    </div></Tooltip>
    <Tooltip label="Median points" detail="The middle estimate across every story here. A median that drifts upward usually means stories are being split too late, not that work got harder."><div className="history-stat">
      <span>Median</span><b>{stats.median_points ?? '—'}</b><small>points</small>
    </div></Tooltip>
    <Tooltip label="Model-scored factors"
      detail="The share of factors the model actually judged, rather than ones filled in from keywords when it skipped them. A low share means the estimates here lean on heuristics, and the stories probably need more detail.">
      <div className="history-stat">
        <span>Model-scored</span>
        <b>{stats.model_scored_share === null ? '—' : `${Math.round(stats.model_scored_share * 100)}%`}</b>
        <small>of all factors</small>
      </div>
    </Tooltip>
    {/* Calibration, not volume: whether teams take the number, and how it held up. */}
    <Tooltip label="Decisions captured" detail="How many estimates a person accepted or overrode. Without a decision the loop is open, and nothing here can be calibrated against reality."><div className="history-stat">
      <span>Decided</span><b>{stats.decided}<em>/{stats.total}</em></b>
      <small>{stats.overridden ? `${stats.overridden} overridden` : 'none overridden'}</small>
    </div></Tooltip>
    <Tooltip label="Override bias" detail="The average direction and size of human overrides. A persistent bias means the framework is mis-tuned for this team, and is worth fixing at the rubric rather than story by story."><div className="history-stat">
      <span>Override bias</span>
      <b>{stats.override_bias === null ? '—'
        : `${stats.override_bias > 0 ? '+' : ''}${stats.override_bias}`}</b>
      <small>{stats.override_bias === null ? 'no overrides yet'
        : stats.override_bias > 0 ? 'teams size up' : 'teams size down'}</small>
    </div></Tooltip>
    <Tooltip label="Accuracy against actuals" detail="The average distance between recommended points and what the work actually took. This is the only number here that measures the framework rather than describing it."><div className="history-stat">
      <span>Accuracy</span>
      <b>{stats.actual_accuracy === null ? '—' : `±${stats.actual_accuracy}`}</b>
      <small>{stats.with_actuals ? `from ${stats.with_actuals} actual(s)` : 'no actuals recorded'}</small>
    </div></Tooltip>
    <div className="history-distribution" aria-label="Points distribution">
      <span><ChartNoAxesColumn size={12} /> Distribution</span>
      <div>
        {POINT_FILTERS.map(point => {
          const count = stats.points[String(point)] ?? 0
          return <i key={point} title={`${count} story/stories at ${point} points`}
            style={{ height: `${Math.max(3, (count / busiest) * 34)}px` }}
            className={count ? 'on' : ''}><em>{point}</em></i>
        })}
      </div>
    </div>
  </section>
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
function DecisionPanel({ entry, onDecided }: {
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

export function EstimateHistoryPanel({
  config, onBack, onReEstimate, initialEntryId, onEntryChange,
}: {
  config?: EstimateConfig
  onBack: () => void
  onReEstimate?: (result: EstimateResult) => void
  /** Opened directly from a shared link such as #/estimate/history/{id}. */
  initialEntryId?: string
  onEntryChange?: (id?: string) => void
}) {
  const [page, setPage] = useState<{ items: EstimateHistoryEntry[]; total: number }>({ items: [], total: 0 })
  const [stats, setStats] = useState<EstimateHistoryStats>()
  const [query, setQuery] = useState('')
  const [points, setPoints] = useState<number>()
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<EstimateResult>()
  const [selectedId, setSelectedId] = useState<string | undefined>(initialEntryId)
  const [selectedEntry, setSelectedEntry] = useState<EstimateHistoryEntry>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const [listed, summary] = await Promise.all([
        api.estimateHistory({ query, points, limit: PAGE_SIZE, offset }),
        api.estimateHistoryStats(),
      ])
      setPage({ items: listed.items, total: listed.total })
      setStats(summary)
      setError('')
    } catch (cause) { setError((cause as Error).message) }
    finally { setLoading(false) }
  }, [query, points, offset])

  // Debounced so typing in the search box does not fire a request per keystroke.
  useEffect(() => {
    const timer = window.setTimeout(load, query ? 250 : 0)
    return () => window.clearTimeout(timer)
  }, [load, query])

  async function open(id: string) {
    setSelectedId(id)
    onEntryChange?.(id)
    try {
      const record = await api.estimateHistoryDetail(id)
      setSelected(record.result as EstimateResult)
      setSelectedEntry(record)
    } catch (cause) { setError((cause as Error).message); setSelectedId(undefined) }
  }

  // A link straight to #/estimate/history/{id} has a selection but no loaded payload. This
  // also runs when the URL changes under a mounted panel, so Back closes the open entry.
  useEffect(() => {
    if (initialEntryId !== undefined) setSelectedId(initialEntryId)
    else if (!selectedId) setSelected(undefined)
  }, [initialEntryId])
  useEffect(() => {
    if (selectedId && !selected) open(selectedId)
    if (!selectedId) setSelected(undefined)
  }, [selectedId])

  async function remove(entry: EstimateHistoryEntry) {
    if (!window.confirm(`Remove the estimate for "${entry.title}" from history?`)) return
    try {
      await api.deleteEstimateHistory(entry.id)
      if (selectedId === entry.id) { setSelected(undefined); setSelectedId(undefined) }
      await load()
    } catch (cause) { setError((cause as Error).message) }
  }

  function download(entry: EstimateHistoryEntry, result: EstimateResult) {
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url
    link.download = `${entry.issue_key || entry.title.slice(0, 40) || 'estimate'}.json`
    link.click()
    URL.revokeObjectURL(url)
  }

  if (selected && selectedId) {
    const entry = page.items.find(item => item.id === selectedId)
    return <div className="history-detail">
      <div className="history-detail-bar">
        <button className="text-action" onClick={() => {
          setSelected(undefined); setSelectedId(undefined); onEntryChange?.(undefined)
        }}>
          <ArrowLeft size={14} /> Back to history
        </button>
        <span>{entry ? `Estimated ${relativeTime(entry.created_at)}` : ''}</span>
        <div className="history-detail-actions">
          {entry && <button className="text-action" onClick={() => download(entry, selected)}>Download JSON</button>}
          {onReEstimate && <button className="text-action" onClick={() => onReEstimate(selected)}>
            <RotateCw size={13} /> Re-estimate this story
          </button>}
        </div>
      </div>
      {selectedEntry && <DecisionPanel entry={selectedEntry} onDecided={updated => {
        setSelectedEntry(updated)
        load()
      }} />}
      {/* The stored payload renders through the same component as a fresh run, so a recalled
          estimate shows its full scorecard, ledger, and gates rather than a summary. */}
      <EstimateResultView result={selected} config={config} events={[]} />
    </div>
  }

  return <div className="history-panel">
    <div className="history-toolbar">
      <button className="text-action" onClick={onBack}><ArrowLeft size={14} /> New estimate</button>
      <label className="history-search">
        <Search size={14} />
        <input value={query} placeholder="Search by story, Jira key, or summary"
          onChange={event => { setQuery(event.target.value); setOffset(0) }} />
      </label>
      <div className="history-filters" role="group" aria-label="Filter by points">
        <button className={points === undefined ? 'active' : ''}
          onClick={() => { setPoints(undefined); setOffset(0) }}>All</button>
        {POINT_FILTERS.map(value => <button key={value} className={points === value ? 'active' : ''}
          onClick={() => { setPoints(points === value ? undefined : value); setOffset(0) }}>{value}</button>)}
      </div>
    </div>

    {error && <div className="estimate-error"><CircleAlert size={16} />{error}</div>}
    {stats && <StatsBar stats={stats} />}

    {loading && !page.items.length && <div className="history-empty"><LoaderCircle className="spin" /> Loading history…</div>}

    {!loading && !page.items.length && <div className="history-empty">
      <History size={30} />
      <p>{query || points
        ? 'No estimate matches that search.'
        : 'No estimates yet. Every story you estimate is recorded here with its full scorecard, so you can revisit the reasoning and compare new work against it.'}</p>
    </div>}

    {page.items.length > 0 && <>
      <ul className="history-list">
        {page.items.map(entry => {
          const verdict = entry.recommendation ? RECOMMENDATIONS[entry.recommendation] : undefined
          return <li key={entry.id}>
            <button className="history-entry" onClick={() => open(entry.id)}>
              <span className="history-points">{entry.points}</span>
              <span className="history-body">
                <b>{entry.issue_key ? `${entry.issue_key} · ` : ''}{entry.title}</b>
                <small>{entry.tldr}</small>
                <em>
                  {relativeTime(entry.created_at)} · base {entry.base_sum} → {entry.adjusted_score}
                  {entry.band ? ` (${entry.band})` : ''} · {entry.confidence || '—'} confidence
                  {entry.heuristic_filled ? ` · ${entry.heuristic_filled} factor(s) inferred` : ''}
                </em>
              </span>
              {verdict && <span className={`chip-${verdict.tone}`}>{verdict.label}</span>}
            </button>
            <button className="history-remove" aria-label={`Remove ${entry.title} from history`}
              onClick={() => remove(entry)}><Trash2 size={14} /></button>
          </li>
        })}
      </ul>
      <div className="history-pager">
        <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Newer</button>
        <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {page.total}</span>
        <button disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Older</button>
      </div>
    </>}
  </div>
}
