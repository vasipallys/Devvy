import { useState } from 'react'
import {
  AlertTriangle, Check, CircleHelp, FileCheck2, GitCompareArrows, Network,
  Scale, ShieldCheck, Sparkles, UserCheck,
} from 'lucide-react'
import type {
  AgenticAssessment, AgenticPipeline, Calculation, EstimateResult,
} from './types'

type Tab = 'report' | 'readiness' | 'evidence' | 'specialists' | 'primary' | 'review' |
  'disagreements' | 'calculation' | 'references' | 'scenario' | 'human' | 'audit'

const TABS: { id: Tab; label: string }[] = [
  { id: 'report', label: 'Final report' },
  { id: 'readiness', label: 'Readiness' },
  { id: 'evidence', label: 'Evidence' },
  { id: 'specialists', label: 'Specialists' },
  { id: 'primary', label: 'Primary' },
  { id: 'review', label: 'Blind review' },
  { id: 'disagreements', label: 'Critic & resolution' },
  { id: 'calculation', label: 'Calculation' },
  { id: 'references', label: 'References' },
  { id: 'scenario', label: 'AI scenario' },
  { id: 'human', label: 'Human consensus' },
  { id: 'audit', label: 'Audit' },
]

function Score({ value }: { value: number }) {
  return <span className={`agent-score score-${value}`}>{value}</span>
}

function AssessmentTable({ assessment }: { assessment: AgenticAssessment }) {
  return <div className="agent-table-wrap">
    <table className="agent-assessment-table">
      <thead><tr>
        <th>Dimension</th><th>Range</th><th>Evidence-led rationale</th>
        <th>Boundary explanation</th><th>Confidence</th>
      </tr></thead>
      <tbody>{assessment.dimensions.map(item => <tr key={item.factor}>
        <td><b>{item.label}</b><small>{item.factor.replaceAll('_', ' ')}</small></td>
        <td><span className="agent-range"><i>{item.score_min}</i><Score value={item.score_most_likely} /><i>{item.score_max}</i></span></td>
        <td><p>{item.rationale}</p><small>{item.evidence_ids.join(' · ')}</small></td>
        <td><p><b>Not lower:</b> {item.why_not_lower}</p><p><b>Not higher:</b> {item.why_not_higher}</p></td>
        <td><span className={`agent-confidence ${item.confidence.toLowerCase()}`}>{item.confidence}</span><small>{item.provenance}</small></td>
      </tr>)}</tbody>
    </table>
  </div>
}

function CalculationReplay({ calculation }: { calculation: Calculation }) {
  return <div className="calculation-replay">
    <div className="replay-formula">
      <span><small>16-factor base</small><b>{calculation.base_sum}</b></span><i>+</i>
      <span><small>Framework rules</small><b>{calculation.base_adjustment_total}</b></span><i>+</i>
      <span><small>Stack calibration</small><b>{calculation.stack_adjustment_total}</b></span><i>=</i>
      <span className="replay-total"><small>Adjusted score</small><b>{calculation.adjusted_score}</b></span><i>→</i>
      <span className="replay-points"><small>Fibonacci</small><b>{calculation.points}</b></span>
    </div>
    <div className="replay-steps">{calculation.steps.map(step => <div className={step.applied ? 'applied' : ''} key={step.rule}>
      {step.applied ? <Check /> : <span />}
      <p><b>{step.label}</b><small>{step.reference} · running total {step.running_total}</small></p>
      <em>{step.delta > 0 ? '+' : ''}{step.delta}</em>
    </div>)}</div>
  </div>
}

export function EstimationPipelineReport({ result }: { result: EstimateResult }) {
  const [tab, setTab] = useState<Tab>('report')
  const pipeline: AgenticPipeline = result.agentic_pipeline
  const report = pipeline.final_report
  const audit = pipeline.consistency_audit

  return <section className="agentic-report" aria-label="Agentic estimation report">
    <header className="agentic-report-header">
      <div><span className="eyebrow">CONTROLLED AGENTIC PIPELINE</span>
        <h2>Every judgement has evidence. Every number has arithmetic.</h2>
      </div>
      <div className="agentic-run-state"><Sparkles /><span><b>{pipeline.mode.replace('_', ' ')}</b><small>{pipeline.version}</small></span></div>
    </header>

    <nav className="agentic-tabs" aria-label="Estimation report sections">
      {TABS.map(item => <button key={item.id} className={tab === item.id ? 'active' : ''}
        aria-selected={tab === item.id} onClick={() => setTab(item.id)}>{item.label}</button>)}
    </nav>

    <div className="agentic-panel" role="tabpanel">
      {tab === 'report' && <>
        <div className="report-summary-grid">
          <div className="report-points"><small>RECOMMENDED</small><strong>{report.recommended_points}</strong><span>story points</span></div>
          <div><h3>Why this value</h3><p>{report.why_selected}</p></div>
          <div><h3>Why not lower</h3><p>{report.why_not_lower}</p></div>
          <div><h3>Why not higher</h3><p>{report.why_not_higher}</p></div>
        </div>
        <div className="human-authority"><UserCheck /><p><b>Decision support, not an automatic commitment</b><span>{report.human_authority}</span></p></div>
        <div className="pipeline-metrics">
          <span><b>{Math.round(audit.dimension_stability_index * 100)}%</b><small>dimension stability</small></span>
          <span><b>{audit.point_stability.primary} / {audit.point_stability.reviewer}</b><small>independent point cross-checks</small></span>
          <span><b>{audit.material_disagreements}</b><small>material disagreements</small></span>
          <span><b>{result.confidence}</b><small>final confidence</small></span>
        </div>
      </>}

      {tab === 'readiness' && <>
        <div className={`readiness-decision ${pipeline.readiness.decision.toLowerCase()}`}>
          <FileCheck2 /><span><small>READINESS DECISION</small><b>{pipeline.readiness.decision.replaceAll('_', ' ')}</b></span>
        </div>
        <div className="readiness-list">{pipeline.readiness.checks.map(check => <article key={check.area}>
          {check.status === 'ready' ? <Check /> : check.status === 'blocked' ? <AlertTriangle /> : <CircleHelp />}
          <div><h3>{check.area}<em>{check.status}</em></h3><p>{check.detail}</p>
            {check.question && <small><b>Resolve:</b> {check.question}</small>}
            <small>Evidence: {check.evidence_ids.join(' · ') || 'none supplied'}</small></div>
        </article>)}</div>
      </>}

      {tab === 'evidence' && <div className="evidence-catalog">
        <div className="evidence-integrity"><ShieldCheck /><p><b>Stable evidence envelope</b><span>{pipeline.canonical_story.input_hash}</span></p></div>
        {Object.entries(pipeline.canonical_story.evidence).map(([id, value]) => <article key={id}>
          <span>{id}</span><p>{value}</p>
        </article>)}
        <h3>Prompt context manifest</h3>
        {result.evidence.context_manifest.map(item => <article key={item.id}>
          <span>{item.id}</span><p><b>{item.label}</b><small>{item.characters} characters · {item.trusted ? 'trusted policy context' : 'untrusted evidence'}{item.truncated ? ' · truncated' : ''}</small></p>
        </article>)}
      </div>}

      {tab === 'specialists' && <>
        <p className="panel-intro"><Network />Routing is deterministic and risk-based. Specialist lenses focus the evidence; they do not invent separate point values.</p>
        <div className="specialist-grid">{pipeline.specialist_routes.map(route => {
          const finding = pipeline.specialist_findings.find(item => item.role === route.role)
          return <article key={route.role}>
            <span>{route.role.replaceAll('_', ' ')}</span><h3>{route.label}</h3><p>{route.reason}</p>
            <small>{route.dimensions.map(item => item.replaceAll('_', ' ')).join(' · ')}</small>
            {finding && <div className="specialist-finding"><b>{finding.summary}</b>
              {finding.material_risks.map(item => <p className="specialist-risk" key={item}>{item}</p>)}
              {finding.open_questions.map(item => <p className="specialist-question" key={item}>{item}</p>)}
              <small>Evidence: {finding.evidence_ids.join(' · ')}</small>
            </div>}
          </article>
        })}</div>
      </>}

      {tab === 'primary' && <><div className="assessment-heading"><div><h3>Primary estimator</h3><p>First evidence assessment, before any independent comparison.</p></div><b>{pipeline.primary.point_cross_check} point cross-check</b></div><AssessmentTable assessment={pipeline.primary} /></>}

      {tab === 'review' && <><div className="assessment-heading"><div><h3>Independent blind reviewer</h3><p>The reviewer received the same evidence and rubric, but no primary scores.</p></div><b>{pipeline.reviewer.point_cross_check} point cross-check</b></div><AssessmentTable assessment={pipeline.reviewer} /></>}

      {tab === 'disagreements' && <>
        <p className="panel-intro"><GitCompareArrows />Only explicit score differences are shown. Material or protected-risk differences receive a critic challenge and recorded arbitration.</p>
        {pipeline.disagreements.length === 0 ? <div className="empty-finding"><Check />The independent assessments agreed on every dimension.</div> :
          <div className="disagreement-list">{pipeline.disagreements.map(item => {
            const decision = pipeline.arbitration.find(choice => choice.factor === item.factor)
            const challenge = pipeline.critic_challenges.find(choice => choice.factor === item.factor)
            return <article className={item.material ? 'material' : ''} key={item.factor}>
              <header><span><b>{item.label}</b><small>{item.reasons.join(' · ')}</small></span><div><Score value={item.primary_score} /><i>vs</i><Score value={item.reviewer_score} /><em>→ {decision?.selected_score}</em></div></header>
              {challenge && <p><b>Critic:</b> {challenge.challenge} <span>{challenge.evidence_needed}</span></p>}
              <footer><Scale /><span><b>{decision?.policy}</b><small>{decision?.rationale}</small></span>{decision?.human_approval_required && <em>Human approval</em>}</footer>
            </article>
          })}</div>}
      </>}

      {tab === 'calculation' && <><p className="panel-intro"><Scale />The model never chooses the point value. Application code replays the published framework rules on the arbitrated scorecard.</p><CalculationReplay calculation={result.calculation} /></>}

      {tab === 'references' && <>
        <p className="panel-intro">Reference stories calibrate scale; they never replace the factor arithmetic.</p>
        <div className="reference-grid">{result.anchors_considered.map(anchor => <article key={`${anchor.stack}-${anchor.title}`}><strong>{anchor.points}</strong><span><b>{anchor.title}</b><small>{anchor.stack}</small></span></article>)}</div>
        <p className="reference-note">{result.anchor_comparison}</p>
      </>}

      {tab === 'scenario' && <div className="scenario-card">
        <Sparkles /><div><span>AI DELIVERY SCENARIO</span><h3>No uncalibrated AI discount applied</h3><p>Devvy does not reduce points merely because AI tools may be used. A future scenario can be shown only when the team has comparable measured outcomes, unchanged quality gates, and explicit human approval.</p></div>
      </div>}

      {tab === 'human' && <>
        <div className="human-review-card"><UserCheck /><div><span>FINAL AUTHORITY</span><h3>Team consensus is pending</h3><p>{pipeline.human_review.reason}</p></div></div>
        <div className="human-options">{pipeline.human_review.options.map(option => <div key={option}><b>{option}</b><span>{option === 'accept' ? `Commit ${result.points} after team review` : option === 'override' ? 'Record a different value and its evidence' : option === 'spike' ? 'Buy missing knowledge before commitment' : 'Split the work into estimable outcomes'}</span></div>)}</div>
        <p className="human-note">Exporting this report or writing to Jira does not change the audit status. Record the team decision in the delivery system of record.</p>
      </>}

      {tab === 'audit' && <>
        <div className={`audit-status ${audit.status.toLowerCase()}`}><ShieldCheck /><span><small>CONSISTENCY AUDIT</small><b>{audit.status.replaceAll('_', ' ')}</b></span></div>
        <div className="audit-grid">
          <span><small>Calculation replay</small><b>{audit.calculation_replay_passed ? 'Passed' : 'Failed'}</b></span>
          <span><small>Risk-floor stability</small><b>{audit.risk_floor_stable ? 'Stable' : 'Review'}</b></span>
          <span><small>Point boundary</small><b>{audit.point_stability.same_boundary ? 'Consistent' : 'Changed'}</b></span>
          <span><small>Model passes</small><b>{pipeline.model_policy.independent_model_passes}</b></span>
        </div>
        <dl className="audit-ledger">
          <div><dt>Input hash</dt><dd>{pipeline.canonical_story.input_hash}</dd></div>
          <div><dt>Primary prompt</dt><dd>{pipeline.prompt_versions.primary}</dd></div>
          <div><dt>Reviewer prompt</dt><dd>{pipeline.prompt_versions.reviewer}</dd></div>
          <div><dt>Local model</dt><dd>{pipeline.model_policy.model}</dd></div>
          <div><dt>Execution</dt><dd>{pipeline.model_policy.serialized ? 'Serialized on shared runtime' : 'Parallel'}</dd></div>
          <div><dt>Hidden chain-of-thought</dt><dd>{pipeline.model_policy.hidden_chain_of_thought_stored ? 'Stored' : 'Not requested or stored'}</dd></div>
        </dl>
        {audit.warnings.map(item => <p className="audit-warning" key={item}><AlertTriangle />{item}</p>)}
      </>}
    </div>
  </section>
}
