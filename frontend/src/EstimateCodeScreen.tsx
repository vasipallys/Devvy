import { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, Anchor, ArrowLeft, BrainCircuit, Check, ChevronDown, CircleSlash, Download,
  FileSpreadsheet, FlaskConical, GitBranch, Keyboard, Layers, Lightbulb, LoaderCircle,
  PanelsTopLeft, Plus, ShieldCheck, Sigma, Target, TrendingDown, Trash2, X,
} from 'lucide-react'
import { api } from './api'
import { EvidencePanel } from './EvidencePanel'
import { SystemStatusChip } from './SystemStatusChip'
import type {
  AgentEvent, Calculation, EstimateConfig, EstimateResult, FactorScore, Level, PolicyCheck,
  Recommendation, StackProfile, Story,
} from './types'

const steps = [
  'assemble_context', 'declare_stack', 'score_factors', 'apply_base_adjustments',
  'apply_stack_adjustments', 'map_to_fibonacci', 'evaluate_gates', 'decide',
]
const labels: Record<string, string> = {
  assemble_context: 'Bound the story evidence',
  declare_stack: 'Load stack calibration',
  score_factors: 'Score 16 factors',
  apply_base_adjustments: 'Apply base adjustments',
  apply_stack_adjustments: 'Apply stack adjustments',
  map_to_fibonacci: 'Map to Fibonacci',
  evaluate_gates: 'Evaluate spike & split gates',
  decide: 'Reach a decision',
}

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

function Detail({ title, count, open, children }: { title: string; count?: number; open?: boolean; children: React.ReactNode }) {
  return <details className="estimate-detail" open={open}>
    <summary><span>{title}{count !== undefined && <em>{count}</em>}</span><ChevronDown size={17} /></summary>
    <div>{children}</div>
  </details>
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

/** The replayable audit trail (§8-§9). This is the product's central claim: a reader can
 *  add these numbers up by hand and reach the same story points. */
function CalculationLedger({ calculation }: { calculation: Calculation }) {
  const [showSkipped, setShowSkipped] = useState(false)
  const visible = calculation.steps.filter(step => showSkipped || step.applied)
  const skipped = calculation.steps.length - calculation.steps.filter(step => step.applied).length

  return <div className="ledger">
    <div className="ledger-summary">
      {[
        ['Base sum', calculation.base_sum, '16 factors'],
        ['Base adjustments', calculation.base_adjustment_total, '§8.1'],
        ['Stack adjustments', calculation.stack_adjustment_total, '§8.2'],
        ['Adjusted score', calculation.adjusted_score, `band ${calculation.band}`],
      ].map(([label, value, note]) => <div key={label as string}>
        <span>{label}</span><b>{typeof value === 'number' && value > 0 && label !== 'Base sum' && label !== 'Adjusted score' ? `+${value}` : value}</b><small>{note}</small>
      </div>)}
    </div>
    <button className="ledger-toggle" onClick={() => setShowSkipped(current => !current)}>
      {showSkipped ? 'Hide' : 'Show'} the {skipped} rules that did not fire
    </button>
    <table className="ledger-table">
      <thead><tr><th>Rule</th><th>Spec</th><th>Effect</th><th>Running total</th></tr></thead>
      <tbody>
        {visible.map(step => <tr key={step.rule} className={step.applied ? '' : 'skipped'}>
          <td>
            <span className={`ledger-mark ${step.applied ? 'on' : 'off'}`}>
              {step.applied ? <Check size={12} /> : <X size={12} />}
            </span>
            {step.label}
          </td>
          <td><code>{step.reference}</code></td>
          <td>{step.applied && step.delta ? `+${step.delta}` : step.applied ? '—' : 'not applied'}</td>
          <td><b>{step.running_total}</b></td>
        </tr>)}
      </tbody>
    </table>
    <p className="ledger-outcome">
      Adjusted score <b>{calculation.adjusted_score}</b> falls in band <b>{calculation.band}</b>,
      which maps to <b>{calculation.mapped_points} points</b>.
      {calculation.cap_exceeded
        ? ` That exceeds the ${calculation.maturity_cap}-point cap for this framework maturity, so the decision escalates.`
        : ` The framework maturity cap of ${calculation.maturity_cap} points is not exceeded.`}
    </p>
  </div>
}

function ScoreRow({ item }: { item: FactorScore }) {
  return <div className={`factor factor-${item.group}`}>
    <div className="factor-head">
      <span className="factor-number">{item.number}</span>
      <b>{item.label}</b>
      <span className={`factor-provenance ${item.provenance}`}>
        {item.provenance === 'model' ? 'scored' : 'inferred'}
      </span>
      <span className={`factor-score s${item.score}`}>{item.score}</span>
    </div>
    <div className="factor-bar" aria-hidden>
      {[1, 2, 3, 4, 5].map(step => <i key={step} className={step <= item.score ? `on s${item.score}` : ''} />)}
    </div>
    <small>{item.reason}</small>
    {item.stack_notes.map(note => <em key={note}>{note}</em>)}
  </div>
}

function GateList({ checks }: { checks: PolicyCheck[] }) {
  return <ul className="gate-list">
    {checks.map(check => <li key={check.rule} className={check.passed ? 'pass' : 'fail'}>
      <span>{check.passed ? <Check size={13} /> : <AlertTriangle size={13} />}</span>
      <div><b>{check.label}</b><small>{check.detail}</small></div>
      <code>{check.reference}</code>
    </li>)}
  </ul>
}

function DetailedReasoningPanel({ result }: { result: EstimateResult }) {
  const reasoning = result.detailed_reasoning
  return <div className="reasoning-panel">
    <div className="reasoning-conclusion"><BrainCircuit size={18} /><p>{reasoning.conclusion}</p></div>
    <div className="reasoning-formula" aria-label="Estimation formula">
      <span><small>Factor subtotal</small><b>{result.calculation.base_sum}</b></span>
      <i>+</i><span><small>Base rules</small><b>{result.calculation.base_adjustment_total}</b></span>
      <i>+</i><span><small>Stack rules</small><b>{result.calculation.stack_adjustment_total}</b></span>
      <i>=</i><span><small>Adjusted score</small><b>{result.calculation.adjusted_score}</b></span>
      <i>→</i><span className="reasoning-points"><small>Fibonacci</small><b>{result.points}</b></span>
    </div>
    <p className="reasoning-formula-text">{reasoning.formula}</p>

    <h4>Where the base score comes from</h4>
    <div className="reasoning-groups">{reasoning.group_contributions.map(group =>
      <div key={group.group} className={`reasoning-group group-${group.group}`}>
        <span>{group.label}</span><b>{group.subtotal}<small> / {group.maximum}</small></b>
        <em>{group.factor_count} factors</em>
      </div>)}</div>

    <h4>Strongest evidence contributors</h4>
    <ol className="contributor-list">{reasoning.top_contributors.map(item =>
      <li key={item.factor}>
        <span className={`factor-score s${item.score}`}>{item.score}</span>
        <div><b>{item.label}</b><small>{item.reason}</small></div>
        <em>{item.provenance === 'model' ? 'model scored' : 'inferred'}</em>
      </li>)}</ol>

    <div className="reasoning-columns">
      <div><h4>Adjustments that changed the result</h4>
        {reasoning.applied_adjustments.length > 0
          ? <ul className="reason-list">{reasoning.applied_adjustments.map(step =>
              <li key={step.rule}><b>+{step.delta}</b><span>{step.label}</span><code>{step.reference}</code></li>)}</ul>
          : <p className="empty-reason">No adjustment rule fired; the result comes directly from the factor subtotal.</p>}
      </div>
      <div><h4>Decision path</h4>
        <ul className="reason-list">{reasoning.gate_path.map(check =>
          <li key={check.rule} className={check.passed ? 'passed' : 'failed'}>
            <b>{check.passed ? 'Pass' : 'Stop'}</b><span>{check.detail}</span><code>{check.reference}</code>
          </li>)}</ul>
      </div>
    </div>

    <div className="sensitivity-callout"><TrendingDown size={18} /><div>
      <b>Lower-band sensitivity</b><p>{reasoning.band_sensitivity.explanation}</p>
    </div></div>
    <div className="sensitivity-grid">{reasoning.factor_sensitivity.map(item =>
      <div key={item.factor} className={item.changes_outcome ? 'changes' : ''}>
        <b>{item.label}</b>
        <span>{item.current_score} → {item.trial_score}</span>
        <small>Adjusted {item.adjusted_score} · {item.points} points · {item.recommendation.replaceAll('_', ' ')}</small>
        {item.changes_outcome && <em>Changes outcome</em>}
      </div>)}</div>
    <p className="confidence-basis"><ShieldCheck size={15} /><span><b>Confidence basis:</b> {reasoning.confidence_basis}</span></p>
  </div>
}

function SuggestionsPanel({ result }: { result: EstimateResult }) {
  return <section className="suggestions-panel">
    <header><Lightbulb size={19} /><div><b>Recommended next actions</b><small>Prioritized from the scorecard and failed gates—not generated point arithmetic</small></div><span>{result.suggestions.length}</span></header>
    <div className="suggestion-grid">{result.suggestions.map(item =>
      <article key={item.id} className={`suggestion suggestion-${item.priority}`}>
        <div className="suggestion-meta"><span>{item.priority}</span><em>{item.category}</em></div>
        <h3>{item.title}</h3>
        <p>{item.action}</p>
        <dl><div><dt>Why now</dt><dd>{item.why}</dd></div><div><dt>Expected outcome</dt><dd>{item.expected_outcome}</dd></div></dl>
        <details><summary>Evidence used</summary><ul>{item.evidence.map(evidence => <li key={evidence}>{evidence}</li>)}</ul></details>
      </article>)}</div>
  </section>
}

export function EstimateCodeScreen({ onHome }: { onHome: () => void }) {
  const [source, setSource] = useState<'manual' | 'upload' | 'jira'>('manual')
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
  const abortRef = useRef<AbortController | undefined>(undefined)

  useEffect(() => { api.estimateConfig().then(setConfig).catch(cause => setError(cause.message)) }, [])
  const setField = <K extends keyof Story>(field: K, value: Story[K]) => setStory(current => ({ ...current, [field]: value }))
  function setCriterion(index: number, value: string) {
    setField('acceptance_criteria', story.acceptance_criteria.map((item, i) => i === index ? value : item))
  }

  function begin() {
    setLoading(true); setError(''); setResult(undefined); setResults([]); setStepsDone([])
    setRunEvents([]); setStatus('Starting local estimation…'); abortRef.current = new AbortController()
  }
  async function estimateOne(next: Story) {
    begin()
    try {
      await api.estimate(
        { ...next, acceptance_criteria: next.acceptance_criteria.filter(Boolean), stack },
        (event, data) => {
          if (event === 'agent_event') setRunEvents(current => [...current, data as AgentEvent])
          if (event === 'status') setStatus(data.message)
          if (event === 'node') setStepsDone(current => [...new Set([...current, data.node])])
          if (event === 'result') { setResult(data); setStatus('Estimate complete') }
          if (event === 'error') throw new Error(data.message)
        }, abortRef.current?.signal)
    } catch (cause) { if ((cause as Error).name !== 'AbortError') setError((cause as Error).message) }
    finally { setLoading(false) }
  }
  async function estimateMany(items: Story[]) {
    if (!items.length) return setError('Select at least one story.')
    begin()
    try {
      await api.estimateBatch(items.map(item => ({ ...item, stack })), (event, data) => {
        if (event === 'agent_event') setRunEvents(current => [...current, data as AgentEvent])
        if (event === 'status') setStatus(data.message)
        if (event === 'item_started') { setStatus(`Estimating ${data.title}`); setStepsDone([]) }
        if (event === 'item_node') setStepsDone(current => [...new Set([...current, data.node])])
        if (event === 'item_result') setResults(current => [...current, data.result])
        if (event === 'item_error' || event === 'error') setError(data.message)
      }, abortRef.current?.signal)
    } catch (cause) { if ((cause as Error).name !== 'AbortError') setError((cause as Error).message) }
    finally { setLoading(false); setStatus('Batch complete') }
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

  const verdict = result ? RECOMMENDATIONS[result.recommendation] : undefined
  const fibonacci = config?.framework.fibonacci ?? [3, 5, 8, 13, 21, 34]

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
          <h1>A point is only useful when<br />everyone understands <em>why.</em></h1>
          <p>Score 16 calibrated factors, apply your stack's known costs, and let fixed arithmetic
            reach the number. Every adjustment is shown, so anyone can replay the maths by hand.</p>
        </div>
        <div className="method-card">
          <Target />
          <div>
            <b>How it works</b>
            <ol>
              <li>Score 16 factors, 1–5</li>
              <li>Calibrate for your stack</li>
              <li>Apply fixed adjustments</li>
              <li>Map, then check the gates</li>
            </ol>
            {config && <small className="method-version">Framework v{config.framework.version}</small>}
          </div>
        </div>
      </section>

      <nav className="source-tabs">
        {([
          { id: 'jira', label: 'From Jira', Icon: PanelsTopLeft },
          { id: 'manual', label: 'Manual entry', Icon: Keyboard },
          { id: 'upload', label: 'Upload Excel / CSV', Icon: FileSpreadsheet },
        ] as const).map(item =>
          <button key={item.id} className={source === item.id ? 'active' : ''} onClick={() => setSource(item.id)}>
            <item.Icon />{item.label}
          </button>)}
      </nav>

      {error && <div className="estimate-error"><AlertTriangle />{error}</div>}

      <StackPanel stack={stack} config={config} onChange={setStack} />

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
          {steps.map((step, index) => {
            const done = stepsDone.includes(step)
            const current = loading && index === stepsDone.length
            return <div className={done ? 'done' : current ? 'current' : ''} key={step}>
              {done ? <Check /> : current ? <LoaderCircle className="spin" /> : <i />}{labels[step]}
            </div>
          })}
        </section>
      </div>

      {/* While the CPU model works, the evidence panel is the only thing telling the user
          what has actually happened. It appears as soon as the run starts and stays put. */}
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

      {result && verdict && <article className="estimate-result">
        <EvidencePanel events={runEvents} compact title="Estimation evidence" />

        <div className={`verdict verdict-${verdict.tone}`}>
          {result.recommendation === 'proceed' ? <ShieldCheck /> : <AlertTriangle />}
          <b>{verdict.label}</b>
          <span>{result.recommendation_detail}</span>
        </div>

        <div className="result-hero">
          <div className="points">
            <span>STORY POINTS</span>
            <strong>{result.points}</strong>
            <em className={`confidence-${result.confidence.toLowerCase()}`}>{result.confidence} confidence</em>
          </div>
          <div>
            <span className="eyebrow">{result.story.key ? `${result.story.key} · ` : ''}{result.story.title}</span>
            <h2>{result.tldr}</h2>
            <p>{result.plain_language_why}</p>
            <div className="hero-meta">
              <span><Layers size={13} />{result.stack.frontend_label} / {result.stack.backend_label}</span>
              <span><Sigma size={13} />Adjusted {result.calculation.adjusted_score} · band {result.calculation.band}</span>
              <span><Target size={13} />{result.stack.maturity_name} · team {result.stack.team_experience}/5</span>
            </div>
          </div>
        </div>

        <div className="fib-scale">
          {fibonacci.map(point => <span className={point === result.points ? 'selected' : ''} key={point}>{point}</span>)}
        </div>

        <div className="result-buttons">
          <button className="download-result" onClick={download}><Download /> JSON</button>
          {config?.jira_write_enabled && result.story.source === 'jira' &&
            <button className="jira-write" onClick={writePoints}>Write {result.points} to Jira</button>}
        </div>

        <Detail title="Detailed estimation reasoning" open>
          <p className="detail-lede"><BrainCircuit size={17} />Replayable rationale from story evidence and deterministic framework rules. Private chain-of-thought is neither requested nor shown.</p>
          <DetailedReasoningPanel result={result} />
        </Detail>

        <SuggestionsPanel result={result} />

        <Detail title="The calculation, step by step">
          <p className="detail-lede"><Sigma size={17} />{result.evidence.determinism}</p>
          <CalculationLedger calculation={result.calculation} />
        </Detail>

        <Detail title="16-factor scorecard" count={result.scorecard.length}>
          <p className="detail-lede">
            <b>What drives this:</b> {result.drivers.join(' · ')}. {result.drivers_explanation}
          </p>
          <p className="provenance-note">
            {result.evidence.scoring_provenance.model_scored} of {result.scorecard.length} factors were
            scored by the local model; {result.evidence.scoring_provenance.heuristic_filled} were inferred
            from the story text and are marked <em>inferred</em>.
          </p>
          <div className="factor-grid">{result.scorecard.map(item => <ScoreRow key={item.factor} item={item} />)}</div>
        </Detail>

        <Detail title="Spike and split gates" count={result.evidence.policy_checks.filter(check => !check.passed).length}>
          <p className="detail-lede">Each gate is evaluated on every run. A failed gate overrides the number.</p>
          <GateList checks={result.evidence.policy_checks} />
        </Detail>

        {result.risk_flags.length > 0 && <Detail title="Risk flags" count={result.risk_flags.length}>
          <ul className="flag-list">{result.risk_flags.map(flag => <li key={`${flag.source}-${flag.label}`}>
            {flag.score !== null && <span className={`factor-score s${flag.score}`}>{flag.score}</span>}
            {flag.score === null && <span className="factor-score stack"><CircleSlash size={12} /></span>}
            <b>{flag.label}</b><small>{flag.detail}</small>
          </li>)}</ul>
        </Detail>}

        <Detail title="Calibration anchors">
          <p className="detail-lede"><Anchor size={17} />{result.anchor_comparison}</p>
          <ul className="anchor-list">{result.anchors_considered.map(anchor =>
            <li key={`${anchor.stack}-${anchor.title}`}><b>{anchor.points}</b><span>{anchor.title}</span><em>{anchor.stack}</em></li>)}</ul>
        </Detail>

        <Detail title="Effort envelope">
          <div className="effort-grid">
            {[['Frontend', result.effort.frontend], ['Backend', result.effort.backend],
              ['Data', result.effort.data], ['Assurance', result.effort.assurance]]
              .map(([label, value]) => <div key={label}><span>{label}</span><b>{value}</b></div>)}
          </div>
          <p>Person-days: <b>{result.effort.person_days.optimistic}</b> optimistic ·
            <b> {result.effort.person_days.likely}</b> likely ·
            <b> {result.effort.person_days.pessimistic}</b> pessimistic</p>
        </Detail>

        {result.hidden_tasks.length > 0 && <Detail title="Hidden sub-tasks" count={result.hidden_tasks.length}>
          <ul>{result.hidden_tasks.map(item => <li key={item.task}><b>{item.task}</b> — {item.weight}</li>)}</ul>
        </Detail>}

        <Detail title="Risks and assumptions">
          <ul>{result.risks.map(item => <li key={item.risk}><b>{item.risk}</b> — {item.mitigation_or_assumption}</li>)}</ul>
          <p className="detail-lede">Assumptions</p>
          <ul>{result.assumptions.map(item => <li key={item}>{item}</li>)}</ul>
        </Detail>

        {result.spike_definition && <Detail title="Proposed spike">
          <p className="detail-lede"><FlaskConical size={17} /><b>{result.spike_definition.title}</b></p>
          <p>{result.spike_definition.objective}</p>
          <p>Timebox: <b>{result.spike_definition.timebox}</b></p>
          <ul>{result.spike_definition.success_criteria.map(item => <li key={item}>{item}</li>)}</ul>
          <p>Deliverable: {result.spike_definition.deliverable}</p>
        </Detail>}

        <Detail title="Split recommendation">
          <p className="detail-lede"><GitBranch size={17} />{result.split_recommendation.rationale}</p>
          <ol>{result.split_recommendation.proposed_stories.map(item => <li key={item}>{item}</li>)}</ol>
        </Detail>

        <Detail title="Provenance">
          <p className="detail-lede">Context the model was given, and how its own guess compares.</p>
          <ul className="anchor-list">{result.evidence.context_manifest.map(item =>
            <li key={item.id}>
              <b>{item.characters}</b>
              <span>{item.label}{item.truncated ? ' (truncated to fit the budget)' : ''}</span>
              <em>{item.trusted ? 'trusted' : 'untrusted'}</em>
            </li>)}</ul>
          <p className="provenance-note">
            {result.evidence.model_cross_check.agreement === 'not_offered'
              ? 'The model did not offer its own point value.'
              : `The model's own guess was ${result.evidence.model_cross_check.model_points} points; the framework calculated ${result.evidence.model_cross_check.calculated_points}. They ${result.evidence.model_cross_check.agreement === 'agrees' ? 'agree' : 'diverge'}.`}
            {' '}{result.evidence.model_cross_check.note}
          </p>
        </Detail>
      </article>}
    </main>
  </div>
}
