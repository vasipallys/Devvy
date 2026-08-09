import { AlertTriangle, Check, FileLock2, Gavel, Scale, ShieldAlert, Swords, X } from 'lucide-react'
import { Tooltip } from './Tooltip'
import type { Eagle, EagleFinding } from './types'

/**
 * The EAGLE governance layer, shown as evidence rather than as a verdict.
 *
 * The architecture's claim is that the number is reproducible — same story, same snapshot,
 * same harness, same estimate. A reader can only believe that if they can see what was frozen,
 * which factors the independent passes disagreed on, what the reviewers challenged, which
 * deterministic rules ran, and what this story was anchored against. So each of those is a
 * section here, and each one says what it found rather than that it ran.
 */

const REVIEWER_META: Record<EagleFinding['reviewer'], { label: string; why: string }> = {
  critic: {
    label: 'Critic',
    why: 'Attacks the estimate that exists. It never proposes a score of its own — its job is '
      + 'to find the component that was missed or the score that carries no evidence.',
  },
  adversarial: {
    label: 'Adversarial',
    why: 'Assumes the estimate is too low and looks only for the most credible reason why: '
      + 'hidden consumers, migration risk, missing rollback, fragile tests.',
  },
  optimistic: {
    label: 'Optimistic',
    why: 'The counterweight, so the adversarial pass cannot inflate unopposed. It looks for '
      + 'complexity counted under two factors, or work the platform already solves.',
  },
}

function Severity({ value }: { value: EagleFinding['severity'] }) {
  return <span className={`eagle-severity ${value}`}>{value}</span>
}

export function EaglePanel({ eagle }: { eagle: Eagle }) {
  const { contract, factor_aggregates: aggregates, findings, validation, spike_gate: gate,
    references, debate, failure_attribution: failures } = eagle
  const disputed = aggregates.filter(item => item.status === 'dispute')
  const failed = validation.rules.filter(item => !item.passed)

  return <section className="eagle-panel">
    <header className="eagle-head">
      <Scale size={16} />
      <span>
        <b>EAGLE governance</b>
        <small>
          Evidence-Augmented Governed Layered Estimation · {eagle.version} · agents reason,
          code calculates
        </small>
      </span>
    </header>

    {/* §2. What was frozen. Without this the reproducibility claim is unverifiable. */}
    <div className="eagle-block">
      <h4><FileLock2 size={13} /> Estimation contract</h4>
      <div className="eagle-contract">
        <Tooltip
          label="Contract hash"
          detail="A digest of the objective, criteria, stack and rules this run was bound to.
            Two runs with the same hash were given the same problem; a different hash is the
            first thing to check when two estimates disagree."
        >
          <code>{contract.contract_hash.replace('sha256:', '').slice(0, 12)}</code>
        </Tooltip>
        <span>{contract.story_id}</span>
        <span>v{contract.contract_version}</span>
        <span>{Object.entries(contract.expected_stacks).map(([k, v]) => `${k}: ${v}`).join(' · ')
          || 'no stack declared'}</span>
      </div>
      <ul className="eagle-stops">
        {Object.entries(contract.stop_conditions).map(([condition, action]) => <li key={condition}>
          <span>{condition.replaceAll('_', ' ')}</span><b>{action}</b>
        </li>)}
      </ul>
    </div>

    {/* §14. Where the independent passes actually disagreed. */}
    <div className="eagle-block">
      <h4><Swords size={13} /> Independent agreement</h4>
      <p className="eagle-note">
        Spread of 0 accepts, 1 accepts the median, 2 or more disputes — and an elevated score
        with no supporting evidence disputes even when the estimators agree, so a missing
        answer can never settle quietly at a number.
      </p>
      {disputed.length === 0
        ? <p className="eagle-clear"><Check size={13} /> No factor was disputed across
          {' '}{aggregates[0]?.scores.length ?? 0} independent assessments.</p>
        : <table className="eagle-table">
          <thead><tr><th>Factor</th><th>Owner</th><th>Scores</th><th>Spread</th><th>Why</th></tr></thead>
          <tbody>
            {disputed.map(item => <tr key={item.factor}>
              <td>{item.label}</td>
              <td>{item.owner}</td>
              <td>{item.scores.join(' · ')} → <b>{item.median_score}</b></td>
              <td>{item.spread}</td>
              <td>{item.reason}</td>
            </tr>)}
          </tbody>
        </table>}
      {debate.rounds.length > 0 && <ol className="eagle-debate">
        {debate.rounds.map((round, index) => <li key={`${round.factor}-${index}`}>
          <b>Round {round.round} · {round.label}</b>
          <small>{round.challenge}</small>
          <small className="eagle-resolution">
            {round.resolved ? `Settled at ${round.selected_score}/5. ` : 'Unresolved. '}
            {round.resolution}
          </small>
        </li>)}
      </ol>}
      {debate.escalation === 'HUMAN_REVIEW' && <p className="eagle-escalate">
        <ShieldAlert size={13} /> A bounded debate did not resolve every challenge. More rounds
        would not converge — this needs a human specialist.
      </p>}
    </div>

    {/* §11 / §12 / §13. Three reviewers, deliberately pulling in opposite directions. */}
    {findings.length > 0 && <div className="eagle-block">
      <h4><Gavel size={13} /> Reviewer findings ({findings.length})</h4>
      {(['critic', 'adversarial', 'optimistic'] as const).map(reviewer => {
        const mine = findings.filter(item => item.reviewer === reviewer)
        if (!mine.length) return null
        return <div key={reviewer} className="eagle-reviewer">
          <Tooltip label={REVIEWER_META[reviewer].label} detail={REVIEWER_META[reviewer].why}>
            <h5>{REVIEWER_META[reviewer].label} <span>{mine.length}</span></h5>
          </Tooltip>
          <ul>
            {mine.map((item, index) => <li key={index}>
              <Severity value={item.severity} />
              <span>
                <b>{item.finding}</b>
                <small>Correction: {item.suggested_correction}</small>
                <small className="eagle-cite">
                  {item.evidence_ids.slice(0, 4).join(', ') || 'no evidence cited'}
                  {' · '}confidence {(item.confidence * 100).toFixed(0)}%
                </small>
              </span>
            </li>)}
          </ul>
        </div>
      })}
    </div>}

    {/* §17 / §20. The rules that are enforced in code rather than asked of a model. */}
    <div className="eagle-block">
      <h4><Check size={13} /> Deterministic validation</h4>
      <ul className="eagle-rules">
        {validation.rules.map(rule => <li key={rule.rule} className={rule.passed ? 'ok' : 'bad'}>
          {rule.passed ? <Check size={12} /> : <X size={12} />}
          <span><b>{rule.rule}</b><small>{rule.detail}</small></span>
        </li>)}
      </ul>
      <p className={`eagle-gate ${gate.decision === 'PROCEED' ? '' : 'blocked'}`}>
        <b>Spike gate: {gate.decision.replaceAll('_', ' ')}</b>
        <span>{gate.summary}</span>
        {gate.triggered.map(item => <small key={item}>{item}</small>)}
      </p>
      {failed.length > 0 && <p className="eagle-escalate">
        <AlertTriangle size={13} /> {failed.length} validation rule(s) failed. The estimate is
        still shown, but it has not satisfied the framework's own contract.
      </p>}
    </div>

    {/* §10. What this story was anchored against, and how weak that anchor is. */}
    <div className="eagle-block">
      <h4><Scale size={13} /> Reference stories</h4>
      <p className="eagle-note">{references.note}</p>
      {references.matches.length > 0 && <table className="eagle-table">
        <thead><tr><th>Story</th><th>Points</th><th>Similarity</th><th>Differences</th></tr></thead>
        <tbody>
          {references.matches.map(item => <tr key={item.id}>
            <td>{item.title}</td>
            <td><b>{item.points}</b></td>
            <td>
              <Tooltip
                label={`${(item.similarity * 100).toFixed(0)}% similar`}
                detail={`Semantic ${(item.components.semantic * 100).toFixed(0)}% ·
                  structural ${(item.components.structural * 100).toFixed(0)}% ·
                  stack ${(item.components.stack * 100).toFixed(0)}%. Structural similarity
                  compares the sixteen factor scores, which is what makes two stories the same
                  shape of work rather than merely the same vocabulary.`}
              >
                <span>{(item.similarity * 100).toFixed(0)}%</span>
              </Tooltip>
            </td>
            <td>{item.differences.join('; ') || 'none above one point'}</td>
          </tr>)}
        </tbody>
      </table>}
      {references.implied_range && <p className="eagle-range">
        Implied range <b>{references.implied_range.lower}</b> –
        {' '}<b>{references.implied_range.upper}</b>, most likely
        {' '}<b>{references.implied_range.likely}</b>; this story reads as
        {' '}<b>{references.relative_assessment}</b> than its closest match.
      </p>}
    </div>

    {/* §29. Which layer failed, so the answer is not "retry with a bigger prompt". */}
    {failures.length > 0 && <div className="eagle-block">
      <h4><AlertTriangle size={13} /> Failure attribution</h4>
      <ul className="eagle-failures">
        {failures.map((item, index) => <li key={index}>
          <span className="eagle-layer">{item.layer.replaceAll('_', ' ')}</span>
          <span><b>{item.detail}</b><small>{item.remedy}</small></span>
        </li>)}
      </ul>
    </div>}
  </section>
}
