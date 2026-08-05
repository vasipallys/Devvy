import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, ArrowLeft, BrainCircuit, Check, FileSpreadsheet, History, Keyboard,
  Layers, LoaderCircle, PanelsTopLeft, Plus, Target, Trash2,
} from 'lucide-react'
import { AgentFlowDiagram } from './AgentFlowDiagram'
import { api, attachToJob } from './api'
import { EvidencePanel } from './EvidencePanel'
import { EstimateHistoryPanel } from './EstimateHistoryPanel'
import { EstimateResultView } from './EstimateResultView'
import { SystemStatusChip } from './SystemStatusChip'
import { isJobActive } from './types'
import type {
  AgentEvent, EstimateConfig, EstimateResult, Level, Recommendation, StackProfile, Story,
} from './types'

const steps = [
  'normalize', 'readiness', 'assemble_context', 'declare_stack', 'specialist_routing',
  'primary_estimate', 'specialist_analysis', 'blind_review', 'disagreement', 'critic', 'arbitration',
  'score_factors', 'apply_base_adjustments', 'apply_stack_adjustments', 'map_to_fibonacci',
  'evaluate_gates', 'decide', 'consistency_audit', 'human_review',
]
const labels: Record<string, string> = {
  normalize: 'Normalize evidence & create input hash',
  readiness: 'Evaluate story readiness',
  assemble_context: 'Bound the story evidence',
  declare_stack: 'Load stack calibration',
  specialist_routing: 'Route specialist lenses',
  primary_estimate: 'Run primary evidence assessment',
  specialist_analysis: 'Apply routed specialist lenses',
  blind_review: 'Run independent blind review',
  disagreement: 'Detect material disagreements',
  critic: 'Challenge conflicting claims',
  arbitration: 'Apply resolution policy',
  score_factors: 'Build final 16-factor scorecard',
  apply_base_adjustments: 'Apply base adjustments',
  apply_stack_adjustments: 'Apply stack adjustments',
  map_to_fibonacci: 'Map to Fibonacci',
  evaluate_gates: 'Evaluate spike & split gates',
  decide: 'Reach framework recommendation',
  consistency_audit: 'Replay and audit consistency',
  human_review: 'Hand off for human consensus',
}

/** Service stage → pipeline checkpoints it completes. Mirrors ESTIMATE_NODES on the server;
 *  the job stream carries agent events, and the checklist is derived from them so a
 *  reattaching client reconstructs the same progress from the snapshot alone. */
const NODE_MAP: Record<string, string[]> = {
  normalize: ['normalize'],
  readiness: ['readiness'],
  assemble_context: ['assemble_context'],
  declare_stack: ['declare_stack'],
  specialist_routing: ['specialist_routing'],
  primary_estimate: ['primary_estimate'],
  specialist_analysis: ['specialist_analysis'],
  blind_review: ['blind_review'],
  disagreement: ['disagreement'],
  critic: ['critic'],
  arbitration: ['arbitration'],
  score_factors: ['score_factors'],
  calculate: ['apply_base_adjustments', 'apply_stack_adjustments', 'map_to_fibonacci'],
  policy_gate: ['evaluate_gates', 'decide'],
  consistency_audit: ['consistency_audit'],
  human_review: ['human_review'],
}
const nodesFor = (event: AgentEvent): string[] =>
  event.status === 'completed' || event.status === 'validated' ? NODE_MAP[event.stage] ?? [] : []
const derivedSteps = (events: AgentEvent[]): string[] =>
  [...new Set(events.flatMap(nodesFor))]

const RECOMMENDATIONS: Record<Recommendation, { label: string; tone: string; blurb: string }> = {
  proceed: { label: 'Proceed', tone: 'ok', blurb: 'Every gate passed. This is committable.' },
  decompose: { label: 'Decompose', tone: 'warn', blurb: 'Too large to commit as one story.' },
  spike_first: { label: 'Spike first', tone: 'warn', blurb: 'Buy the missing knowledge before committing.' },
  upgrade_framework_first: { label: 'Evaluate the framework first', tone: 'danger', blurb: 'The stack is too new to estimate against.' },
  epic_discovery: { label: 'Epic — run discovery', tone: 'danger', blurb: 'A migration is not a story.' },
}

const defaultStack: StackProfile = {
  frontend: 'none', backend: 'none', database: '', maturity_level: 3, team_experience: 3,
  scenario: 'standard', new_testing_layer: false, new_observability_signal: false,
  build_pattern_change: false, additional_stacks: 0,
}
const emptyStory: Story = {
  title: '', user_story: '', acceptance_criteria: [''], technical_breakdown: '', source: 'manual',
}


/** The technology stack declaration (§3). Every control here changes the arithmetic, so
 *  each one shows the penalty it carries rather than hiding it behind the result. */
function StackPanel({ stack, config, onChange }: {
  stack: StackProfile
  config?: EstimateConfig
  onChange: (next: StackProfile) => void
}) {
  const set = <K extends keyof StackProfile>(key: K, value: StackProfile[K]) =>
    onChange({ ...stack, [key]: value })
  const maturity = config?.maturity_levels.find(item => item.level === stack.maturity_level)
  const penalties = [
    stack.maturity_level === 5 && '+3 bleeding edge',
    stack.maturity_level === 4 && '+2 emerging',
    stack.maturity_level === 1 && '+2 legacy',
    stack.team_experience <= 2 && '+2 low experience',
    stack.new_testing_layer && '+1 new test layer',
    stack.new_observability_signal && '+1 new signal',
    stack.build_pattern_change && '+1 build change',
    stack.additional_stacks > 0 && `+${stack.additional_stacks} polyglot`,
  ].filter(Boolean) as string[]

  return <section className="stack-panel">
    <header>
      <Layers size={17} />
      <span><b>Technology stack calibration</b><small>Adjusts the score before it maps to points</small></span>
      <span className={`stack-total ${penalties.length ? 'active' : ''}`}>
        {penalties.length ? `+${penalties.reduce((sum, item) => sum + Number(item.match(/\d+/)?.[0] ?? 0), 0)}` : 'No penalty'}
      </span>
    </header>
    <div className="stack-grid">
      <label>Frontend
        <select value={stack.frontend} onChange={event => set('frontend', event.target.value as StackProfile['frontend'])}>
          {config?.stacks.frontend.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </label>
      <label>Backend
        <select value={stack.backend} onChange={event => set('backend', event.target.value as StackProfile['backend'])}>
          {config?.stacks.backend.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </label>
      <label>Database <span>optional</span>
        <input value={stack.database} onChange={event => set('database', event.target.value)} placeholder="PostgreSQL" />
      </label>
      <label>Scenario
        <select value={stack.scenario} onChange={event => set('scenario', event.target.value as StackProfile['scenario'])}>
          {config?.stacks.scenarios.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </label>
    </div>
    <div className="stack-sliders">
      <label>
        <span>Framework maturity<b>{stack.maturity_level} · {maturity?.name ?? '—'}</b></span>
        <input type="range" min={1} max={5} value={stack.maturity_level}
          onChange={event => set('maturity_level', Number(event.target.value) as Level)} />
        <small>{maturity ? `${maturity.definition} Caps estimates at ${maturity.cap} points.` : ''}</small>
      </label>
      <label>
        <span>Team experience with this stack<b>{stack.team_experience} / 5</b></span>
        <input type="range" min={1} max={5} value={stack.team_experience}
          onChange={event => set('team_experience', Number(event.target.value) as Level)} />
        <small>{stack.team_experience <= 2 ? 'Scores 2 or below add +2 for the learning curve.' : 'No experience penalty applies.'}</small>
      </label>
    </div>
    <div className="stack-flags">
      {([
        ['new_testing_layer', 'Introduces a new testing layer'],
        ['new_observability_signal', 'Introduces a new observability signal'],
        ['build_pattern_change', 'Changes the build or deployment pattern'],
      ] as const).map(([key, label]) => <label key={key}>
        <input type="checkbox" checked={stack[key]} onChange={event => set(key, event.target.checked)} />
        {label}<em>+1</em>
      </label>)}
      <label className="stack-count">
        Additional stacks at a polyglot boundary
        <input type="number" min={0} max={6} value={stack.additional_stacks}
          onChange={event => set('additional_stacks', Math.max(0, Math.min(6, Number(event.target.value) || 0)))} />
      </label>
    </div>
  </section>
}






export function EstimateCodeScreen({ onHome }: { onHome: () => void }) {
  const [source, setSource] = useState<'manual' | 'upload' | 'jira'>('manual')
  const [view, setView] = useState<'new' | 'history'>('new')
  const [story, setStory] = useState<Story>(emptyStory)
  const [stack, setStack] = useState<StackProfile>(defaultStack)
  const [config, setConfig] = useState<EstimateConfig>()
  const [stepsDone, setStepsDone] = useState<string[]>([])
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<EstimateResult>()
  const [results, setResults] = useState<EstimateResult[]>([])
  const [error, setError] = useState('')
  const [runEvents, setRunEvents] = useState<AgentEvent[]>([])
  const [upload, setUpload] = useState<any>()
  const [mapping, setMapping] = useState<Record<string, string | null>>({})
  const [jiraProject, setJiraProject] = useState('')
  const [jiraQuery, setJiraQuery] = useState('')
  const [jiraStories, setJiraStories] = useState<Story[]>([])
  const [selectedJira, setSelectedJira] = useState<string[]>([])
  const [jobId, setJobId] = useState<string>()
  const abortRef = useRef<AbortController | undefined>(undefined)

  useEffect(() => { api.estimateConfig().then(setConfig).catch(cause => setError(cause.message)) }, [])
  // Reopening the screen rejoins an estimate still running on the server rather than
  // presenting an idle form while work is in flight.
  useEffect(() => {
    let disposed = false
    api.jobs().then(({ jobs }) => {
      const live = jobs.find(job => job.kind === 'estimate' && isJobActive(job.status))
      if (live && !disposed) follow(live.id)
    }).catch(() => undefined)
    return () => { disposed = true }
  }, [])
  const setField = <K extends keyof Story>(field: K, value: Story[K]) => setStory(current => ({ ...current, [field]: value }))
  function setCriterion(index: number, value: string) {
    setField('acceptance_criteria', story.acceptance_criteria.map((item, i) => i === index ? value : item))
  }

  function begin() {
    setLoading(true); setError(''); setResult(undefined); setResults([]); setStepsDone([])
    setRunEvents([]); setStatus('Starting local estimation…'); abortRef.current = new AbortController()
  }
  /** Attach to an estimation job; also used to resume one already running on the server. */
  async function follow(jobId: string) {
    setJobId(jobId)
    setLoading(true)
    abortRef.current = new AbortController()
    try {
      await attachToJob(jobId, {
        onSnapshot: job => {
          setRunEvents(job.events)
          setStepsDone(derivedSteps(job.events))
          if (job.progress) setStatus(job.progress)
        },
        onEvent: event => {
          setRunEvents(current => [...current, event])
          setStepsDone(current => [...new Set([...current, ...nodesFor(event)])])
        },
        onStatus: message => setStatus(message),
        onDone: (status, result, failure) => {
          setStepsDone(steps)
          if (status === 'succeeded' && result) {
            const produced: EstimateResult[] = result.results || []
            setResults(produced)
            setResult(produced[0])
            const failed = result.failures?.length ?? 0
            setStatus(
              produced.length > 1
                ? `Estimated ${produced.length} of ${result.count} stories`
                : 'Estimate complete',
            )
            if (failed) setError(`${failed} story/stories could not be estimated.`)
          } else if (status !== 'succeeded') {
            setError(failure || `The estimate ${status}.`)
            setStatus(`Estimate ${status}`)
          }
        },
      }, abortRef.current.signal)
    } catch (cause) {
      if ((cause as Error).name !== 'AbortError') setError((cause as Error).message)
    } finally { setLoading(false); setJobId(undefined) }
  }

  async function estimateOne(next: Story) {
    begin()
    try {
      const { job_id } = await api.submitEstimate({
        ...next, acceptance_criteria: next.acceptance_criteria.filter(Boolean), stack,
      })
      await follow(job_id)
    } catch (cause) { setLoading(false); setError((cause as Error).message) }
  }

  async function estimateMany(items: Story[]) {
    if (!items.length) return setError('Select at least one story.')
    begin()
    try {
      const { job_id } = await api.submitEstimateBatch(items.map(item => ({ ...item, stack })))
      await follow(job_id)
    } catch (cause) { setLoading(false); setError((cause as Error).message) }
  }

  async function cancelRun() {
    if (!jobId) return
    try { await api.cancelJob(jobId) } catch { /* already finished */ }
    abortRef.current?.abort()
  }
  async function parseFile(file?: File) {
    if (!file) return
    setLoading(true); setError('')
    try { const parsed = await api.parseEstimateUpload(file); setUpload(parsed); setMapping(parsed.suggested_mapping) }
    catch (cause) { setError((cause as Error).message) }
    finally { setLoading(false) }
  }
  function uploadStories(): Story[] {
    if (!upload || !mapping.title) return []
    return upload.rows.flatMap((row: Record<string, unknown>) => {
      const title = String(row[mapping.title!] || '').trim(); if (!title) return []
      const criteria = mapping.acceptance_criteria ? String(row[mapping.acceptance_criteria] || '').split(/\n|;/).filter(Boolean) : []
      return [{
        title,
        user_story: mapping.user_story ? String(row[mapping.user_story] || '') : '',
        acceptance_criteria: criteria,
        technical_breakdown: mapping.technical_breakdown ? String(row[mapping.technical_breakdown] || '') : '',
        source: 'upload' as const,
      }]
    })
  }
  async function fetchJira() {
    if (!jiraProject.trim()) return setError('Enter a Jira project key.')
    setLoading(true); setError('')
    try { setJiraStories(await api.jiraIssues(jiraProject, jiraQuery)) }
    catch (cause) { setError((cause as Error).message) }
    finally { setLoading(false) }
  }
  function download() {
    if (!result) return
    const url = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a')
    link.href = url; link.download = `${result.story.key || 'estimate'}.json`; link.click()
    URL.revokeObjectURL(url)
  }
  async function writePoints() {
    if (!result?.story.key) return
    if (result.recommendation !== 'proceed' &&
      !window.confirm(`The framework recommends "${RECOMMENDATIONS[result.recommendation].label}" rather than committing. Write ${result.points} points to ${result.story.key} anyway?`)) return
    if (!window.confirm(`Write ${result.points} points to ${result.story.key} in Jira?`)) return
    try { await api.writeJiraPoints(result.story.key, result.points); setStatus(`${result.story.key} updated in Jira`) }
    catch (cause) { setError((cause as Error).message) }
  }


  return <div className="product-screen estimate-screen">
    <header className="estimate-header">
      <button onClick={onHome}><ArrowLeft size={17} /> Home</button>
      <div className="product-brand"><BrainCircuit /><span><b>Estimate Code</b><small>Evidence-led estimation</small></span></div>
      <SystemStatusChip />
    </header>
    <main className="estimate-main">
      <section className="estimate-hero">
        <div>
          <span className="eyebrow">DEFENSIBLE BY DESIGN</span>
          <h1>Independent judgement.<br />Replayable <em>evidence.</em></h1>
          <p>Two independent assessments examine the same bounded evidence. Explicit disagreement
            controls reconcile their scores, then fixed framework arithmetic reaches the number.</p>
        </div>
        <div className="method-card">
          <Target />
          <div>
            <b>How it works</b>
            <ol>
              <li>Check readiness and route risk</li>
              <li>Run primary and blind reviews</li>
              <li>Challenge and resolve differences</li>
              <li>Replay arithmetic, then human review</li>
            </ol>
            {config && <small className="method-version">Framework v{config.framework.version}</small>}
          </div>
        </div>
      </section>

      <nav className="source-tabs with-history">
        {([
          { id: 'jira', label: 'From Jira', Icon: PanelsTopLeft },
          { id: 'manual', label: 'Manual entry', Icon: Keyboard },
          { id: 'upload', label: 'Upload Excel / CSV', Icon: FileSpreadsheet },
        ] as const).map(item =>
          <button key={item.id} className={view === 'new' && source === item.id ? 'active' : ''}
            onClick={() => { setView('new'); setSource(item.id) }}>
            <item.Icon />{item.label}
          </button>)}
        <button className={view === 'history' ? 'active' : ''} onClick={() => setView('history')}>
          <History />History
        </button>
      </nav>

      {error && <div className="estimate-error"><AlertTriangle />{error}</div>}

      {view === 'history' && <EstimateHistoryPanel
        config={config}
        onBack={() => setView('new')}
        onReEstimate={recalled => {
          // Reload the stored story and its stack into the form so a past estimate can be
          // re-run against current knowledge, rather than retyped.
          const story = recalled.story
          setStory({
            title: story.title, user_story: story.user_story,
            acceptance_criteria: story.acceptance_criteria.length ? story.acceptance_criteria : [''],
            technical_breakdown: story.technical_breakdown || '', source: 'manual',
          })
          if (story.stack) setStack(story.stack)
          setResult(undefined); setResults([]); setRunEvents([]); setStepsDone([])
          setView('new'); setSource('manual')
        }}
      />}

      {view === 'new' && <><StackPanel stack={stack} config={config} onChange={setStack} />

      <div className="estimate-workspace">
        <section className="story-card">
          {source === 'manual' && <form onSubmit={event => { event.preventDefault(); estimateOne(story) }}>
            <span className="eyebrow">ONE STORY</span>
            <h2>Describe the work</h2>
            <label>Title<input required value={story.title} onChange={event => setField('title', event.target.value)} placeholder="What outcome are we delivering?" /></label>
            <label>User story<textarea required rows={4} value={story.user_story} onChange={event => setField('user_story', event.target.value)} placeholder="As a…, I want…, so that…" /></label>
            <fieldset>
              <legend>Acceptance criteria</legend>
              {story.acceptance_criteria.map((item, index) => <div className="criterion" key={index}>
                <span>{index + 1}</span>
                <input aria-label={`Acceptance criterion ${index + 1}`} value={item} onChange={event => setCriterion(index, event.target.value)} placeholder="Observable condition of success" />
                <button type="button" aria-label={`Remove acceptance criterion ${index + 1}`} disabled={story.acceptance_criteria.length === 1} onClick={() => setField('acceptance_criteria', story.acceptance_criteria.filter((_, i) => i !== index))}><Trash2 /></button>
              </div>)}
              <button type="button" className="text-action" onClick={() => setField('acceptance_criteria', [...story.acceptance_criteria, ''])}><Plus /> Add criterion</button>
            </fieldset>
            <label>Technical breakdown <span>optional</span><textarea rows={3} value={story.technical_breakdown} onChange={event => setField('technical_breakdown', event.target.value)} placeholder="Known services, components, migrations, or constraints" /></label>
            <button className="estimate-action" disabled={loading}>{loading ? <LoaderCircle className="spin" /> : <BrainCircuit />}Build justified estimate</button>
            {loading && <button type="button" className="cancel-run" onClick={cancelRun}>Cancel request</button>}
          </form>}

          {source === 'upload' && <div className="upload-pane">
            <span className="eyebrow">BATCH ESTIMATION</span>
            <h2>Import stories</h2>
            <label className="upload-drop">
              <FileSpreadsheet /><b>Choose Excel or CSV</b><small>Up to 100 stories · 15 MB</small>
              <input type="file" accept=".csv,.xlsx" onChange={event => parseFile(event.target.files?.[0])} />
            </label>
            {upload && <>
              <p>{upload.row_count} row(s) found. Confirm column mapping — the stack profile above applies to every row.</p>
              <div className="mapping-grid">{Object.keys(mapping).map(field => <label key={field}>{field.replaceAll('_', ' ')}
                <select value={mapping[field] || ''} onChange={event => setMapping(current => ({ ...current, [field]: event.target.value || null }))}>
                  <option value="">Not mapped</option>
                  {upload.columns.map((column: string) => <option key={column}>{column}</option>)}
                </select></label>)}</div>
              <button className="estimate-action" disabled={loading || !mapping.title} onClick={() => estimateMany(uploadStories())}>Estimate {uploadStories().length} stories</button>
            </>}
          </div>}

          {source === 'jira' && <div className="jira-pane">
            <span className="eyebrow">JIRA SOURCE</span>
            <h2>Choose backlog stories</h2>
            {!config?.jira_configured
              ? <div className="setup-note">Set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN in Devvy's .env to enable this source.</div>
              : <>
                <div className="jira-search">
                  <input value={jiraProject} onChange={event => setJiraProject(event.target.value)} placeholder="Project key" />
                  <input value={jiraQuery} onChange={event => setJiraQuery(event.target.value)} placeholder="Optional text filter" />
                  <button onClick={fetchJira}>Load</button>
                </div>
                <div className="jira-list">{jiraStories.map(item => <label key={item.key}>
                  <input type="checkbox" checked={selectedJira.includes(item.key!)} onChange={event => setSelectedJira(current => event.target.checked ? [...current, item.key!] : current.filter(key => key !== item.key))} />
                  <b>{item.key}</b><span>{item.title}</span>
                </label>)}</div>
                <button className="estimate-action" disabled={!selectedJira.length || loading} onClick={() => estimateMany(jiraStories.filter(item => selectedJira.includes(item.key!)))}>Estimate selected</button>
              </>}
          </div>}
        </section>

        <section className={`estimate-pipeline ${loading ? 'active' : ''}`}>
          <span className="eyebrow">LIVE REASONING</span>
          <h2>{status || 'Your evidence pipeline'}</h2>
          {loading && <p className="pipeline-note">
            This runs on the server. You can close the tab — the estimate keeps going and
            waits for you in Activity.
          </p>}
          {steps.map((step, index) => {
            const done = stepsDone.includes(step)
            const current = loading && index === stepsDone.length
            return <div className={done ? 'done' : current ? 'current' : ''} key={step}>
              {done ? <Check /> : current ? <LoaderCircle className="spin" /> : <i />}{labels[step]}
            </div>
          })}
        </section>
      </div>

      {/* While the CPU model works, these are the only things telling the user what has
          actually happened. The flow diagram shows which agent is running and what it has
          produced; the evidence panel keeps the raw trajectory. Both appear as soon as the
          run starts and stay put. */}
      {(loading || runEvents.length > 0) && !result &&
        <AgentFlowDiagram events={runEvents} />}
      {(loading || runEvents.length > 0) && !result &&
        <EvidencePanel events={runEvents} compact title="Estimation evidence" />}

      {results.length > 0 && <section className="batch-results">
        <h2>Batch results</h2>
        {results.map(item => <button key={item.story.key || item.story.title} onClick={() => setResult(item)}>
          <strong>{item.points}</strong>
          <span><b>{item.story.title}</b><small>{item.tldr}</small></span>
          <em className={`chip-${RECOMMENDATIONS[item.recommendation].tone}`}>{RECOMMENDATIONS[item.recommendation].label}</em>
        </button>)}
      </section>}

      {result && <EstimateResultView
        result={result}
        config={config}
        events={runEvents}
        onDownload={download}
        onWriteJira={config?.jira_write_enabled && result.story.source === 'jira' ? writePoints : undefined}
      />}
      </>}
    </main>
  </div>
}
