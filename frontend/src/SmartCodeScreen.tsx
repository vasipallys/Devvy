import { useEffect, useRef, useState } from 'react'
import { AlertTriangle, ArrowLeft, Check, Code2, FileCode2, FolderOpen, History, LoaderCircle, Play, ShieldCheck, Sparkles } from 'lucide-react'
import { api, attachToJob } from './api'
import { EvidencePanel } from './EvidencePanel'
import { SystemStatusChip } from './SystemStatusChip'
import { Tooltip } from './Tooltip'
import { isJobActive } from './types'
import type { AgentEvent, JobDetail, SmartCodePreview, SmartCodeRequest } from './types'

const pipeline = ['classify', 'retrieve', 'plan', 'code', 'verify', 'critique', 'gate']

/** Why each checkpoint exists, for hover. */
const STAGE_WHY: Record<string, string> = {
  classify: 'The request is validated before anything reads your disk — mode, workspace containment, and target file types.',
  retrieve: 'Repository files are read and marked untrusted evidence, so a comment or docstring cannot redirect the change.',
  plan: 'The smallest complete change is planned before any code is written, so the diff stays reviewable.',
  code: 'Whole-file edits are drafted. Nothing is written to disk at this point — this is still a proposal.',
  verify: 'Deterministic structural checks: Python parses, JSON parses, brackets balance. It does not run your tests or build.',
  critique: 'Review findings are recorded against the proposal, including ones that do not block applying it.',
  gate: 'Nothing is written until you approve. Approval needs unchanged files, passing checks, and a single-use token.',
}

/** Derive checkpoint statuses from agent events, so a reattaching client rebuilds the
 *  pipeline from the snapshot exactly as a client that watched from the start. */
function stagesFrom(events: AgentEvent[]): Record<string, string> {
  const stages: Record<string, string> = {}
  for (const event of events) {
    if (pipeline.includes(event.stage) && event.status !== 'running') {
      stages[event.stage] = event.status
    }
  }
  return stages
}

export function SmartCodeScreen({ onHome, initialJobId }: {
  onHome: () => void
  /** Open this screen on one specific run, from Activity or a shared link. */
  initialJobId?: string
}) {
  const [workspace, setWorkspace] = useState('')
  const [targets, setTargets] = useState<string[]>([])
  const [objective, setObjective] = useState('')
  const [acceptance, setAcceptance] = useState('')
  const [mode, setMode] = useState<SmartCodeRequest['mode']>('modify')
  const [language, setLanguage] = useState('')
  const [framework, setFramework] = useState('')
  const [risk, setRisk] = useState<SmartCodeRequest['risk']>('medium')
  // Stage → status, so a failed structural check is not painted as a completed step.
  const [stages, setStages] = useState<Record<string, string>>({})
  const [status, setStatus] = useState('Ready')
  const [preview, setPreview] = useState<SmartCodePreview>()
  const [activeDiff, setActiveDiff] = useState('')
  const [running, setRunning] = useState(false)
  const [applying, setApplying] = useState(false)
  const [result, setResult] = useState<any>()
  const [error, setError] = useState('')
  const [runEvents, setRunEvents] = useState<AgentEvent[]>([])
  const [targetDraft, setTargetDraft] = useState('')
  const [jobId, setJobId] = useState<string>()
  /** Set when the screen is showing a finished run rebuilt from its stored result rather
   *  than a live one. Applying is bounded by a server-side token that this screen cannot
   *  see, so a restored preview must say what it is instead of looking live. */
  const [restoredAt, setRestoredAt] = useState<string>()
  const abortRef = useRef<AbortController | undefined>(undefined)

  // Open on a specific run when asked; otherwise rejoin whatever of ours is still going.
  //
  // Opening "the workspace" was not enough. With several runs it attached to an arbitrary
  // one, and for a finished run it showed an empty form — the result was reachable only from
  // Activity, which is the screen you had just chosen to leave.
  useEffect(() => {
    let disposed = false
    const load = async () => {
      if (initialJobId) {
        try {
          const job = await api.job(initialJobId)
          if (disposed) return
          if (isJobActive(job.status)) return follow(job.id)
          return restore(job)
        } catch (cause) {
          // A job that is missing, or belongs to somebody else, is indistinguishable by
          // design: the server answers 404 either way, and so does this screen.
          if (!disposed) setError((cause as Error).message)
          return
        }
      }
      try {
        const { jobs } = await api.jobs()
        const live = jobs.find(job => job.kind === 'smart-code' && isJobActive(job.status))
        if (live && !disposed) follow(live.id)
      } catch { /* nothing to rejoin */ }
    }
    load()
    return () => { disposed = true }
  }, [initialJobId])

  /** Rebuild the screen from a finished run's stored result. */
  function restore(job: JobDetail) {
    setRunEvents(job.events)
    setStages({ classify: 'completed', ...stagesFrom(job.events), ...(job.result?.stages || {}) })
    setRestoredAt(job.completed_at ?? undefined)
    const request = (job.request ?? {}) as Partial<SmartCodeRequest>
    // Restoring the inputs too, so "run it again" is one click rather than retyping the
    // objective from the job title.
    if (request.workspace_root) setWorkspace(request.workspace_root)
    if (request.objective) setObjective(request.objective)
    if (request.mode) setMode(request.mode)
    if (request.target_paths) setTargets(request.target_paths)
    if (request.acceptance_criteria) setAcceptance(request.acceptance_criteria.join('\n'))
    if (request.language) setLanguage(request.language)
    if (request.framework) setFramework(request.framework)
    if (request.risk) setRisk(request.risk)
    const data = job.result?.preview as SmartCodePreview | undefined
    if (data) {
      setPreview(data)
      setActiveDiff(Object.keys(data.diffs || {})[0] || '')
      setStatus('Showing a completed run')
    } else if (job.error) {
      setError(job.error)
      setStatus(`This run ${job.status}`)
    } else {
      setStatus(`This run ${job.status} without producing a preview`)
    }
  }

  // A browser cannot read a real filesystem path from a file input, so targets are entered
  // as text. They may be absolute, or relative to the workspace root — the API resolves both
  // and rejects anything that escapes the workspace.
  function addTarget() {
    const value = targetDraft.trim()
    if (!value) return
    if (targets.includes(value)) return setTargetDraft('')
    setTargets(current => [...current, value])
    setTargetDraft('')
  }
  /** Attach to a preview job; also used to rejoin one already running on the server. */
  async function follow(id: string) {
    setJobId(id)
    setRunning(true)
    abortRef.current = new AbortController()
    try {
      await attachToJob(id, {
        onSnapshot: job => {
          setRunEvents(job.events)
          setStages({ classify: 'completed', ...stagesFrom(job.events) })
          if (job.progress) setStatus(job.progress)
        },
        onEvent: event => {
          setRunEvents(current => [...current, event])
          setStages(current => ({ ...current, ...stagesFrom([event]) }))
        },
        onStatus: message => setStatus(message),
        onDone: (status, result, failure) => {
          if (status === 'succeeded' && result?.preview) {
            const data = result.preview
            setStages(current => ({ ...current, ...(result.stages || {}) }))
            setPreview(data)
            // "Verification failed" was reported whenever the gate was shut, including
            // when the model produced no file at all — naming a check that never ran, on a
            // screen that simultaneously showed every pipeline stage green.
            setStatus(
              data.can_apply ? 'Verified — review the diff'
                : mode === 'review' ? 'Review complete'
                : data.edits.length === 0 ? 'No file produced — nothing to apply'
                : 'Verification failed',
            )
            setActiveDiff(Object.keys(data.diffs || {})[0] || '')
          } else if (status !== 'succeeded') {
            setError(failure || `The preview ${status}.`)
            setStatus(`Preview ${status}`)
          }
        },
      }, abortRef.current.signal)
    } catch (cause) {
      if ((cause as Error).name !== 'AbortError') setError((cause as Error).message)
    } finally { setRunning(false); setJobId(undefined) }
  }

  async function run() {
    if (!workspace.trim() || !objective.trim()) return setError('Choose a workspace and describe the change.')
    setError(''); setPreview(undefined); setResult(undefined); setStages({}); setRunEvents([])
    setRunning(true); setStatus('Queued')
    const payload: SmartCodeRequest = {
      objective: objective.trim(), workspace_root: workspace.trim(), mode, target_paths: targets,
      acceptance_criteria: acceptance.split('\n').map(x => x.trim()).filter(Boolean),
      language: language || undefined, framework: framework || undefined, risk,
    }
    try {
      const { job_id } = await api.submitSmartCode(payload)
      await follow(job_id)
    } catch (cause) { setRunning(false); setError((cause as Error).message) }
  }

  async function cancelRun() {
    if (!jobId) return
    try { await api.cancelJob(jobId) } catch { /* already finished */ }
    abortRef.current?.abort()
  }
  // The specific checks that are keeping the write gate shut, and how much of the run did
  // succeed. "3 of 4 files are ready" and "the run failed" are very different situations, and
  // the gate shutting on one bad file made them look identical.
  const blockingFailures = (preview?.verification ?? []).filter(item => !item.passed)
  // Verification reports absolute paths; an edit carries a workspace-relative one. Comparing
  // them raw silently matches nothing, so every file would look ready.
  const slashes = (value: string) => value.replaceAll('\\', '/')
  const brokenPaths = blockingFailures.map(item => slashes(item.path))
  const readyCount = (preview?.edits ?? []).filter(
    edit => !brokenPaths.some(path => path.endsWith(slashes(edit.path))),
  ).length

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
        <label>Workspace folder<input value={workspace} onChange={event => setWorkspace(event.target.value)} placeholder="D:\projects\my-app" spellCheck={false} autoComplete="off"/><small className="field-hint"><FolderOpen size={13}/> Absolute path to the repository Devvy may read and edit.</small></label>
        <label htmlFor="smart-target">Target files <span>optional</span></label>
        <div className="input-action">
          <input id="smart-target" value={targetDraft} spellCheck={false} autoComplete="off"
            onChange={event => setTargetDraft(event.target.value)}
            onKeyDown={event => { if (event.key === 'Enter') { event.preventDefault(); addTarget() } }}
            placeholder="src/app.py or an absolute path"/>
          <button type="button" title="Add target file" aria-label="Add target file" disabled={!targetDraft.trim()} onClick={addTarget}><FileCode2 size={17}/></button>
        </div>
        <small className="field-hint">Leave empty to let Devvy rank the most relevant files itself.</small>
        {targets.length > 0 && <div className="target-list">{targets.map(path => <span key={path} title={path}>{path.split(/[\\/]/).pop()}<button aria-label={`Remove ${path}`} onClick={() => setTargets(items => items.filter(item => item !== path))}>×</button></span>)}</div>}
        <label>Objective<textarea rows={6} value={objective} onChange={event => setObjective(event.target.value)} placeholder="Describe the feature, fix, refactor, or review focus…"/></label>
        <div className="field-pair"><label>Language<input value={language} onChange={event => setLanguage(event.target.value)} placeholder="infer"/></label><label>Framework<input value={framework} onChange={event => setFramework(event.target.value)} placeholder="infer"/></label></div>
        <label>Acceptance criteria <span>one per line</span><textarea rows={4} value={acceptance} onChange={event => setAcceptance(event.target.value)} placeholder="Behavior is covered by tests&#10;Existing API remains compatible"/></label>
        <label>Risk tier<select value={risk} onChange={event => setRisk(event.target.value as SmartCodeRequest['risk'])}><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
        <button className="primary-action" disabled={running || !workspace.trim() || !objective.trim()} onClick={run}>{running ? <LoaderCircle className="spin"/> : <Play/>}{running ? 'Building preview…' : mode === 'review' ? 'Run review' : 'Build verified preview'}</button>
        {running && <button className="outline-action cancel-run" onClick={cancelRun}>Cancel request</button>}
        {running && <small className="field-hint">This runs on the server. You can close the tab and come back to it in Activity.</small>}
      </aside>
      <main className="smart-workspace">
        <section className="pipeline-panel"><div className="panel-title"><b>PIPELINE</b><span>{status}</span></div><div className="smart-pipeline">{pipeline.map((step, index) => {
          const state = stages[step]
          const active = running && index === Object.keys(stages).length
          return <Tooltip key={step} label={step} detail={STAGE_WHY[step]}>
            <div className={state === 'failed' ? 'failed' : state ? 'done' : active ? 'running' : ''}>
            {state === 'failed' ? <AlertTriangle/> : state ? <Check/> : active ? <LoaderCircle className="spin"/> : <i/>}
            <b>{step}</b><small>{step === 'gate' ? 'human approval' : step === 'verify' ? 'syntax · policy' : 'agent stage'}</small>
          </div></Tooltip>
        })}</div></section>
        <EvidencePanel events={runEvents} compact title="Engineering evidence"/>
        {error && <div className="product-error">{error}</div>}
        {!preview && !error && <section className="smart-empty"><Sparkles size={40}/><h1>Production changes start with evidence.</h1><p>Select a workspace and describe the outcome. Smart Code retrieves relevant files, plans the smallest change, generates complete code, verifies it, and waits for your approval before writing.</p></section>}
        {preview && <div className="smart-results">
          {/* A restored preview looks identical to a live one, and is not: approval needs a
              single-use server-side token that expires and does not survive a restart. Saying
              so up front beats an Approve button that fails for a reason nobody can see. */}
          {restoredAt && <div className="restored-banner" role="status">
            <History size={15}/>
            <span>
              <b>Showing a completed run</b>
              <small>
                Finished {new Date(restoredAt).toLocaleString()}. The diff and evidence below are
                exactly what that run produced. Applying needs a live preview — if approval is
                refused as expired, run it again to get a fresh one.
              </small>
            </span>
          </div>}
          <section className="result-summary">
            <div><span className="eyebrow">RESULT</span><h2>{preview.summary}</h2></div>
            {preview.edits.length > 0 && <div className="apply-column">
              <button className="apply-action" disabled={!preview.can_apply || applying || !!result} onClick={apply}>
                <ShieldCheck size={17}/>{result ? 'Applied' : applying ? 'Applying…' : 'Approve & apply'}
              </button>
              {/* A disabled button explains itself in text, not a tooltip: browsers suppress
                  pointer events on a disabled control, so hover help never reaches the one
                  person who needs it. Blocking the write is correct — leaving the reason on
                  another part of the page is what made it look broken. */}
              {!preview.can_apply && !result && <p className="apply-blocked" role="status">
                <AlertTriangle size={14}/>
                <span>
                  <b>
                    Cannot apply — {blockingFailures.length} of {preview.edits.length} file
                    {preview.edits.length === 1 ? '' : 's'} failed its checks
                  </b>
                  {blockingFailures.map(item => <small key={item.path}>
                    {item.path.split(/[\\/]/).pop()}: {item.detail}
                  </small>)}
                  <small className="apply-blocked-next">
                    {readyCount > 0
                      ? `The other ${readyCount} file${readyCount === 1 ? '' : 's'} passed and `
                        + 'are shown in the diff. Nothing is written until every file parses — '
                        + 'applying half a change would leave the workspace broken in a way '
                        + 'that is harder to undo than re-running.'
                      : 'Devvy will not write code it could not parse.'}
                    {' '}Re-run, or narrow the objective so the model has room to finish.
                  </small>
                </span>
              </p>}
            </div>}
          </section>
          {preview.plan_supplied === false
            ? <section className="plan-strip"><div className="plan-absent">
                <span>—</span>The model did not return a plan for this objective.
              </div></section>
            : <section className="plan-strip">{preview.plan.map((item, index) => <div key={item}><span>{index + 1}</span>{item}</div>)}</section>}
          {preview.findings.length > 0 && <section className="finding-list">{preview.findings.map((item, index) => <div key={index} className={item.severity}><b>{item.severity}</b><span>{item.path && `${item.path}: `}{item.message}</span></div>)}</section>}
          {preview.edits.length > 0 && <section className="diff-panel"><nav>{Object.keys(preview.diffs).map(path => <button className={activeDiff === path ? 'active' : ''} key={path} onClick={() => setActiveDiff(path)}>{path}</button>)}</nav><pre>{preview.diffs[activeDiff] || 'No textual changes.'}</pre></section>}
          <section className="verification-row">{preview.verification.map(item => <div className={item.passed ? 'pass' : 'fail'} key={item.path}>{item.passed ? <Check/> : '×'}<span><b>{item.path.split(/[\\/]/).pop()}</b><small>{item.detail}</small></span></div>)}</section>
          {result && <div className="applied-banner"><Check/> Applied {result.applied.length} file(s). Backups: {result.backup_dir || 'new files only'}</div>}
        </div>}
      </main>
    </div>
  </div>
}
