import { useRef, useState } from 'react'
import { ArrowLeft, Check, Code2, FileCode2, FolderOpen, LoaderCircle, Play, ShieldCheck, Sparkles } from 'lucide-react'
import { api } from './api'
import { EvidencePanel } from './EvidencePanel'
import { SystemStatusChip } from './SystemStatusChip'
import type { AgentEvent, SmartCodePreview, SmartCodeRequest } from './types'

const pipeline = ['classify', 'retrieve', 'plan', 'code', 'verify', 'critique', 'gate']

export function SmartCodeScreen({ onHome }: { onHome: () => void }) {
  const [workspace, setWorkspace] = useState('')
  const [targets, setTargets] = useState<string[]>([])
  const [objective, setObjective] = useState('')
  const [acceptance, setAcceptance] = useState('')
  const [mode, setMode] = useState<SmartCodeRequest['mode']>('modify')
  const [language, setLanguage] = useState('')
  const [framework, setFramework] = useState('')
  const [risk, setRisk] = useState<SmartCodeRequest['risk']>('medium')
  const [stages, setStages] = useState<string[]>([])
  const [status, setStatus] = useState('Ready')
  const [preview, setPreview] = useState<SmartCodePreview>()
  const [activeDiff, setActiveDiff] = useState('')
  const [running, setRunning] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>()
  const [error, setError] = useState('')
  const [runEvents, setRunEvents] = useState<AgentEvent[]>([])
  const abortRef = useRef<AbortController | undefined>(undefined)

  async function pickFolder() {
    if (!window.desktop) return setError('Folder selection is available in the desktop app. Paste an absolute path below.')
    const path = await window.desktop.pickFolder(); if (path) setWorkspace(path)
  }
  async function pickFiles() {
    if (!window.desktop) return setError('File selection is available in the desktop app.')
    const paths = await window.desktop.pickFiles(); if (paths.length) setTargets(paths)
  }
  async function run() {
    if (!workspace.trim() || !objective.trim()) return setError('Choose a workspace and describe the change.')
    setError(''); setPreview(undefined); setResult(undefined); setStages([]); setRunEvents([]); setRunning(true)
    abortRef.current = new AbortController()
    const payload: SmartCodeRequest = {
      objective: objective.trim(), workspace_root: workspace.trim(), mode, target_paths: targets,
      acceptance_criteria: acceptance.split('\n').map(x => x.trim()).filter(Boolean),
      language: language || undefined, framework: framework || undefined, risk,
    }
    try {
      await api.smartCodePreview(payload, (event, data) => {
        if (event === 'agent_event') setRunEvents(current => [...current, data as AgentEvent])
        if (event === 'started') { setStages(['classify']); setStatus(data.message) }
        if (event === 'status') setStatus(data.message)
        if (event === 'stage') setStages(current => [...new Set([...current, data.stage])])
        if (event === 'result') {
          setPreview(data); setStatus(data.can_apply ? 'Verified — review the diff' : mode === 'review' ? 'Review complete' : 'Verification failed')
          setActiveDiff(Object.keys(data.diffs || {})[0] || '')
        }
        if (event === 'error') throw new Error(data.message)
      }, abortRef.current.signal)
    } catch (cause) {
      if ((cause as Error).name !== 'AbortError') setError((cause as Error).message)
    } finally { setRunning(false) }
  }
  async function apply() {
    if (!preview?.can_apply || !window.confirm(`Apply ${preview.edits.length} verified file change(s) to ${workspace}?`)) return
    setApplying(true); setError('')
    try {
      const applied = await api.smartCodeApply(preview.preview_token)
      setResult(applied); setStatus('Changes applied and evidence saved')
      setRunEvents(current => [...current, {
        run_id: current[0]?.run_id || preview.preview_token, stage: 'apply', status: 'completed',
        label: 'Approved changes written atomically', elapsed_ms: current.at(-1)?.elapsed_ms || 0,
        evidence: { files: applied.applied.length, backup: applied.backup_dir || 'new files only' },
      }])
    }
    catch (cause) { setError((cause as Error).message) }
    finally { setApplying(false) }
  }

  return <div className="product-screen smart-screen">
    <header className="product-header"><button onClick={onHome}><ArrowLeft size={17}/> Home</button><div className="product-brand"><Code2/><span><b>Smart Code</b><small>Repository-aware engineering agent</small></span></div><SystemStatusChip/></header>
    <div className="smart-layout">
      <aside className="smart-controls">
        <label>Mode<select value={mode} onChange={event => setMode(event.target.value as SmartCodeRequest['mode'])}><option value="generate">Generate</option><option value="modify">Modify</option><option value="review">Review</option></select></label>
        <label>Workspace folder<div className="input-action"><input value={workspace} onChange={event => setWorkspace(event.target.value)} placeholder="D:\\projects\\my-app"/><button title="Choose folder" onClick={pickFolder}><FolderOpen size={17}/></button></div></label>
        <label>Target files <span>optional</span><button className="outline-action" onClick={pickFiles}><FileCode2 size={16}/> {targets.length ? `${targets.length} selected` : 'Select files'}</button></label>
        {targets.length > 0 && <div className="target-list">{targets.map(path => <span key={path} title={path}>{path.split(/[\\/]/).pop()}<button aria-label={`Remove ${path}`} onClick={() => setTargets(items => items.filter(item => item !== path))}>×</button></span>)}</div>}
        <label>Objective<textarea rows={6} value={objective} onChange={event => setObjective(event.target.value)} placeholder="Describe the feature, fix, refactor, or review focus…"/></label>
        <div className="field-pair"><label>Language<input value={language} onChange={event => setLanguage(event.target.value)} placeholder="infer"/></label><label>Framework<input value={framework} onChange={event => setFramework(event.target.value)} placeholder="infer"/></label></div>
        <label>Acceptance criteria <span>one per line</span><textarea rows={4} value={acceptance} onChange={event => setAcceptance(event.target.value)} placeholder="Behavior is covered by tests&#10;Existing API remains compatible"/></label>
        <label>Risk tier<select value={risk} onChange={event => setRisk(event.target.value as SmartCodeRequest['risk'])}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
        <button className="primary-action" disabled={running || !workspace.trim() || !objective.trim()} onClick={run}>{running ? <LoaderCircle className="spin"/> : <Play/>}{running ? 'Building preview…' : mode === 'review' ? 'Run review' : 'Build verified preview'}</button>
      </aside>
      <main className="smart-workspace">
        <section className="pipeline-panel"><div className="panel-title"><b>PIPELINE</b><span>{status}</span></div><div className="smart-pipeline">{pipeline.map((step, index) => <div key={step} className={stages.includes(step) ? 'done' : running && index === stages.length ? 'running' : ''}>{stages.includes(step) ? <Check/> : running && index === stages.length ? <LoaderCircle className="spin"/> : <i/>}<b>{step}</b><small>{step === 'gate' ? 'human approval' : step === 'verify' ? 'syntax · policy' : 'agent stage'}</small></div>)}</div></section>
        <EvidencePanel events={runEvents} compact title="Engineering evidence"/>
        {error && <div className="product-error">{error}</div>}
        {!preview && !error && <section className="smart-empty"><Sparkles size={40}/><h1>Production changes start with evidence.</h1><p>Select a workspace and describe the outcome. Smart Code retrieves relevant files, plans the smallest change, generates complete code, verifies it, and waits for your approval before writing.</p></section>}
        {preview && <div className="smart-results">
          <section className="result-summary"><div><span className="eyebrow">RESULT</span><h2>{preview.summary}</h2></div>{preview.edits.length > 0 && <button className="apply-action" disabled={!preview.can_apply || applying || !!result} onClick={apply}><ShieldCheck size={17}/>{result ? 'Applied' : applying ? 'Applying…' : 'Approve & apply'}</button>}</section>
          <section className="plan-strip">{preview.plan.map((item, index) => <div key={item}><span>{index + 1}</span>{item}</div>)}</section>
          {preview.findings.length > 0 && <section className="finding-list">{preview.findings.map((item, index) => <div key={index} className={item.severity}><b>{item.severity}</b><span>{item.path && `${item.path}: `}{item.message}</span></div>)}</section>}
          {preview.edits.length > 0 && <section className="diff-panel"><nav>{Object.keys(preview.diffs).map(path => <button className={activeDiff === path ? 'active' : ''} key={path} onClick={() => setActiveDiff(path)}>{path}</button>)}</nav><pre>{preview.diffs[activeDiff] || 'No textual changes.'}</pre></section>}
          <section className="verification-row">{preview.verification.map(item => <div className={item.passed ? 'pass' : 'fail'} key={item.path}>{item.passed ? <Check/> : '×'}<span><b>{item.path.split(/[\\/]/).pop()}</b><small>{item.detail}</small></span></div>)}</section>
          {result && <div className="applied-banner"><Check/> Applied {result.applied.length} file(s). Backups: {result.backup_dir || 'new files only'}</div>}
        </div>}
      </main>
    </div>
  </div>
}
