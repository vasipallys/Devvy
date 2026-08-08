import { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity, AlertTriangle, ArrowLeft, Check, ChevronRight, Circle, Code2, Eye, FileCode2,
  FolderOpen, History, LoaderCircle, Send, ShieldCheck, Sparkles, Square, Wrench,
} from 'lucide-react'
import { api, attachToJob } from './api'
import { DockPane, DockToggle, useDock } from './Dock'
import { narrate } from './evidenceNarration'
import { SystemStatusChip } from './SystemStatusChip'
import { Tooltip } from './Tooltip'
import { isJobActive } from './types'
import type {
  AgentEvent, JobDetail, SmartCodePreview, SmartCodeRequest, WorkspaceInfo,
} from './types'

/**
 * Smart Code as a conversation, with the machinery beside it.
 *
 * The form asked for eight fields before it would do anything, then answered once and stopped.
 * But building a change is not one question: the first attempt reveals what the objective left
 * out, a check fails, something needs narrowing. That is a conversation, and forcing it through
 * "fill the form again" threw away everything the previous attempt had learned.
 *
 * So each message either starts a run or *corrects the previous one* — the correction carries
 * the last run's specific failures as its brief, which is what makes the loop converge rather
 * than repeat.
 *
 * The screen is three columns, and the split is deliberate. The transcript is the conversation
 * and it scrolls. The right-hand panel is the *run* — pipeline, workspace, files read, files
 * changed — and it does not scroll away, because a CPU-bound run takes minutes and the whole
 * question during those minutes is "what is it doing now". Putting that inline meant a long
 * pasted brief pushed it below the fold and the application looked dead. The left rail is the
 * session: every attempt, in order, with where each one got to.
 *
 * The settings that genuinely constrain safety — which folder, what Devvy may do in it — live
 * in the panel too. They are not conversation; they are the boundary the conversation happens
 * inside, and they should be readable at a glance without being in the way.
 */

const PIPELINE = ['classify', 'retrieve', 'plan', 'code', 'verify', 'critique', 'gate'] as const

const STAGE_WHY: Record<string, string> = {
  classify: 'The request is validated before anything reads your disk — mode, workspace containment, and target file types.',
  retrieve: 'Repository files are read and marked untrusted evidence, so a comment or docstring cannot redirect the change.',
  plan: 'The smallest complete change is planned before any code is written, so the diff stays reviewable.',
  code: 'Whole-file edits are drafted. Nothing is written to disk at this point — this is still a proposal.',
  verify: 'Deterministic structural checks: Python parses, JSON parses, brackets balance. It does not run your tests or build.',
  critique: 'Review findings are recorded against the proposal, including ones that do not block applying it.',
  gate: 'Nothing is written until you approve. Approval needs unchanged files, passing checks, and a single-use token.',
}

function stagesFrom(events: AgentEvent[]): Record<string, string> {
  const stages: Record<string, string> = {}
  for (const event of events) {
    if ((PIPELINE as readonly string[]).includes(event.stage) && event.status !== 'running') {
      stages[event.stage] = event.status
    }
  }
  return stages
}

/** One exchange: what was asked, and the run that answered it. */
interface Turn {
  id: string
  request: string
  jobId?: string
  events: AgentEvent[]
  stages: Record<string, string>
  status: string
  preview?: SmartCodePreview
  error?: string
  running: boolean
  applied?: { applied: { path: string }[]; backup_dir?: string }
  /** True when this turn corrected an earlier one rather than starting fresh. */
  correction: boolean
}

const slashes = (value: string) => value.replaceAll('\\', '/')
const base = (path: string) => path.split(/[\\/]/).pop() || path

/** The last terminal reading a stage published, so the panel can quote real figures. */
function evidenceOf(events: AgentEvent[], stage: string): Record<string, unknown> {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index]
    if (event.stage === stage && event.status !== 'running') {
      return (event.evidence ?? {}) as Record<string, unknown>
    }
  }
  return {}
}

const size = (value: unknown): number =>
  (Array.isArray(value) ? value.length : typeof value === 'number' ? value : 0)

const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []

/**
 * One short line of fact per stage — the figure that stage produced, not a description of what
 * the stage is for. "8 of 34 files" tells you the run is proceeding; "reads the repository"
 * would be true before the run started and is therefore worth nothing while you wait.
 */
function stageFact(turn: Turn, step: string): string {
  const evidence = evidenceOf(turn.events, step)
  switch (step) {
    case 'classify': {
      const mode = typeof evidence.mode === 'string' ? evidence.mode : ''
      const targets = size(evidence.targets)
      return [mode, targets ? `${targets} named file${targets === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ')
    }
    case 'retrieve': {
      const considered = size(evidence.files_considered)
      const included = size(evidence.files_included)
      if (!considered) return 'no source files'
      return `${included} of ${considered} files read`
    }
    case 'plan': {
      const files = size(evidence.files)
      return files ? `${files} file${files === 1 ? '' : 's'} planned` : ''
    }
    case 'code': {
      const edits = size(evidence.edits) || turn.preview?.edits?.length || 0
      return edits ? `${edits} file${edits === 1 ? '' : 's'} drafted` : ''
    }
    case 'verify': {
      const checks = turn.preview?.verification ?? []
      if (!checks.length) return ''
      const passed = checks.filter(item => item.passed).length
      return `${passed} of ${checks.length} passed`
    }
    case 'critique': {
      const findings = size(evidence.findings) || turn.preview?.findings?.length || 0
      return findings ? `${findings} finding${findings === 1 ? '' : 's'}` : 'no findings'
    }
    case 'gate': {
      if (turn.applied) return 'applied'
      if (!turn.preview) return ''
      return turn.preview.can_apply ? 'awaiting your approval' : 'blocked by failed checks'
    }
    default:
      return ''
  }
}

/**
 * The pipeline as a vertical spine.
 *
 * Seven stages read as a list, not as a row of chips: a list has room for each stage's actual
 * finding beside its name, and the connector between marks makes the order — and how far the
 * run has got along it — legible without reading a single word.
 */
function Pipeline({ turn }: { turn: Turn }) {
  const done = Object.keys(turn.stages).length
  return <ol className="pipe">
    {PIPELINE.map((step, index) => {
      const state = turn.stages[step]
      const running = turn.running && index === done
      const status = state === 'failed' ? 'failed' : state ? 'done' : running ? 'running' : 'pending'
      const fact = stageFact(turn, step)
      return <li key={step} className={`pipe-step ${status}`}>
        <span className="pipe-mark" aria-hidden>
          {status === 'failed' ? <AlertTriangle size={11} />
            : status === 'done' ? <Check size={11} />
            : status === 'running' ? <LoaderCircle className="spin" size={11} />
            : <Circle size={7} />}
        </span>
        <Tooltip label={step} detail={STAGE_WHY[step]}>
          <span className="pipe-body">
            <b>{step}</b>
            <small>{fact || (status === 'pending' ? 'not started' : status)}</small>
          </span>
        </Tooltip>
      </li>
    })}
  </ol>
}

/** A pasted brief can be a hundred lines. Shown whole it pushes the answer below the fold, so
 *  the screen shows only what the user already knows — their own message. */
function Prompt({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  const lines = text.split('\n')
  const long = lines.length > 8 || text.length > 600
  if (!long) return <p>{text}</p>
  return <>
    <p className={open ? '' : 'clamped'}>{open ? text : lines.slice(0, 8).join('\n')}</p>
    <button className="chat-prompt-toggle" onClick={() => setOpen(value => !value)}>
      {open ? 'Show less' : `Show full prompt (${lines.length} lines)`}
    </button>
  </>
}

/** The pipeline talking as it works: one line per stage, in the order they happened. */
function Narration({ events }: { events: AgentEvent[] }) {
  const lines = events
    .map(event => ({ event, text: narrate(event) }))
    .filter(item => item.text)
  if (!lines.length) return null
  return <div className="chat-narration">
    {lines.map((item, index) => <p key={`${item.event.stage}-${index}`} className={item.event.status}>
      <span className="chat-narration-stage">{item.event.stage.replaceAll('_', ' ')}</span>
      {item.text}
    </p>)}
  </div>
}

function RunResult({ turn, onApply, applying, openDiff, onOpenDiff }: {
  turn: Turn
  onApply: () => void
  applying: boolean
  openDiff: string
  onOpenDiff: (path: string) => void
}) {
  const preview = turn.preview
  if (!preview) return null
  const failures = (preview.verification ?? []).filter(item => !item.passed)
  const ready = (preview.edits ?? []).filter(
    edit => !failures.some(item => slashes(item.path).endsWith(slashes(edit.path))),
  ).length
  const diffs = Object.keys(preview.diffs ?? {})
  const active = diffs.includes(openDiff) ? openDiff : diffs[0] || ''

  return <div className="chat-result">
    <p className="chat-summary">{preview.summary}</p>

    {preview.plan?.length > 0 && <ol className="chat-plan">
      {preview.plan.map(step => <li key={step}>{step}</li>)}
    </ol>}

    {preview.deploy_steps && preview.deploy_steps.length > 0 && <details className="chat-deploy">
      <summary>How to deploy this ({preview.deploy_steps.length} steps)</summary>
      <ol>{preview.deploy_steps.map(step => <li key={step}>{step}</li>)}</ol>
    </details>}

    {preview.findings?.length > 0 && <ul className="chat-findings">
      {preview.findings.map((item, index) => <li key={index} className={item.severity}>
        <b>{item.severity}</b>{item.path ? ` ${item.path}: ` : ' '}{item.message}
      </li>)}
    </ul>}

    {diffs.length > 0 && <div className="chat-diff">
      <nav>{diffs.map(path => <button key={path} className={active === path ? 'active' : ''}
        onClick={() => onOpenDiff(path)}>{base(path)}</button>)}</nav>
      <pre>{preview.diffs[active] || 'No textual changes.'}</pre>
    </div>}

    {turn.applied
      ? <p className="chat-applied"><Check size={14} /> Applied {turn.applied.applied.length} file(s).
        {' '}Backups: {turn.applied.backup_dir || 'new files only'}</p>
      : preview.edits?.length > 0 && <div className="chat-gate">
        <button className="chat-apply" disabled={!preview.can_apply || applying} onClick={onApply}>
          <ShieldCheck size={15} />{applying ? 'Applying…' : 'Approve & apply'}
        </button>
        {/* A disabled button explains itself in text: browsers suppress pointer events on a
            disabled control, so hover help never reaches the person who needs it. */}
        {!preview.can_apply && <p className="chat-blocked">
          <AlertTriangle size={13} />
          <span>
            <b>{failures.length} of {preview.edits.length} file(s) failed their checks</b>
            {failures.slice(0, 3).map(item => <small key={item.path}>
              {base(item.path)}: {item.detail}
            </small>)}
            <small className="chat-blocked-next">
              {ready > 0
                ? `The other ${ready} passed. Nothing is written until every file parses — `
                  + 'applying half a change is harder to undo than asking again below.'
                : 'Devvy will not write code it could not parse.'}
              {' '}Reply with what to change and Devvy will retry with these errors as the brief.
            </small>
          </span>
        </p>}
      </div>}
  </div>
}

/** A collapsible group in the run panel. Open by default — a panel that hides its contents to
 *  look tidy makes the reader click through three sections to find out what is happening. */
function Group({ title, count, children, defaultOpen = true }: {
  title: string
  count?: number | string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return <section className={`panel-group ${open ? 'open' : ''}`}>
    <button className="panel-group-head" onClick={() => setOpen(value => !value)} aria-expanded={open}>
      <ChevronRight size={13} />
      <b>{title}</b>
      {count !== undefined && count !== '' && <span className="panel-count">{count}</span>}
    </button>
    {open && <div className="panel-group-body">{children}</div>}
  </section>
}

export function SmartCodeChat({ onHome, initialJobId }: {
  onHome: () => void
  initialJobId?: string
}) {
  const [workspace, setWorkspace] = useState('')
  /** What the folder turned out to be — the basis for the mode, instead of asking the user. */
  const [folder, setFolder] = useState<WorkspaceInfo>()
  const [inspecting, setInspecting] = useState(false)
  /** The only choice that changes what Devvy may do: propose changes, or read only. */
  const [permission, setPermission] = useState<'propose' | 'read-only'>('propose')
  const [risk, setRisk] = useState<SmartCodeRequest['risk']>('medium')
  const [targets, setTargets] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState('')
  // Both panels are the reader's to size, move and put away; the run panel starts open because
  // losing sight of the run is the failure this layout exists to prevent.
  const sessionDock = useDock('smart-code.session', {
    side: 'left', width: 238, min: 180, max: 400, overlayBelow: 1100,
  })
  const runDock = useDock('smart-code.run', {
    side: 'right', width: 346, min: 280, max: 620, overlayBelow: 820,
  })
  /** Which file's diff the transcript is showing — the run panel drives it too. */
  const [openDiff, setOpenDiff] = useState('')
  const endRef = useRef<HTMLDivElement>(null)
  const answerRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | undefined>(undefined)
  const turnRefs = useRef<Record<string, HTMLElement | null>>({})

  // Block body: a concise arrow returns its expression, and React calls an effect's return
  // value as the cleanup function.
  //
  // Follows the newest *answer*, not the end of the document. Scrolling to the bottom after a
  // long prompt lands on empty space below it, which reads as nothing having happened.
  useEffect(() => {
    const target = answerRef.current ?? endRef.current
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }, [turns.length])

  // The mode is derived, not asked. An empty folder can only be generated into; a folder with
  // source in it is being modified; read-only permission means review whatever it holds. The
  // user was being asked to describe something the application can see for itself, and getting
  // it wrong decides whether existing files are read as context at all.
  const mode: SmartCodeRequest['mode'] =
    permission === 'read-only' ? 'review' : folder?.empty ? 'generate' : 'modify'

  // Inspect the folder as it is typed, so the detected mode appears before anything is sent.
  useEffect(() => {
    const path = workspace.trim()
    if (!path) { setFolder(undefined); return }
    let disposed = false
    setInspecting(true)
    const timer = window.setTimeout(() => {
      api.inspectWorkspace(path)
        .then(info => { if (!disposed) setFolder(info) })
        .catch(() => { if (!disposed) setFolder(undefined) })
        .finally(() => { if (!disposed) setInspecting(false) })
    }, 400)
    return () => { disposed = true; window.clearTimeout(timer) }
  }, [workspace])

  const busy = turns.some(turn => turn.running)
  /** The run in flight, if any — the one the panel reports on. */
  const active = turns.find(turn => turn.running)
  /** The last finished run, which the next message corrects rather than replaces. */
  const lastFinished = [...turns].reverse().find(turn => !turn.running && turn.preview)
  /** What the panel describes: the live run, or the most recent one once it settles. */
  const current = active ?? turns[turns.length - 1]

  /** Files this run actually read — the evidence the answer is grounded in. */
  const sources = useMemo(() => {
    if (!current) return [] as string[]
    const evidence = evidenceOf(current.events, 'retrieve')
    const read = strings(evidence.files_read)
    return read.length ? read : strings(evidence.files_included)
  }, [current])

  /** Files this run proposes to write, each carrying its own check result. */
  const changes = useMemo(() => {
    const preview = current?.preview
    if (!preview) return [] as { path: string; action: string; passed: boolean; detail: string }[]
    const checks = preview.verification ?? []
    return (preview.edits ?? []).map(edit => {
      const check = checks.find(item => slashes(item.path).endsWith(slashes(edit.path)))
      return {
        path: edit.path,
        action: edit.action,
        passed: check ? check.passed : true,
        detail: check?.detail ?? edit.reason ?? '',
      }
    })
  }, [current])

  function patch(id: string, update: Partial<Turn>) {
    setTurns(current => current.map(turn => (turn.id === id ? { ...turn, ...update } : turn)))
  }

  async function follow(turnId: string, jobId: string) {
    abortRef.current = new AbortController()
    try {
      await attachToJob(jobId, {
        onSnapshot: job => patch(turnId, {
          events: job.events,
          stages: { ...stagesFrom(job.events) },
          status: job.progress || 'Working…',
        }),
        onEvent: event => setTurns(current => current.map(turn => turn.id === turnId
          ? {
            ...turn,
            events: [...turn.events, event],
            stages: { ...turn.stages, ...stagesFrom([event]) },
          }
          : turn)),
        onStatus: message => patch(turnId, { status: message }),
        onDone: (status, result, failure) => {
          if (status === 'succeeded' && result?.preview) {
            patch(turnId, {
              preview: result.preview,
              stages: { ...stagesFrom([]), ...(result.stages || {}) },
              status: result.preview.can_apply ? 'Verified — review the diff' : 'Checks failed',
              running: false,
            })
          } else {
            patch(turnId, {
              error: failure || `The run ${status}.`,
              status: `Run ${status}`,
              running: false,
            })
          }
        },
      }, abortRef.current.signal)
    } catch (cause) {
      if ((cause as Error).name !== 'AbortError') {
        patch(turnId, { error: (cause as Error).message, running: false })
      }
    } finally {
      patch(turnId, { running: false })
    }
  }

  function restore(job: JobDetail) {
    const request = (job.request ?? {}) as Partial<SmartCodeRequest>
    if (request.workspace_root) setWorkspace(request.workspace_root)
    // The mode is derived from the folder now, so a restored run only needs its permission
    // restored: a review can never have produced a writable change.
    if (request.mode === 'review') setPermission('read-only')
    setTurns([{
      id: job.id,
      request: request.objective || job.title,
      jobId: job.id,
      events: job.events,
      stages: { ...stagesFrom(job.events), ...((job.result?.stages as Record<string, string>) || {}) },
      status: 'Showing a completed run',
      preview: job.result?.preview as SmartCodePreview | undefined,
      error: job.error ?? undefined,
      running: false,
      correction: false,
    }])
  }

  // Open on a specific run when asked; otherwise rejoin one of ours still going.
  useEffect(() => {
    let disposed = false
    const load = async () => {
      try {
        if (initialJobId) {
          const job = await api.job(initialJobId)
          if (disposed) return
          if (isJobActive(job.status)) {
            const request = (job.request ?? {}) as Partial<SmartCodeRequest>
            if (request.workspace_root) setWorkspace(request.workspace_root)
            if (request.mode === 'review') setPermission('read-only')
            setTurns([{
              id: job.id, request: request.objective || job.title, jobId: job.id,
              events: job.events, stages: stagesFrom(job.events), status: job.progress || 'Working…',
              running: true, correction: false,
            }])
            follow(job.id, job.id)
            return
          }
          return restore(job)
        }
        const { jobs } = await api.jobs()
        const live = jobs.find(job => job.kind === 'smart-code' && isJobActive(job.status))
        if (live && !disposed) {
          const job = await api.job(live.id)
          const request = (job.request ?? {}) as Partial<SmartCodeRequest>
          if (request.workspace_root) setWorkspace(request.workspace_root)
          if (request.mode === 'review') setPermission('read-only')
          setTurns([{
            id: job.id, request: request.objective || job.title, jobId: job.id,
            events: job.events, stages: stagesFrom(job.events), status: job.progress || 'Working…',
            running: true, correction: false,
          }])
          follow(job.id, job.id)
        }
      } catch (cause) {
        // Missing and not-yours are the same 404 by design.
        if (!disposed) setError((cause as Error).message)
      }
    }
    load()
    return () => { disposed = true }
  }, [initialJobId])

  async function send() {
    const message = input.trim()
    if (!message || busy) return
    if (!workspace.trim()) {
      // The folder field lives in the run panel, so an error about it is useless if the panel
      // is the thing you have put away.
      runDock.setCollapsed(false)
      return setError('Choose the workspace folder Devvy may read and edit.')
    }
    setError('')
    setInput('')

    // A follow-up corrects the previous run rather than starting from nothing. That is what
    // makes the loop converge: the model is told what failed last time, not just asked again.
    const correcting = Boolean(lastFinished?.jobId)
    const id = crypto.randomUUID()
    setTurns(current => [...current, {
      id, request: message, events: [], stages: {}, status: 'Queued',
      running: true, correction: correcting,
    }])

    try {
      const { job_id } = correcting
        ? await api.fixSmartCode(lastFinished!.jobId!, message)
        : await api.submitSmartCode({
          objective: message,
          workspace_root: workspace.trim(),
          mode,
          target_paths: targets.split(/[\n,]/).map(item => item.trim()).filter(Boolean),
          acceptance_criteria: [],
          risk,
        })
      patch(id, { jobId: job_id })
      await follow(id, job_id)
    } catch (cause) {
      patch(id, { error: (cause as Error).message, running: false, status: 'Could not start' })
    }
  }

  async function apply(turn: Turn) {
    const preview = turn.preview
    if (!preview?.can_apply) return
    if (!window.confirm(`Apply ${preview.edits.length} verified file change(s) to ${workspace}?`)) return
    setApplying(true)
    setError('')
    try {
      const applied = await api.smartCodeApply(preview.preview_token)
      patch(turn.id, { applied, status: 'Changes applied' })
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setApplying(false)
    }
  }

  async function stop() {
    const live = turns.find(turn => turn.running && turn.jobId)
    if (!live?.jobId) return
    try { await api.cancelJob(live.jobId) } catch { /* already finished */ }
    abortRef.current?.abort()
  }

  /** Panel → transcript: choosing a file scrolls to the run that produced it and opens its diff. */
  function showDiff(path: string) {
    setOpenDiff(path)
    if (current) turnRefs.current[current.id]?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return <div className="product-screen code-screen">
    <header className="product-header">
      <button onClick={onHome}><ArrowLeft size={17} /> Home</button>
      <div className="product-brand">
        <Code2 /><span><b>Smart Code</b><small>Repository-aware engineering agent</small></span>
      </div>
      <div className="code-header-tools">
        {/* The run panel can be closed or collapsed, so the header keeps a live reading of the
            run. The pipeline must never be nowhere on the screen; that is the whole failure
            this layout was built to fix. */}
        {runDock.collapsed && current && <Tooltip
          label={`${Object.keys(current.stages).length} of ${PIPELINE.length} stages done`}
          detail="Open the run panel to see each stage, what it found, and the files it proposes."
        >
          <button className="code-progress" onClick={() => runDock.setCollapsed(false)}>
            {current.running
              ? <LoaderCircle className="spin" size={12} />
              : current.error ? <AlertTriangle size={12} /> : <Check size={12} />}
            <span>{Object.keys(current.stages).length}/{PIPELINE.length}</span>
            <small>{current.status}</small>
          </button>
        </Tooltip>}
        <SystemStatusChip />
        <DockToggle dock={sessionDock} label="the session list" />
        <DockToggle dock={runDock} label="the run panel" />
      </div>
    </header>

    <div className="dock-shell">
      {/* The session: every attempt in order, and how far each got. A correction is not a new
          question, so this shows the thread rather than a list of unrelated runs. */}
      <DockPane dock={sessionDock} label="Session" icon={<History size={14} />}>
        {!turns.length && <p className="rail-empty">Attempts appear here as you go.</p>}
        <ol className="rail-list">
          {turns.map((turn, index) => <li key={turn.id}>
            <button
              className={`rail-item ${turn.id === current?.id ? 'active' : ''}`}
              onClick={() => turnRefs.current[turn.id]?.scrollIntoView({ behavior: 'smooth', block: 'start' })}
            >
              <span className={`rail-dot ${turn.running ? 'running' : turn.error || turn.preview?.can_apply === false ? 'failed' : 'ok'}`} />
              <span>
                <b>{turn.correction ? `Correction ${index}` : 'First attempt'}</b>
                <small>{turn.request.split('\n')[0].slice(0, 60) || 'untitled'}</small>
              </span>
            </button>
          </li>)}
        </ol>
      </DockPane>

      <div className="dock-center">
        <main className="chat-transcript">
          {!turns.length && <div className="chat-welcome">
            <Sparkles size={34} />
            <h1>Describe the change you want.</h1>
            <p>
              Devvy retrieves the relevant files, plans the smallest change, writes each file,
              verifies it, and waits for your approval before writing anything to disk. Reply to
              keep going — each message carries the last run's failures, so the change converges
              instead of starting over.
            </p>
          </div>}

          {turns.map(turn => <article
            key={turn.id}
            className="chat-turn"
            ref={element => { turnRefs.current[turn.id] = element }}
          >
            <div className="chat-message user">
              {turn.correction && <span className="chat-correction"><Wrench size={11} /> correction</span>}
              <Prompt text={turn.request} />
            </div>

            <div
              className="chat-message agent"
              ref={turn.id === turns[turns.length - 1]?.id ? answerRef : undefined}
            >
              <div className="chat-agent-head">
                <span className="chat-avatar"><Code2 size={14} /></span>
                <b>Devvy</b>
                <span className={`chat-status ${turn.running ? 'running' : turn.error ? 'failed' : ''}`}>
                  {turn.running && <LoaderCircle className="spin" size={11} />}{turn.status}
                </span>
              </div>

              <Narration events={turn.events} />

              {turn.error && <p className="chat-error"><AlertTriangle size={14} /> {turn.error}</p>}
              <RunResult
                turn={turn}
                applying={applying}
                onApply={() => apply(turn)}
                openDiff={openDiff}
                onOpenDiff={setOpenDiff}
              />
            </div>
          </article>)}
          <div ref={endRef} />
        </main>

        <footer className="chat-composer">
          {error && <div className="product-error">{error}</div>}
          {lastFinished && !busy && <p className="chat-continue">
            <History size={12} /> Your next message continues this change — Devvy will carry the last
            run's failures into it rather than starting over.
          </p>}
          <div className="chat-input">
            <textarea
              value={input}
              onChange={event => setInput(event.target.value)}
              placeholder={lastFinished ? 'What should change?' : 'Describe the feature, fix, or refactor…'}
              rows={1}
              aria-label="Describe the change"
              onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send() }
              }}
            />
            {busy
              ? <button className="chat-send" onClick={stop} aria-label="Stop this run">
                <Square size={14} />
              </button>
              : <button className="chat-send" onClick={send} disabled={!input.trim()} aria-label="Send">
                <Send size={16} />
              </button>}
          </div>
        </footer>
      </div>

      {/* The run, held still. A generation on a CPU model takes minutes, and for all of those
          minutes the only question is what it is doing — so this never scrolls away. */}
      <DockPane dock={runDock} label="Run" icon={<Activity size={14} />}>
        <div className="panel-head">
          <span className={`panel-state ${current?.running ? 'running' : current?.error ? 'failed' : current ? 'done' : ''}`}>
            {current?.running
              ? <><LoaderCircle className="spin" size={12} /> Running</>
              : current?.error ? <><AlertTriangle size={12} /> Failed</>
              : current ? <><Check size={12} /> Finished</>
              : <>Idle</>}
          </span>
          {busy && <button className="panel-stop" onClick={stop}><Square size={11} /> Stop</button>}
        </div>
        {current && <p className="panel-status">{current.status}</p>}

        <div className="panel-groups">
          <Group title="Pipeline" count={current ? `${Object.keys(current.stages).length}/${PIPELINE.length}` : ''}>
            {current
              ? <Pipeline turn={current} />
              : <p className="panel-hint">The seven stages appear here once a run starts.</p>}
          </Group>

          <Group title="Workspace">
            <label className="panel-field">Folder
              <input
                value={workspace}
                onChange={event => setWorkspace(event.target.value)}
                placeholder="D:\projects\my-app"
                spellCheck={false}
              />
            </label>
            <p className={`panel-detect ${folder?.exists === false ? 'bad' : ''}`}>
              {inspecting
                ? <><LoaderCircle className="spin" size={11} /> reading folder…</>
                : folder?.exists === false
                  ? <><AlertTriangle size={11} /> {folder.reason || 'Folder not found'}</>
                  : folder?.exists
                    ? <><Sparkles size={11} /> {mode} · {folder.source_files
                      ? `${folder.source_files} source files` : 'empty folder'}
                      {folder.languages?.length ? ` · ${folder.languages.join(' ')}` : ''}</>
                    : <>The absolute path to the repository. Nothing outside it is read or written.</>}
            </p>

            <Tooltip
              label={permission === 'propose' ? 'Devvy may propose changes' : 'Devvy may only read'}
              detail={permission === 'propose'
                ? 'Devvy writes nothing without your explicit approval on the diff. This permission '
                  + 'lets it prepare one; it never grants the write itself.'
                : 'Read-only. Devvy reviews the code and reports findings, and no run can produce a '
                  + 'file to write at all.'}
            >
              <button
                className={`panel-toggle ${permission === 'read-only' ? 'muted' : ''}`}
                onClick={() => setPermission(value => (value === 'propose' ? 'read-only' : 'propose'))}
              >
                {permission === 'propose' ? <ShieldCheck size={13} /> : <Eye size={13} />}
                <span>{permission === 'propose' ? 'Propose changes' : 'Read only'}</span>
              </button>
            </Tooltip>

            <label className="panel-field">Only these files <small>optional</small>
              <input value={targets} onChange={event => setTargets(event.target.value)}
                placeholder="src/app.py, src/models.py" spellCheck={false} />
            </label>
            <label className="panel-field">Risk tier
              <select value={risk} onChange={event => setRisk(event.target.value as SmartCodeRequest['risk'])}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </label>
          </Group>

          <Group title="Sources read" count={sources.length || ''} defaultOpen={false}>
            {sources.length
              ? <ul className="panel-files">
                {sources.map(path => <li key={path}>
                  <FolderOpen size={12} />
                  <Tooltip label={path} detail="Read as untrusted evidence — instructions written
                    inside your own code cannot redirect the change.">
                    <span>{base(path)}</span>
                  </Tooltip>
                </li>)}
              </ul>
              : <p className="panel-hint">Nothing read yet. An empty folder is grounded in your
                objective alone.</p>}
          </Group>

          <Group title="Proposed changes" count={changes.length || ''}>
            {changes.length
              ? <ul className="panel-files">
                {changes.map(file => <li key={file.path} className={file.passed ? '' : 'bad'}>
                  <FileCode2 size={12} />
                  <button className="panel-file" onClick={() => showDiff(file.path)}>
                    <span>{base(file.path)}</span>
                    <small>{file.action}{file.passed ? '' : ` · ${file.detail}`}</small>
                  </button>
                  {file.passed ? <Check size={12} className="ok" /> : <AlertTriangle size={12} className="bad" />}
                </li>)}
              </ul>
              : <p className="panel-hint">Nothing is proposed yet. Files appear here as they are
                drafted, each with its own check result.</p>}

            {current?.preview && !current.applied && current.preview.edits.length > 0
              && <button
                className="panel-apply"
                disabled={!current.preview.can_apply || applying}
                onClick={() => apply(current)}
              >
                <ShieldCheck size={14} />{applying ? 'Applying…' : 'Approve & apply'}
              </button>}
            {current?.applied && <p className="panel-applied">
              <Check size={12} /> Applied to disk. Backups: {current.applied.backup_dir || 'new files only'}
            </p>}
          </Group>
        </div>
      </DockPane>
    </div>
  </div>
}
