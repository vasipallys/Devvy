import { useState } from 'react'
import {
  AlertTriangle, Anchor, BrainCircuit, Check, ChevronDown, CircleSlash, Download, FlaskConical,
  GitBranch, Lightbulb, TrendingDown,
  Layers, ShieldCheck, Sigma, Target, X,
} from 'lucide-react'
import { EvidencePanel } from './EvidencePanel'
import { AgentFlowDiagram } from './AgentFlowDiagram'
import { EstimationPipelineReport } from './EstimationPipelineReport'
import type {
  AgentEvent, Calculation, EstimateConfig, EstimateResult, FactorScore, PolicyCheck,
  Points, Recommendation,
} from './types'

export const RECOMMENDATIONS: Record<Recommendation, { label: string; tone: string; blurb: string }> = {
  proceed: { label: 'Proceed', tone: 'ok', blurb: 'Every gate passed. This is committable.' },
  decompose: { label: 'Decompose', tone: 'warn', blurb: 'Too large to commit as one story.' },
  spike_first: { label: 'Spike first', tone: 'warn', blurb: 'Buy the missing knowledge before committing.' },
  upgrade_framework_first: { label: 'Evaluate the framework first', tone: 'danger', blurb: 'The stack is too new to estimate against.' },
  epic_discovery: { label: 'Epic — run discovery', tone: 'danger', blurb: 'A migration is not a story.' },
}

function Detail({ title, count, open, children }: { title: string; count?: number; open?: boolean; children: React.ReactNode }) {
  return <details className="estimate-detail" open={open}>
    <summary><span>{title}{count !== undefined && <em>{count}</em>}</span><ChevronDown size={17} /></summary>
    <div>{children}</div>
  </details>
}

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

/** Renders one estimate. Shared by a live run and a stored history entry so a recalled
 *  estimate shows the same scorecard, ledger, and gates as the day it was produced —
 *  which is the whole reason the full payload is kept. */
export function EstimateResultView({ result, config, events, onDownload, onWriteJira }: {
  result: EstimateResult
  config?: EstimateConfig
  events: AgentEvent[]
  onDownload?: () => void
  onWriteJira?: () => void
}) {
  const verdict = RECOMMENDATIONS[result.recommendation]
  const fibonacci: Points[] = config?.framework.fibonacci ?? [3, 5, 8, 13, 21, 34]
  if (!verdict) return null
  return <article className="estimate-result">
        <EvidencePanel events={events} compact title="Estimation evidence" />

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
          <button className="download-result" onClick={onDownload}><Download /> JSON</button>
          {config?.jira_write_enabled && result.story.source === 'jira' &&
            <button className="jira-write" onClick={onWriteJira}>Write {result.points} to Jira</button>}
        </div>

        <AgentFlowDiagram
          events={events}
          pipeline={result.agentic_pipeline}
          calculation={result.calculation}
        />
        <EstimationPipelineReport result={result} />

        <Detail title="Framework appendix: detailed reasoning">
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
      </article>
}
