import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AlertTriangle, ArrowLeft, BrainCircuit, Check, FileSpreadsheet, History, Keyboard,
  Layers, LoaderCircle, PanelsTopLeft, Plus, Target, Trash2,
} from 'lucide-react'
import { STAGE_WHY } from './AgentFlowDiagram'
import { Tooltip } from './Tooltip'
import { api, attachToJob } from './api'
import { narrateStep } from './evidenceNarration'
import { EstimateHistoryPanel } from './EstimateHistoryPanel'
import { EstimateResultView, RECOMMENDATIONS } from './EstimateResultView'
import { TechniquePanel } from './TechniquePanel'
import { TechniquePicker } from './TechniquePicker'
import { SystemStatusChip } from './SystemStatusChip'
import { isJobActive } from './types'
import type {
  AgentEvent, EstimateConfig, EstimateResult, Level, StackProfile, Story, TechniqueId,
} from './types'

/** Steps that only exist when a repository was supplied.
 *
 *  They are held out of the checklist otherwise rather than shown greyed. A stage that can
 *  never complete on this run reads as a stall, and three of them at the top of the list reads
 *  as a broken pipeline — which is exactly the impression a reader forms while waiting on a
 *  CPU model for two minutes. Held out, the counts stay true: "12 of 27" means twelve of the
 *  twenty-seven that apply. */
const REPO_STEPS = new Set(['repo_intelligence', 'repo_answers', 'change_plan'])

/** Steps that only exist for the techniques that seat a squad. T-shirt sizing, affinity
 *  mapping and the bucket system are a single pass, so showing "collect each discipline's
 *  estimate" for them would list a row that can never complete. */
const SQUAD_STEPS = new Set(['technique_squad', 'technique_vote', 'technique_round'])
const SQUAD_TECHNIQUES = new Set(['planning_poker', 'dot_voting'])

const steps = [
  'contract', 'requirements', 'repo_intelligence',
  'normalize', 'readiness', 'assemble_context', 'declare_stack', 'specialist_routing',
  'primary_estimate', 'focus_pass', 'specialist_analysis', 'blind_review', 'disagreement', 'critic', 'arbitration',
  'eagle_conflict', 'eagle_review', 'eagle_debate',
  'score_factors', 'apply_base_adjustments', 'apply_stack_adjustments', 'map_to_fibonacci',
  'evaluate_gates', 'decide', 'repo_answers', 'eagle_validation', 'eagle_reference',
  'change_plan', 'consistency_audit',
  'technique_squad', 'technique_vote', 'technique_round', 'technique',
  'human_review',
]
const labels: Record<string, string> = {
  contract: 'Seal the estimation contract',
  requirements: 'Read the requirements from the story',
  repo_intelligence: 'Read the repository',
  normalize: 'Normalize evidence & create input hash',
  readiness: 'Evaluate story readiness',
  assemble_context: 'Bound the story evidence',
  declare_stack: 'Load stack calibration',
  specialist_routing: 'Route specialist lenses',
  primary_estimate: 'Run primary evidence assessment',
  focus_pass: 'Ask the model the simpler question',
  specialist_analysis: 'Apply routed specialist lenses',
  blind_review: 'Run independent blind review',
  disagreement: 'Detect material disagreements',
  critic: 'Challenge conflicting claims',
  arbitration: 'Apply resolution policy',
  eagle_conflict: 'Measure independent agreement',
  eagle_review: 'Critic, adversarial & optimistic review',
  eagle_debate: 'Debate the disputed factors',
  score_factors: 'Build final 16-factor scorecard',
  apply_base_adjustments: 'Apply base adjustments',
  apply_stack_adjustments: 'Apply stack adjustments',
  map_to_fibonacci: 'Map to Fibonacci',
  evaluate_gates: 'Evaluate spike & split gates',
  decide: 'Reach framework recommendation',
  repo_answers: 'Replace inferred scores with repository facts',
  eagle_validation: 'Enforce deterministic validation & spike gate',
  eagle_reference: 'Anchor against historical stories',
  change_plan: 'Name what would actually change',
  consistency_audit: 'Replay and audit consistency',
  technique: 'Run the chosen estimation technique',
  technique_squad: 'Seat the squad',
  technique_vote: 'Collect each discipline’s estimate',
  technique_round: 'Re-poll the outliers',
  human_review: 'Hand off for human consensus',
}

/** Service stage → pipeline checkpoints it completes. Mirrors ESTIMATE_NODES on the server;
 *  the job stream carries agent events, and the checklist is derived from them so a
 *  reattaching client reconstructs the same progress from the snapshot alone. */
const NODE_MAP: Record<string, string[]> = {
  contract: ['contract'],
  requirements: ['requirements'],
  repo_intelligence: ['repo_intelligence'],
  repo_answers: ['repo_answers'],
  change_plan: ['change_plan'],
  normalize: ['normalize'],
  readiness: ['readiness'],
  assemble_context: ['assemble_context'],
  declare_stack: ['declare_stack'],
  specialist_routing: ['specialist_routing'],
  primary_estimate: ['primary_estimate'],
  focus_pass: ['focus_pass'],
  specialist_analysis: ['specialist_analysis'],
  blind_review: ['blind_review'],
  disagreement: ['disagreement'],
  critic: ['critic'],
  arbitration: ['arbitration'],
  eagle_conflict: ['eagle_conflict'],
  eagle_review: ['eagle_review'],
  eagle_debate: ['eagle_debate'],
  eagle_validation: ['eagle_validation'],
  eagle_reference: ['eagle_reference'],
  score_factors: ['score_factors'],
  calculate: ['apply_base_adjustments', 'apply_stack_adjustments', 'map_to_fibonacci'],
  policy_gate: ['evaluate_gates', 'decide'],
  consistency_audit: ['consistency_audit'],
  technique: ['technique'],
  technique_squad: ['technique_squad'],
  technique_vote: ['technique_vote'],
  technique_round: ['technique_round'],
  human_review: ['human_review'],
}
/** Why each EAGLE step exists. The rest come from the flow diagram's node definitions, so
 *  the explanation lives in one place and the checklist does not need a second view to reach
 *  it. */
const EAGLE_WHY: Record<string, string> = {
  contract: 'The objective, acceptance criteria, stack and rules are frozen and hashed before '
    + 'anything is scored. Two runs with the same hash were given the same problem — which is '
    + 'the only way to explain why two estimates differ.',
  requirements: 'The story is decomposed into numbered requirements, each quoting the text it '
    + 'came from. It is what turns "the story is unclear" into a named gap a writer can act on '
    + '— and it is the same decomposition code generation works from, so a score can never be '
    + 'made against a requirement nobody wrote down.',
  repo_intelligence: 'The codebase the story lands in is read before anything is scored: '
    + 'declared stack, what is already present, what is absent, and the files this story would '
    + 'touch. What the repository can answer, the model is never asked to guess.',
  repo_answers: 'Where the repository settles a question the story left open, the inferred '
    + 'score is replaced by a fact read from disk. A factor the story never mentions is not '
    + 'automatically unknown — and a score from a file beats a score from silence.',
  change_plan: 'The model is never asked to name a file. It is shown the ranked change surface '
    + 'and asked what changes inside each, and every path is then checked against disk — so the '
    + 'estimate is sized against files that exist rather than files that sounded plausible.',
  focus_pass: 'Sixteen scored objects in one response is where a small model degrades: it holds '
    + 'the shape and loses the content, answering the same number sixteen times. Asked instead '
    + 'which factors the story touches, which are largest and which it left unanswered, it '
    + 'answers with recall — and code turns the reading into scores.',
  eagle_conflict: 'The independent assessments are compared factor by factor. A spread of two '
    + 'or more disputes, and so does an elevated score with no evidence behind it, so a missing '
    + 'answer can never settle quietly on a middling number.',
  eagle_review: 'Three reviewers argue from opposite directions: the critic attacks the '
    + 'estimate, the adversarial pass looks only for reasons it is too low, and the optimistic '
    + 'pass only for complexity counted twice. Neither side can inflate unopposed.',
  eagle_debate: 'Only the disputed factors are re-examined, for a bounded number of rounds. One '
    + 'contested score is not a reason to redo work that was already agreed, and a debate that '
    + 'could run forever would reintroduce the variance blind scoring removed.',
  eagle_validation: 'The objective rules run in code, not in a prompt: sixteen factors, all in '
    + 'range, every elevated score evidenced, adjustments reconciling to the total — then the '
    + 'spike gate, which is allowed to refuse to estimate at all.',
  eagle_reference: 'The estimate is anchored against past stories that were the same shape of '
    + 'work, compared on all sixteen factor scores rather than on shared vocabulary. A weak '
    + 'match is reported as weak instead of being used as an anchor.',
}

/** The five phases a reader can name, and the steps that belong to each.
 *
 *  Twenty-five steps in one column is a 1,900px ribbon that no layout can sit beside without
 *  leaving a hole. Grouped into phases they lay out as a wide, shallow grid instead — which is
 *  also how a person actually holds the pipeline in their head: gather evidence, assess it
 *  independently, reconcile the differences, calculate, decide. */
const PHASES: { id: string; title: string; blurb: string; steps: string[] }[] = [
  {
    id: 'intake', title: 'Evidence',
    blurb: 'Freeze the problem and gather what can be known about it.',
    steps: ['contract', 'requirements', 'repo_intelligence', 'normalize', 'readiness',
      'assemble_context', 'declare_stack',
      'specialist_routing'],
  },
  {
    id: 'independent', title: 'Independent assessment',
    blurb: 'Score the sixteen factors, twice, without either pass seeing the other.',
    steps: ['primary_estimate', 'focus_pass', 'specialist_analysis', 'blind_review'],
  },
  {
    id: 'reconcile', title: 'Challenge',
    blurb: 'Find where the assessments disagree and argue it out on evidence.',
    steps: ['disagreement', 'critic', 'arbitration', 'eagle_conflict', 'eagle_review',
      'eagle_debate'],
  },
  {
    id: 'deterministic', title: 'Calculation',
    blurb: 'Fixed arithmetic and gates, in code — replayable by hand.',
    steps: ['score_factors', 'apply_base_adjustments', 'apply_stack_adjustments',
      'map_to_fibonacci', 'evaluate_gates', 'decide', 'repo_answers', 'eagle_validation',
      'eagle_reference', 'change_plan', 'consistency_audit'],
  },
  {
    id: 'session', title: 'The session',
    blurb: 'The technique you chose, run by a squad that owns different parts of the work.',
    steps: ['technique_squad', 'technique_vote', 'technique_round', 'technique'],
  },
  { id: 'human', title: 'Your decision', blurb: 'The team owns the number.', steps: ['human_review'] },
]

/** The phases, minus any step that cannot apply to this run.
 *
 *  A step that can never complete reads as a stall, and the counts stop meaning anything:
 *  "12 of 30" is only true if all thirty were ever going to happen. */
const phasesFor = (hidden: Set<string>) => PHASES
  .map(phase => ({ ...phase, steps: phase.steps.filter(step => !hidden.has(step)) }))
  .filter(phase => phase.steps.length > 0)

/** Checklist step → the explanation for the stage that produces it. */
const whyForStep = (step: string): string =>
  EAGLE_WHY[step]
  ?? STAGE_WHY[Object.keys(NODE_MAP).find(stage => NODE_MAP[stage].includes(step)) ?? step]
  ?? 'A stage of the deterministic estimation pipeline.'

const nodesFor = (event: AgentEvent): string[] =>
  event.status === 'completed' || event.status === 'validated' ? NODE_MAP[event.stage] ?? [] : []
const derivedSteps = (events: AgentEvent[]): string[] =>
  [...new Set(events.flatMap(nodesFor))]


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
function StackPanel({ stack, config, onChange, workspace, onWorkspaceChange }: {
  stack: StackProfile
  config?: EstimateConfig
  onChange: (next: StackProfile) => void
  workspace: string
  onWorkspaceChange: (value: string) => void
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
      <Tooltip label="Stack penalty total"
        detail={penalties.length
          ? `Added to the scored total before it maps to points: ${penalties.join(', ')}. The same story on a different stack gets a different number, deliberately.`
          : 'Nothing about the declared stack adds to this estimate. A mature framework and an experienced team carry no penalty.'}>
        <span className={`stack-total ${penalties.length ? 'active' : ''}`}>
          {penalties.length ? `+${penalties.reduce((sum, item) => sum + Number(item.match(/\d+/)?.[0] ?? 0), 0)}` : 'No penalty'}
        </span>
      </Tooltip>
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
      <Tooltip label="Framework maturity"
        detail="How settled the framework is. Newer frameworks cost more — fewer answered questions, more churn — so this adds to the score and also caps how many points a single story may carry.">
      <label>
        <span>Framework maturity<b>{stack.maturity_level} · {maturity?.name ?? '—'}</b></span>
        <input type="range" min={1} max={5} value={stack.maturity_level}
          onChange={event => set('maturity_level', Number(event.target.value) as Level)} />
        <small>{maturity ? `${maturity.definition} Caps estimates at ${maturity.cap} points.` : ''}</small>
      </label></Tooltip>
      <Tooltip label="Team experience"
        detail="Experience with this stack specifically, not seniority. A strong team new to a framework still pays the learning curve, and scores of 2 or below add to the estimate.">
      <label>
        <span>Team experience with this stack<b>{stack.team_experience} / 5</b></span>
        <input type="range" min={1} max={5} value={stack.team_experience}
          onChange={event => set('team_experience', Number(event.target.value) as Level)} />
        <small>{stack.team_experience <= 2 ? 'Scores 2 or below add +2 for the learning curve.' : 'No experience penalty applies.'}</small>
      </label></Tooltip>
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

    <label className="workspace-field">
      <Tooltip
        label="Repository to estimate against"
        detail="Optional, and the single biggest improvement available to an estimate. With a
          repository, questions the story left open are answered by the codebase — whether
          migrations exist, whether there are tests beside the files this touches, whether there
          is a CI pipeline — instead of being scored as unbounded. The result also carries a
          verified list of files to change."
      >
        <span>Repository <em>optional</em></span>
      </Tooltip>
      <input
        value={workspace}
        onChange={event => onWorkspaceChange(event.target.value)}
        placeholder="D:\projects\my-app"
        spellCheck={false}
      />
      <small>
        {workspace.trim()
          ? 'The codebase will answer what the story leaves open, and the result will name the '
            + 'files this change touches.'
          : 'Without one, every question the story leaves open is priced as unbounded. Nothing '
            + 'outside this folder is read, and nothing is written.'}
      </small>
    </label>
  </section>
}






export function EstimateCodeScreen({
  onHome, initialView = 'new', initialHistoryId, initialJobId, onViewChange,
}: {
  onHome: () => void
  initialView?: 'new' | 'history'
  initialHistoryId?: string
  /** Open this screen on one specific run, from Activity or a shared link. */
  initialJobId?: string
  onViewChange?: (view: 'new' | 'history', id?: string) => void
}) {
  const [source, setSource] = useState<'manual' | 'upload' | 'jira'>('manual')
  const [view, setView] = useState<'new' | 'history'>(initialView)
  const [story, setStory] = useState<Story>(emptyStory)
  const [stack, setStack] = useState<StackProfile>(defaultStack)
  /** Which technique runs the session. The five differ in precision and in wall clock, so
   *  this is a real choice rather than a label on the same run. */
  const [technique, setTechnique] = useState<TechniqueId>('planning_poker')

  const techniqueChosen = useRef(false)
  const [config, setConfig] = useState<EstimateConfig>()

  // The backend owns the default technique. Applied once, and never over a choice the user
  // has already made — `config` arrives asynchronously, and overwriting a selection when it
  // lands would silently undo a deliberate click.
  useEffect(() => {
    if (config?.default_technique && !techniqueChosen.current) {
      setTechnique(config.default_technique)
    }
  }, [config?.default_technique])
  const [stepsDone, setStepsDone] = useState<string[]>([])
  const [status, setStatus] = useState('')
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<EstimateResult>()
  const [results, setResults] = useState<EstimateResult[]>([])
  const [error, setError] = useState('')
  const [runEvents, setRunEvents] = useState<AgentEvent[]>([])
  /** Optional repository. With one, the codebase answers what the story left open. */
  const [workspace, setWorkspace] = useState('')
  /** Checklist step → the sentence describing what that step found, kept live as events land.
   *  Derived rather than stored, so a client reattaching mid-run rebuilds the same narration
   *  from the snapshot it is handed. */
  const currentStepRef = useRef<HTMLLIElement>(null)
  const feedEndRef = useRef<HTMLLIElement>(null)

  /** The pipeline that applies to this run. Without a repository the three repository stages
   *  do not exist, and a checklist that lists them anyway is describing a different run. */
  const hasRepo = workspace.trim().length > 0
  const seatsSquad = SQUAD_TECHNIQUES.has(technique)
  const hidden = useMemo(() => {
    const set = new Set<string>()
    if (!hasRepo) REPO_STEPS.forEach(item => set.add(item))
    if (!seatsSquad) SQUAD_STEPS.forEach(item => set.add(item))
    return set
  }, [hasRepo, seatsSquad])
  const runSteps = useMemo(() => steps.filter(step => !hidden.has(step)), [hidden])
  const phases = useMemo(() => phasesFor(hidden), [hidden])

  /** The stage in flight: the first that has not reported. Derived rather than counted, because
   *  some stages legitimately never run — there is no debate when nothing is disputed. */
  const activeStep = loading ? runSteps.find(step => !stepsDone.includes(step)) : undefined

  // Block body: a concise arrow returns its expression, and React calls that as the cleanup.
  //
  // The feed follows itself so the newest sentence is the one on screen; `nearest` keeps the
  // scrolling inside the feed rather than dragging the page away from the form.
  useEffect(() => {
    feedEndRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [stepsDone.length])
  const narration = useMemo(() => {
    const spoken: Record<string, { text: string; status: string }> = {}
    for (const event of runEvents) {
      // Narrated per *step*, not per stage. Five steps share two events — the three parts of
      // the arithmetic, and the two halves of the gate — and narrating by stage printed one
      // sentence against all of them, which says the pipeline has nothing to report about
      // four of the five.
      for (const node of NODE_MAP[event.stage] ?? []) {
        const said = narrateStep(node, event)
        if (said) spoken[node] = { text: said, status: event.status }
      }
    }
    return spoken
  }, [runEvents])

  /** Narration in pipeline order, for the stages that have reported. */
  const feed = useMemo(
    () => runSteps
      .filter(step => narration[step])
      .map(step => ({ step, ...narration[step] })),
    [narration, runSteps],
  )
  const [upload, setUpload] = useState<any>()
  const [mapping, setMapping] = useState<Record<string, string | null>>({})
  const [jiraProject, setJiraProject] = useState('')
  const [jiraQuery, setJiraQuery] = useState('')
  const [jiraStories, setJiraStories] = useState<Story[]>([])
  const [selectedJira, setSelectedJira] = useState<string[]>([])
  const [jobId, setJobId] = useState<string>()
  const abortRef = useRef<AbortController | undefined>(undefined)

  useEffect(() => { api.estimateConfig().then(setConfig).catch(cause => setError(cause.message)) }, [])
  // The route can change without remounting — Back, or a pasted hash, both land here while
  // the page key stays the same — so the view follows the URL rather than only its first value.
  useEffect(() => {
    setView(initialView)
  }, [initialView])
  // Reopening the screen rejoins an estimate still running on the server rather than
  // presenting an idle form while work is in flight.
  // Open on a specific run when asked; otherwise rejoin whatever of ours is still going.
  // Without the id, several concurrent estimates made this a coin flip, and a finished one
  // showed an empty form — its result reachable only from the screen you just left.
  useEffect(() => {
    let disposed = false
    const load = async () => {
      if (initialJobId) {
        try {
          const job = await api.job(initialJobId)
          if (disposed) return
          if (isJobActive(job.status)) return follow(job.id)
          setRunEvents(job.events)
          setStepsDone(derivedSteps(job.events))
          const produced: EstimateResult[] = job.result?.results ?? []
          if (produced.length) {
            setResults(produced)
            setResult(produced[0])
            setStatus(`Showing a completed estimate${produced.length > 1 ? ` (${produced.length} stories)` : ''}`)
          } else {
            setStatus(`This run ${job.status} without producing an estimate`)
            if (job.error) setError(job.error)
          }
        } catch (cause) {
          // Missing and not-yours are deliberately indistinguishable: the server answers 404
          // for both, and so does this screen.
          if (!disposed) setError((cause as Error).message)
        }
        return
      }
      try {
        const { jobs } = await api.jobs()
        const live = jobs.find(job => job.kind === 'estimate' && isJobActive(job.status))
        if (live && !disposed) follow(live.id)
      } catch { /* nothing to rejoin */ }
    }
    load()
    return () => { disposed = true }
  }, [initialJobId])
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
        // A ten-story batch takes half an hour; story one is done in the first three
        // minutes. Show each result as it lands rather than all of them at the end.
        onPartial: partial => {
          const produced: EstimateResult[] = partial.results || []
          if (produced.length) {
            setResults(produced)
            setResult(current => current ?? produced[0])
          }
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
      const { job_id } = await api.submitEstimate(
        { ...next, acceptance_criteria: next.acceptance_criteria.filter(Boolean), stack,
          technique },
        workspace.trim(),
      )
      await follow(job_id)
    } catch (cause) { setLoading(false); setError((cause as Error).message) }
  }

  /** Estimate this story again from scratch, optionally with detail the story left out.
   *
   *  The previous result is not passed anywhere: a correction is appended to the story as
   *  evidence, and the pipeline runs clean. Feeding a model its own last answer produces a
   *  polite adjustment of that answer, which is the anchoring the blind pass exists to avoid. */
  async function reEstimate(correction: string) {
    if (!result) return
    const previous = result.story
    setResult(undefined)
    await estimateOne({
      title: previous.title,
      user_story: previous.user_story
        + (correction ? `

Additional detail supplied by the team: ${correction}` : ''),
      acceptance_criteria: [...previous.acceptance_criteria],
      technical_breakdown: previous.technical_breakdown,
      key: previous.key,
      source: previous.source,
    })
  }

  async function estimateMany(items: Story[]) {
    if (!items.length) return setError('Select at least one story.')
    begin()
    try {
      const { job_id } = await api.submitEstimateBatch(
        items.map(item => ({ ...item, stack, technique })), workspace.trim(),
      )
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
            onClick={() => { setView('new'); onViewChange?.('new'); setSource(item.id) }}>
            <item.Icon />{item.label}
          </button>)}
        <button className={view === 'history' ? 'active' : ''} onClick={() => setView('history')}>
          <History />History
        </button>
      </nav>

      {error && <div className="estimate-error"><AlertTriangle />{error}</div>}

      {view === 'history' && <EstimateHistoryPanel
        config={config}
        initialEntryId={initialHistoryId}
        onEntryChange={id => onViewChange?.('history', id)}
        onBack={() => { setView('new'); onViewChange?.('new') }}
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

      {view === 'new' && <><TechniquePicker
        techniques={config?.techniques ?? []}
        value={technique}
        onChange={value => { techniqueChosen.current = true; setTechnique(value) }}
        disabled={loading}
      />
      <StackPanel stack={stack} config={config} onChange={setStack}
        workspace={workspace} onWorkspaceChange={setWorkspace} />

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

        {/* Status as a wide, shallow grid; narration as a feed underneath.
            These are two different reading jobs and they want two different shapes. A grid is
            for scanning — where has it got to — and twenty-five rows in one column could not
            be scanned or sat beside anything. Prose is for reading, one column, in order. */}
        <section className={`estimate-pipeline ${loading ? 'active' : ''} ${result ? 'settled' : ''}`}>
          <div className="pipeline-head">
            <span className="eyebrow">LIVE REASONING</span>
            <h2>{status || 'Your evidence pipeline'}</h2>
            {loading && <p className="pipeline-note">
              This runs on the server. You can close the tab — the estimate keeps going and
              waits for you in Activity.
            </p>}
            {result && <p className="pipeline-settled">
              <Check size={13} /> {stepsDone.length} of {runSteps.length} stages completed. The full
              account is in the report below.
            </p>}
            {!loading && !result && <p className="pipeline-note">
              Five phases, {runSteps.length} stages. Nothing here is the model's opinion of a number
              — it scores evidence, and the arithmetic happens in code.
            </p>}
          </div>

          <div className="phase-grid">
            {phases.map(phase => {
              const done = phase.steps.filter(step => stepsDone.includes(step)).length
              const live = phase.steps.some(step => step === activeStep)
              return <div
                key={phase.id}
                className={`phase ${live ? 'live' : done === phase.steps.length ? 'done' : ''}`}
              >
                <Tooltip label={phase.title} detail={phase.blurb}>
                  <h3>{phase.title}<em>{done}/{phase.steps.length}</em></h3>
                </Tooltip>
                <ol>
                  {phase.steps.map(step => {
                    const stepDone = stepsDone.includes(step)
                    const current = step === activeStep
                    return <li
                      key={step}
                      className={stepDone ? 'done' : current ? 'current' : ''}
                      ref={current ? currentStepRef : undefined}
                    >
                      <Tooltip label={labels[step]} detail={whyForStep(step)}>
                        <span>
                          {stepDone ? <Check /> : current ? <LoaderCircle className="spin" /> : <i />}
                          {labels[step]}
                        </span>
                      </Tooltip>
                    </li>
                  })}
                </ol>
              </div>
            })}
          </div>

          {/* Every stage that has reported, in the order it happened. */}
          {feed.length > 0 && <div className="pipeline-feed">
            <h3>What each stage found</h3>
            <ol>
              {feed.map(item => <li key={item.step} className={item.status}>
                <b>{labels[item.step]}</b>
                <span>{item.text}</span>
              </li>)}
              <li ref={feedEndRef} className="feed-end" />
            </ol>
          </div>}
        </section>
      </div>

      {/* One live surface, deliberately. The flow diagram and the evidence panel both
          narrate the same events the checklist narrates, so a run used to tell the reader the
          same thing three times in three shapes — and none of the three was authoritative.
          Both still exist inside the finished result, where they are reference rather than
          progress; here the checklist is the single account of what is happening. */}
      {results.length > 0 && <section className="batch-results">
        <h2>Batch results</h2>
        {results.map(item => <button key={item.story.key || item.story.title} onClick={() => setResult(item)}>
          <strong>{item.points}</strong>
          <span><b>{item.story.title}</b><small>{item.tldr}</small></span>
          <em className={`chip-${RECOMMENDATIONS[item.recommendation].tone}`}>{RECOMMENDATIONS[item.recommendation].label}</em>
        </button>)}
      </section>}

      {result?.technique && <TechniquePanel outcome={result.technique} />}

      {result && <EstimateResultView
        result={result}
        onReEstimate={reEstimate}
        config={config}
        events={runEvents}
        onDownload={download}
        onWriteJira={config?.jira_write_enabled && result.story.source === 'jira' ? writePoints : undefined}
      />}
      </>}
    </main>
  </div>
}
