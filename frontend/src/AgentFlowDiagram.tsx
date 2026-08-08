import { useState } from 'react'
import {
  Check, ChevronDown, CircleAlert, CircleDashed, Eye, EyeOff, LoaderCircle, Scale, UserCheck,
} from 'lucide-react'
import { narrate } from './evidenceNarration'
import { Tooltip } from './Tooltip'
import type { AgentEvent, AgenticPipeline, Calculation } from './types'

/** One node in the flow. `lane` groups nodes into the phases a reader can name. */
interface Node {
  stage: string
  label: string
  hint: string
  /** Why this stage exists. Shown on hover, because the reasoning should not need a click. */
  why: string
  lane: 'intake' | 'independent' | 'reconcile' | 'deterministic' | 'human'
  /** Which parallel branch this sits in, for the two independent model passes. */
  branch?: 'primary' | 'reviewer'
}

const NODES: Node[] = [
  { stage: 'normalize', label: 'Normalize', hint: 'Freeze the evidence and hash it', why: 'Estimating the same story twice must give the same evidence. The text is canonicalised and hashed so a later run can be compared against this one.', lane: 'intake' },
  { stage: 'readiness', label: 'Readiness', hint: 'Is this story estimable at all?', why: 'Some stories cannot be estimated at all. Catching that here avoids producing a confident number from evidence that does not support one.', lane: 'intake' },
  { stage: 'specialist_routing', label: 'Route lenses', hint: 'Pick the specialist views this story needs', why: 'Routing is deterministic and risk-based, so the lenses applied to a story are predictable and reviewable rather than whatever the model felt like considering.', lane: 'intake' },
  { stage: 'assemble_context', label: 'Bound context', hint: 'Budget and label the evidence', why: 'The evidence is budgeted and labelled untrusted, so story text arriving from Jira or a spreadsheet is read as data and never as instructions.', lane: 'intake' },
  { stage: 'declare_stack', label: 'Load calibration', hint: 'Apply the declared stack profile', why: "The same work costs differently on different stacks. This loads the declared stack's scoring guidance, reference stories, and maturity cap.", lane: 'intake' },

  { stage: 'primary_estimate', label: 'Primary estimator', hint: 'First independent model pass', why: 'The model scores 16 factors from 1 to 5 and explains each. It is never asked for the point value — that is arithmetic, not judgement.', lane: 'independent', branch: 'primary' },
  { stage: 'specialist_analysis', label: 'Specialist lenses', hint: 'Project scores through owned dimensions', why: 'A deterministic projection of the scored evidence through each routed lens. It surfaces ownership and risk; it does not add another opinion.', lane: 'independent', branch: 'primary' },
  { stage: 'blind_review', label: 'Blind reviewer', hint: 'Second pass — never sees the first scores', why: 'A second pass over the same evidence, blind to the first and run warmer. It only runs when a different opinion could change the answer, because it doubles the CPU cost.', lane: 'independent', branch: 'reviewer' },

  { stage: 'disagreement', label: 'Compare', hint: 'Where do the two passes differ?', why: 'Where two independent readings differ is exactly where the estimate is least certain. Differences are measured rather than averaged away silently.', lane: 'reconcile' },
  { stage: 'critic', label: 'Critic', hint: 'Challenge material differences', why: 'A material difference should not be settled by whichever answer was phrased more confidently. The critic states what evidence would actually resolve it.', lane: 'reconcile' },
  { stage: 'arbitration', label: 'Arbitrate', hint: 'Resolve by fixed policy, not persuasion', why: 'Conflicts resolve by published policy — conservative on protected risk, midpoint otherwise — so the outcome does not depend on persuasion.', lane: 'reconcile' },

  { stage: 'score_factors', label: 'Final scorecard', hint: '16 factors, 1–5', why: 'The final 16-factor scorecard. Factors the model declined to score are filled from story text and labelled inferred, so judgement is distinguishable from a guess.', lane: 'deterministic' },
  { stage: 'calculate', label: 'Arithmetic', hint: 'Sum, adjust, map to Fibonacci', why: "Fixed arithmetic: base sum, the framework's adjustments, then a Fibonacci band. Every rule is recorded whether or not it fired, so the number can be replayed by hand.", lane: 'deterministic' },
  { stage: 'policy_gate', label: 'Gates', hint: 'Spike, split, and cap rules', why: 'Gates can override the number entirely. Maximum uncertainty or a bleeding-edge framework means the honest answer is a spike, not a smaller estimate.', lane: 'deterministic' },
  { stage: 'consistency_audit', label: 'Audit', hint: 'Replay and check the result holds', why: 'The calculation is replayed from the final scorecard and checked against what was reported, so a mistake here shows up rather than shipping quietly.', lane: 'deterministic' },

  { stage: 'human_review', label: 'Human decision', hint: 'The team owns the estimate', why: 'The recommendation is decision support. The team owns the estimate and may accept, override, spike, or decompose it — and that decision is recorded.', lane: 'human' },
]

const LANES: { id: Node['lane']; title: string; blurb: string }[] = [
  { id: 'intake', title: 'Intake', blurb: 'Turn the story into stable, bounded evidence.' },
  { id: 'independent', title: 'Two independent assessments', blurb: 'Two model passes score the same evidence separately. The reviewer is blind to the first.' },
  { id: 'reconcile', title: 'Reconciliation', blurb: 'Differences are surfaced, challenged, then resolved by published policy.' },
  { id: 'deterministic', title: 'Deterministic calculation', blurb: 'No model runs here. Fixed rules turn the scorecard into a number.' },
  { id: 'human', title: 'Human authority', blurb: 'The recommendation is decision support. The team decides.' },
]

type Status = 'pending' | 'running' | 'completed' | 'waiting' | 'failed'

/** Derive each node's state.
 *
 *  A live run has an event stream; a stored estimate recalled from history has none, but the
 *  presence of a finished pipeline is itself proof every stage ran. Without that fallback a
 *  completed estimate would render as entirely pending, which is worse than showing nothing. */
function statuses(events: AgentEvent[], finished: boolean): Record<string, Status> {
  const seen: Record<string, Status> = {}
  for (const event of events) {
    const status = event.status
    if (status === 'failed') seen[event.stage] = 'failed'
    else if (status === 'waiting') seen[event.stage] = 'waiting'
    else if (status === 'completed' || status === 'validated') seen[event.stage] = 'completed'
    else if (!seen[event.stage]) seen[event.stage] = 'running'
  }
  const order = NODES.map(node => node.stage)
  const lastReported = Math.max(-1, ...order.map((stage, index) => (seen[stage] ? index : -1)))
  const result: Record<string, Status> = {}
  order.forEach((stage, index) => {
    result[stage] = seen[stage]
      ?? (finished ? 'completed' : index < lastReported ? 'completed' : 'pending')
  })
  // The pipeline always ends waiting on a person; it is never "done" on the model's say-so.
  if (finished && result.human_review === 'completed') result.human_review = 'waiting'
  return result
}

/** The headline number a node produced, read from the stored pipeline. Used for estimates
 *  recalled from history, where no event stream survives but every output does. */
function storedMetric(
  stage: string, pipeline: AgenticPipeline, calculation?: Calculation,
): string | undefined {
  const material = pipeline.disagreements.filter(item => item.material).length
  switch (stage) {
    case 'normalize': return `${Object.keys(pipeline.canonical_story.evidence).length} evidence item(s)`
    case 'readiness': return pipeline.readiness.decision.replaceAll('_', ' ').toLowerCase()
    case 'specialist_routing': return `${pipeline.specialist_routes.length} lens(es)`
    case 'assemble_context': return 'bounded'
    case 'declare_stack': return `maturity ${pipeline.canonical_story.stack.maturity_level}`
    case 'primary_estimate': return `${pipeline.primary.model_scored}/16 model · cross-check ${pipeline.primary.point_cross_check}`
    case 'blind_review': return `${pipeline.reviewer.model_scored}/16 model · cross-check ${pipeline.reviewer.point_cross_check}`
    case 'specialist_analysis': return `${pipeline.specialist_findings.length} finding(s)`
    case 'disagreement': return `${material} material of ${pipeline.disagreements.length}`
    case 'critic': return `${pipeline.critic_challenges.length} challenge(s)`
    case 'arbitration': return `${pipeline.arbitration.length} decision(s)`
    case 'score_factors': return `${pipeline.primary.model_scored} scored · ${pipeline.primary.heuristic_filled} inferred`
    case 'calculate': return calculation ? `${calculation.adjusted_score} → ${calculation.points} points` : undefined
    case 'policy_gate': return pipeline.final_report.recommendation.replaceAll('_', ' ')
    case 'consistency_audit': return pipeline.consistency_audit.status.replaceAll('_', ' ').toLowerCase()
    case 'human_review': return 'awaiting decision'
    default: return undefined
  }
}

/** The headline number a node produced, pulled from its evidence. Showing a node's actual
 *  output is what turns a progress checklist into an explanation.
 *
 *  Every progress event carries an `evidence` object — the batch wrapper always adds a story
 *  index — so a mid-flight retry event matches the stage without holding the stage's results.
 *  Each case therefore checks the fields it needs rather than assuming they arrived, which is
 *  what stops a running node reading "undefined/16 model". */
function metric(stage: string, events: AgentEvent[]): string | undefined {
  const event = [...events].reverse().find(item => item.stage === stage && item.evidence)
  const evidence = event?.evidence as Record<string, any> | undefined
  if (!evidence) return undefined
  const has = (...keys: string[]) => keys.every(key => evidence[key] !== undefined)
  switch (stage) {
    case 'normalize': return has('evidence_items') ? `${evidence.evidence_items} evidence item(s)` : undefined
    case 'readiness': return has('checks', 'questions') ? `${evidence.checks} checks · ${evidence.questions} question(s)` : undefined
    case 'specialist_routing': return has('specialists') ? `${evidence.specialists.length} lens(es)` : undefined
    case 'assemble_context': return has('characters') ? `${evidence.characters} chars` : undefined
    case 'declare_stack': return has('maturity') ? String(evidence.maturity) : undefined
    case 'primary_estimate':
    case 'blind_review': return has('model_scored', 'point_cross_check')
      ? `${evidence.model_scored}/16 model · cross-check ${evidence.point_cross_check}` : undefined
    case 'specialist_analysis': return has('material_risks') ? `${evidence.material_risks} risk(s)` : undefined
    case 'disagreement': return has('material', 'differences') ? `${evidence.material} material of ${evidence.differences}` : undefined
    case 'critic': return has('challenges') ? `${evidence.challenges} challenge(s)` : undefined
    case 'arbitration': return has('decisions') ? `${evidence.decisions} decision(s)` : undefined
    case 'score_factors': return has('model_scored', 'heuristic_filled') ? `${evidence.model_scored} scored · ${evidence.heuristic_filled} inferred` : undefined
    case 'calculate': return has('adjusted_score', 'points') ? `${evidence.adjusted_score} → ${evidence.points} points` : undefined
    case 'policy_gate': return has('gates_evaluated', 'confidence') ? `${evidence.gates_evaluated} gates · ${evidence.confidence}` : undefined
    case 'consistency_audit': return has('status') ? String(evidence.status).replaceAll('_', ' ') : undefined
    case 'human_review': return 'awaiting decision'
    default: return undefined
  }
}

/** What this stage actually did, in a sentence, from the run's own evidence.
 *
 *  The compact metric beside a node ("17 evidence item(s)", "2055 chars") is a glanceable
 *  reading, not an explanation: it says what a number was without saying what the stage did or
 *  whether that number is good. Opening a node should answer the question that made the reader
 *  open it. The same narration the evidence panel uses is reused deliberately — one stage
 *  should not describe itself two different ways in two places.
 *
 *  A stage that has not run yet has no evidence to narrate, so the node falls back to why the
 *  stage exists. That is the honest thing to show for work that has not happened. */
function narrationFor(stage: string, events: AgentEvent[]): string | undefined {
  const event = [...events].reverse().find(item => item.stage === stage)
  return event ? narrate(event) : undefined
}

function StatusMark({ status }: { status: Status }) {
  if (status === 'running') return <LoaderCircle className="spin" size={13} />
  if (status === 'completed') return <Check size={13} />
  if (status === 'failed') return <CircleAlert size={13} />
  if (status === 'waiting') return <UserCheck size={13} />
  return <CircleDashed size={13} />
}

function FlowNode({ node, status, detail, narration, expanded, onToggle }: {
  node: Node
  status: Status
  detail?: string
  /** What this stage did on this run, when it has run. */
  narration?: string
  expanded: boolean
  onToggle: () => void
}) {
  return <Tooltip label={node.label} detail={node.why}><button
    className={`flow-node status-${status} ${expanded ? 'expanded' : ''}`}
    aria-current={status === 'running' ? 'step' : undefined}
    aria-expanded={expanded}
    onClick={onToggle}
  >
    <span className="flow-node-head">
      <span className="flow-mark"><StatusMark status={status} /></span>
      <b>{node.label}</b>
      {node.branch === 'reviewer' && <span className="flow-blind" title="Blind: never sees the primary scores"><EyeOff size={11} /></span>}
      {node.branch === 'primary' && node.stage === 'primary_estimate' && <span className="flow-blind open" title="Sees the story evidence"><Eye size={11} /></span>}
      <ChevronDown size={12} className="flow-chevron" />
    </span>
    {detail && <span className="flow-metric">{detail}</span>}
    {expanded && <span className="flow-detail">
      {/* What happened on this run, when it has run; otherwise why the stage exists. Never
          both — a reader opening a node wants one answer, not a paragraph of scaffolding. */}
      <span className="flow-narration">{narration ?? node.why}</span>
      {!narration && <span className="flow-hint">Not reached yet on this run.</span>}
    </span>}
  </button></Tooltip>
}

/** The arithmetic, drawn as a flow rather than described. Each step shows the number it
 *  contributed, so the path from 16 scores to one Fibonacci value is followable by eye. */
function CalculationFlow({ calculation }: { calculation: Calculation }) {
  const steps = [
    { label: '16 factor scores', value: calculation.base_sum, note: 'base sum',
      why: 'Every factor scored 1–5 and added up. This is the only number the model influences, and it does so one factor at a time.' },
    { label: 'Base adjustments', value: calculation.base_adjustment_total, note: '§8.1', signed: true,
      why: 'Fixed penalties for combinations the framework knows cost more than their parts — high uncertainty, cross-team dependency, irreversibility, or a full-stack change.' },
    { label: 'Stack adjustments', value: calculation.stack_adjustment_total, note: '§8.2', signed: true,
      why: "Penalties from the declared stack: framework maturity, the team's experience with it, and any new testing, observability, or build work it forces." },
  ]
  return <div className="calc-flow" aria-label="How the number was calculated">
    {steps.map(step => <Tooltip key={step.label} label={step.label} detail={step.why}>
      <div className="calc-step">
        <b>{step.signed && step.value >= 0 ? `+${step.value}` : step.value}</b>
        <span>{step.label}</span><small>{step.note}</small>
      </div>
    </Tooltip>)}
    <div className="calc-arrow" aria-hidden>=</div>
    <Tooltip label="Adjusted score"
      detail={`The scored total plus every adjustment that fired. It falls in band ${calculation.band}, and the band — not the model — decides the points.`}>
      <div className="calc-step total">
        <b>{calculation.adjusted_score}</b><span>Adjusted score</span><small>band {calculation.band}</small>
      </div>
    </Tooltip>
    <div className="calc-arrow" aria-hidden>→</div>
    <Tooltip label="Story points"
      detail={calculation.cap_exceeded
        ? `This exceeds the ${calculation.maturity_cap}-point ceiling the declared framework maturity allows. The number is reported honestly rather than shrunk; the recommendation escalates instead.`
        : `The Fibonacci value for band ${calculation.band}, within the ${calculation.maturity_cap}-point ceiling this framework maturity allows.`}>
      <div className={`calc-step result ${calculation.cap_exceeded ? 'capped' : ''}`}>
        <b>{calculation.points}</b><span>Story points</span>
        <small>{calculation.cap_exceeded ? `over the ${calculation.maturity_cap}-point cap` : `cap ${calculation.maturity_cap}`}</small>
      </div>
    </Tooltip>
  </div>
}

export function AgentFlowDiagram({ events, pipeline, calculation, defaultOpen = false }: {
  events: AgentEvent[]
  pipeline?: AgenticPipeline
  calculation?: Calculation
  defaultOpen?: boolean
}) {
  const [expanded, setExpanded] = useState<string>()
  const [showAll, setShowAll] = useState(defaultOpen)
  const state = statuses(events, Boolean(pipeline))
  const done = NODES.filter(node => state[node.stage] === 'completed').length
  // Live events win where they exist; the stored pipeline fills in a recalled estimate.
  const detailFor = (stage: string) =>
    metric(stage, events) ?? (pipeline ? storedMetric(stage, pipeline, calculation) : undefined)

  return <section className="agent-flow" aria-label="Agent pipeline flow">
    <header className="agent-flow-head">
      <div>
        <span className="eyebrow">HOW THIS ESTIMATE IS BEING MADE</span>
        <h3>Agent flow</h3>
        <p>
          Two independent passes score the same evidence, their differences are reconciled by
          published policy, and only then does fixed arithmetic produce a number. No model
          chooses the points.
        </p>
      </div>
      <div className="agent-flow-progress" role="status">
        <b>{done}<small>/{NODES.length}</small></b>
        <span>stages complete</span>
        {pipeline && <em>{pipeline.mode.replaceAll('_', ' ').toLowerCase()} pipeline</em>}
      </div>
    </header>

    <div className="flow-lanes">
      {LANES.map(lane => {
        const nodes = NODES.filter(node => node.lane === lane.id)
        const parallel = lane.id === 'independent'
        return <section className={`flow-lane lane-${lane.id}`} key={lane.id}>
          <header>
            <h4>{lane.title}</h4>
            <p>{lane.blurb}</p>
          </header>
          {parallel
            ? <div className="flow-branches">
                <div className="flow-branch">
                  <span className="branch-tag">Pass A</span>
                  {nodes.filter(node => node.branch === 'primary').map(node =>
                    <FlowNode key={node.stage} node={node} status={state[node.stage]}
                      detail={detailFor(node.stage)} narration={narrationFor(node.stage, events)}
                      expanded={expanded === node.stage}
                      onToggle={() => setExpanded(expanded === node.stage ? undefined : node.stage)} />)}
                </div>
                <div className="flow-branch">
                  <span className="branch-tag blind">Pass B · blind</span>
                  {nodes.filter(node => node.branch === 'reviewer').map(node =>
                    <FlowNode key={node.stage} node={node} status={state[node.stage]}
                      detail={detailFor(node.stage)} narration={narrationFor(node.stage, events)}
                      expanded={expanded === node.stage}
                      onToggle={() => setExpanded(expanded === node.stage ? undefined : node.stage)} />)}
                </div>
              </div>
            : <div className="flow-row">
                {nodes.map(node =>
                  <FlowNode key={node.stage} node={node} status={state[node.stage]}
                    detail={detailFor(node.stage)} narration={narrationFor(node.stage, events)}
                    expanded={expanded === node.stage}
                    onToggle={() => setExpanded(expanded === node.stage ? undefined : node.stage)} />)}
              </div>}
        </section>
      })}
    </div>

    {calculation && <div className="flow-calc-wrap">
      <span className="eyebrow">THE ARITHMETIC</span>
      <CalculationFlow calculation={calculation} />
    </div>}

    {pipeline && <button className="flow-more" onClick={() => setShowAll(!showAll)}>
      <Scale size={13} /> {showAll ? 'Hide' : 'Show'} what the two passes disagreed on
      ({pipeline.disagreements.filter(item => item.material).length} material)
    </button>}

    {pipeline && showAll && <div className="flow-disagreements">
      {pipeline.disagreements.length === 0 && <p>Both passes agreed on every factor.</p>}
      {pipeline.disagreements.map(item => {
        const decision = pipeline.arbitration.find(entry => entry.factor === item.factor)
        return <div className={`disagreement ${item.material ? 'material' : ''}`} key={item.factor}>
          <b>{item.label}</b>
          <span className="disagreement-scores">
            <i title="Primary estimator">{item.primary_score}</i>
            <em>vs</em>
            <i title="Blind reviewer">{item.reviewer_score}</i>
            {decision && <><em>→</em><strong title="Arbitrated score">{decision.selected_score}</strong></>}
          </span>
          <small>{decision?.policy ?? item.reasons.join('; ')}</small>
          {decision?.human_approval_required && <span className="needs-human">needs human approval</span>}
        </div>
      })}
    </div>}
  </section>
}
