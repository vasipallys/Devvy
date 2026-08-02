import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, Anchor, ArrowLeft, BrainCircuit, Check, ChevronDown, Download, FileSpreadsheet, GitBranch, Keyboard, LoaderCircle, PanelsTopLeft, Plus, Target, Trash2 } from 'lucide-react'
import { api } from './api'
import { EvidencePanel } from './EvidencePanel'
import { SystemStatusChip } from './SystemStatusChip'
import type { AgentEvent, EstimateResult, Story } from './types'

const steps = ['score_parameters', 'identify_drivers', 'compare_to_anchors', 'derive_points', 'spike_split_branch', 'write_plain_language_reasoning', 'detect_hidden_tasks', 'assess_risks', 'recommend_split']
const labels: Record<string, string> = { score_parameters: 'Score parameters', identify_drivers: 'Identify drivers', compare_to_anchors: 'Compare anchors', derive_points: 'Derive points', spike_split_branch: 'Spike / split check', write_plain_language_reasoning: 'Write plain-language why', detect_hidden_tasks: 'Find hidden work', assess_risks: 'Assess risks', recommend_split: 'Recommend split' }
const emptyStory: Story = { title: '', user_story: '', acceptance_criteria: [''], technical_breakdown: '', source: 'manual' }

function Detail({ title, children }: { title: string; children: React.ReactNode }) {
  return <details className="estimate-detail"><summary>{title}<ChevronDown size={17}/></summary><div>{children}</div></details>
}

export function EstimateCodeScreen({ onHome }: { onHome: () => void }) {
  const [source, setSource] = useState<'manual' | 'upload' | 'jira'>('manual')
  const [story, setStory] = useState<Story>(emptyStory)
  const [config, setConfig] = useState<any>()
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
  function setCriterion(index: number, value: string) { setField('acceptance_criteria', story.acceptance_criteria.map((item, i) => i === index ? value : item)) }

  function begin() { setLoading(true); setError(''); setResult(undefined); setResults([]); setStepsDone([]); setRunEvents([]); setStatus('Starting local estimation…'); abortRef.current = new AbortController() }
  async function estimateOne(next: Story) {
    begin()
    try {
      await api.estimate({ ...next, acceptance_criteria: next.acceptance_criteria.filter(Boolean) }, (event, data) => {
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
      await api.estimateBatch(items, (event, data) => {
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
      return [{ title, user_story: mapping.user_story ? String(row[mapping.user_story] || '') : '', acceptance_criteria: criteria, technical_breakdown: mapping.technical_breakdown ? String(row[mapping.technical_breakdown] || '') : '', source: 'upload' as const }]
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
    const link = document.createElement('a'); link.href = url; link.download = `${result.story.key || 'estimate'}.json`; link.click(); URL.revokeObjectURL(url)
  }
  async function writePoints() {
    if (!result?.story.key || !window.confirm(`Write ${result.points} points to ${result.story.key} in Jira?`)) return
    try { await api.writeJiraPoints(result.story.key, result.points); setStatus(`${result.story.key} updated in Jira`) }
    catch (cause) { setError((cause as Error).message) }
  }

  return <div className="product-screen estimate-screen">
    <header className="estimate-header"><button onClick={onHome}><ArrowLeft size={17}/> Home</button><div className="product-brand"><BrainCircuit/><span><b>Estimate Code</b><small>Evidence-led estimation</small></span></div><SystemStatusChip/></header>
    <main className="estimate-main">
      <section className="estimate-hero"><div><span className="eyebrow">DEFENSIBLE BY DESIGN</span><h1>A point is only useful when<br/>everyone understands <em>why.</em></h1><p>Score the real work, calibrate it against fixed anchors, and share a conclusion anyone can defend.</p></div><div className="method-card"><Target/><div><b>How it works</b><ol><li>Score 12 delivery factors</li><li>Find the true drivers</li><li>Compare fixed anchors</li><li>Conclude, never guess</li></ol></div></div></section>
      <nav className="source-tabs">{([{ id: 'jira', label: 'From Jira', Icon: PanelsTopLeft }, { id: 'manual', label: 'Manual entry', Icon: Keyboard }, { id: 'upload', label: 'Upload Excel / CSV', Icon: FileSpreadsheet }] as const).map(item => <button key={item.id} className={source === item.id ? 'active' : ''} onClick={() => setSource(item.id)}><item.Icon/>{item.label}</button>)}</nav>
      {error && <div className="estimate-error"><AlertTriangle/>{error}</div>}
      <div className="estimate-workspace">
        <section className="story-card">
          {source === 'manual' && <form onSubmit={event => { event.preventDefault(); estimateOne(story) }}><span className="eyebrow">ONE STORY</span><h2>Describe the work</h2><label>Title<input required value={story.title} onChange={event => setField('title', event.target.value)} placeholder="What outcome are we delivering?"/></label><label>User story<textarea required rows={4} value={story.user_story} onChange={event => setField('user_story', event.target.value)} placeholder="As a…, I want…, so that…"/></label><fieldset><legend>Acceptance criteria</legend>{story.acceptance_criteria.map((item, index) => <div className="criterion" key={index}><span>{index + 1}</span><input aria-label={`Acceptance criterion ${index + 1}`} value={item} onChange={event => setCriterion(index, event.target.value)} placeholder="Observable condition of success"/><button type="button" aria-label={`Remove acceptance criterion ${index + 1}`} disabled={story.acceptance_criteria.length === 1} onClick={() => setField('acceptance_criteria', story.acceptance_criteria.filter((_, i) => i !== index))}><Trash2/></button></div>)}<button type="button" className="text-action" onClick={() => setField('acceptance_criteria', [...story.acceptance_criteria, ''])}><Plus/> Add criterion</button></fieldset><label>Technical breakdown <span>optional</span><textarea rows={3} value={story.technical_breakdown} onChange={event => setField('technical_breakdown', event.target.value)} placeholder="Known services, components, migrations, or constraints"/></label><button className="estimate-action" disabled={loading}>{loading ? <LoaderCircle className="spin"/> : <BrainCircuit/>}Build justified estimate</button></form>}
          {source === 'upload' && <div className="upload-pane"><span className="eyebrow">BATCH ESTIMATION</span><h2>Import stories</h2><label className="upload-drop"><FileSpreadsheet/><b>Choose Excel or CSV</b><small>Up to 100 stories · 15 MB</small><input type="file" accept=".csv,.xlsx" onChange={event => parseFile(event.target.files?.[0])}/></label>{upload && <><p>{upload.row_count} row(s) found. Confirm column mapping:</p><div className="mapping-grid">{Object.keys(mapping).map(field => <label key={field}>{field.replaceAll('_', ' ')}<select value={mapping[field] || ''} onChange={event => setMapping(current => ({ ...current, [field]: event.target.value || null }))}><option value="">Not mapped</option>{upload.columns.map((column: string) => <option key={column}>{column}</option>)}</select></label>)}</div><button className="estimate-action" disabled={loading || !mapping.title} onClick={() => estimateMany(uploadStories())}>Estimate {uploadStories().length} stories</button></>}</div>}
          {source === 'jira' && <div className="jira-pane"><span className="eyebrow">JIRA SOURCE</span><h2>Choose backlog stories</h2>{!config?.jira_configured ? <div className="setup-note">Set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN in Devvy’s .env to enable this source.</div> : <><div className="jira-search"><input value={jiraProject} onChange={event => setJiraProject(event.target.value)} placeholder="Project key"/><input value={jiraQuery} onChange={event => setJiraQuery(event.target.value)} placeholder="Optional text filter"/><button onClick={fetchJira}>Load</button></div><div className="jira-list">{jiraStories.map(item => <label key={item.key}><input type="checkbox" checked={selectedJira.includes(item.key!)} onChange={event => setSelectedJira(current => event.target.checked ? [...current, item.key!] : current.filter(key => key !== item.key))}/><b>{item.key}</b><span>{item.title}</span></label>)}</div><button className="estimate-action" disabled={!selectedJira.length || loading} onClick={() => estimateMany(jiraStories.filter(item => selectedJira.includes(item.key!)))}>Estimate selected</button></>}</div>}
        </section>
        <section className={`estimate-pipeline ${loading ? 'active' : ''}`}><span className="eyebrow">LIVE REASONING</span><h2>{status || 'Your evidence pipeline'}</h2>{steps.map((step, index) => { const done = stepsDone.includes(step); const current = loading && index === stepsDone.length; return <div className={done ? 'done' : current ? 'current' : ''} key={step}>{done ? <Check/> : current ? <LoaderCircle className="spin"/> : <i/>}{labels[step]}</div> })}</section>
      </div>
      {results.length > 0 && <section className="batch-results"><h2>Batch results</h2>{results.map(item => <button key={item.story.key || item.story.title} onClick={() => setResult(item)}><strong>{item.points}</strong><span><b>{item.story.title}</b><small>{item.tldr}</small></span></button>)}</section>}
      {result && <article className="estimate-result">
        <EvidencePanel events={runEvents} compact title="Estimation evidence"/>
        {(result.spike_recommended || result.split_recommendation.split_recommended) && <div className="recommendation"><AlertTriangle/><b>{result.split_recommendation.split_recommended ? 'SPLIT' : 'SPIKE'} recommended</b><span>{result.split_recommendation.split_recommended ? result.split_recommendation.rationale : result.spike_reason}</span></div>}
        <div className="result-hero"><div className="points"><span>STORY POINTS</span><strong>{result.points}</strong></div><div><span className="eyebrow">{result.story.key ? `${result.story.key} · ` : ''}{result.story.title}</span><h2>{result.tldr}</h2><p>{result.plain_language_why}</p></div></div>
        <div className="fib-scale">{[1,2,3,5,8,13].map(point => <span className={point === result.points ? 'selected' : ''} key={point}>{point}</span>)}</div><div className="result-buttons"><button className="download-result" onClick={download}><Download/> JSON</button>{config?.jira_write_enabled && result.story.source === 'jira' && <button className="jira-write" onClick={writePoints}>Write {result.points} to Jira</button>}</div>
        <Detail title="Scorecard and drivers"><p><b>What drives this:</b> {result.drivers_explanation}</p><div className="score-grid">{result.scorecard.map(item => <div key={item.parameter}><span className={item.score.toLowerCase()}>{item.score}</span><b>{item.parameter.replaceAll('_',' ')}</b><small>{item.reason}</small></div>)}</div></Detail>
        <Detail title="Calibration anchor comparison"><p><Anchor size={17}/>{result.anchor_comparison}</p><p>{result.points_derivation}</p></Detail>
        <Detail title={`Hidden sub-tasks (${result.hidden_tasks.length})`}><ul>{result.hidden_tasks.map(item => <li key={item.task}><b>{item.task}</b> — {item.weight}</li>)}</ul></Detail>
        <Detail title="Risks and assumptions"><ul>{result.risks.map(item => <li key={item.risk}><b>{item.risk}</b> — {item.mitigation_or_assumption}</li>)}</ul></Detail>
        <Detail title="Split recommendation"><p><GitBranch size={17}/>{result.split_recommendation.rationale}</p><ol>{result.split_recommendation.proposed_stories.map(item => <li key={item}>{item}</li>)}</ol></Detail>
      </article>}
    </main>
  </div>
}
