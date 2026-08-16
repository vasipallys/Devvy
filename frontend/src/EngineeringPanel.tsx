import { AlertTriangle, Check, CircleSlash, FileMinus2, ListChecks, Scale, X } from 'lucide-react'
import { Tooltip } from './Tooltip'
import type { EngineeringReport } from './types'

/**
 * The engineering gates, shown as the account a reviewer actually needs.
 *
 * A diff answers "what changed". It cannot answer the two questions that decide whether a
 * change is safe to merge: *which requirement made each file necessary*, and *what was
 * considered and deliberately left alone*. Both are here, and the second matters more than it
 * looks — a change is only credibly minimal if you can see what it declined to touch.
 *
 * The decision block is deliberately incapable of saying APPROVED. Nothing generated here is
 * ever executed, so the build and test status are permanently NOT EXECUTED, and a judge that
 * cannot see a green build must not approve. Reporting that plainly is the feature.
 */

const STATUS_MEANING: Record<string, string> = {
  covered: 'A file implements this requirement and a test covers it.',
  untested: 'A file implements this requirement, but nothing tests it. The change may be '
    + 'correct — there is simply no evidence either way.',
  uncovered: 'Nothing in this change implements this requirement. This is the finding the '
    + 'matrix exists to surface: a requirement that was read and then forgotten.',
}

const DECISION_MEANING: Record<string, string> = {
  APPROVED: 'Every gate passed and the evidence supports merging.',
  NEEDS_FIX: 'Something decidable is incomplete — coverage, a finding, or evidence that was '
    + 'never produced. Approval requires a green build and green tests, and neither was run.',
  BLOCKED: 'Structural checks failed. A change that does not parse is not a change.',
}

export function EngineeringPanel({ report }: { report: EngineeringReport }) {
  const { requirements, necessity, traceability, decision } = report
  const all = [...requirements.functional, ...requirements.non_functional]
  const dropped = necessity.verdicts.filter(item => !item.necessary)

  return <section className="eng-panel">
    <header className="eng-head">
      <Scale size={16} />
      <span>
        <b>Engineering review</b>
        <small>{requirements.summary}</small>
      </span>
      <Tooltip label={decision.decision} detail={DECISION_MEANING[decision.decision]}>
        <span className={`eng-decision ${decision.decision.toLowerCase()}`}>
          {decision.decision.replace('_', ' ')}
          <em>{decision.requirement_coverage}% covered</em>
        </span>
      </Tooltip>
    </header>

    {/* Numbered requirements, so every later claim has something to point at. */}
    <div className="eng-block">
      <h4><ListChecks size={13} /> Requirements</h4>
      <ul className="eng-reqs">
        {all.map(item => <li key={item.id}>
          <Tooltip label={item.id} detail={`Read from ${item.source}.`}>
            <code>{item.id}</code>
          </Tooltip>
          <span>
            <b>{item.statement}</b>
            {item.acceptance.map(line => <small key={line}>✓ {line}</small>)}
          </span>
        </li>)}
      </ul>
      {requirements.assumptions.length > 0 && <div className="eng-assumptions">
        <h5>Recorded assumptions</h5>
        {requirements.assumptions.map(item => <p key={item.id}>
          <code>{item.id}</code> <b>{item.about}</b> — {item.assumed}
        </p>)}
      </div>}
      {requirements.open_questions.length > 0 && <details className="eng-questions">
        <summary>{requirements.open_questions.length} behaviour(s) the requirement did not define</summary>
        <ul>{requirements.open_questions.map(item => <li key={item}>{item}</li>)}</ul>
      </details>}
    </div>

    {/* Prove before modify. */}
    <div className="eng-block">
      <h4><Check size={13} /> Why each file changed</h4>
      <div className="scroll">
        <table className="eng-table">
          <thead><tr><th>File</th><th>Action</th><th>Requirement</th><th>Evidence</th></tr></thead>
          <tbody>
            {necessity.verdicts.filter(item => item.necessary).map(item => <tr key={item.path}>
              <td><code>{item.path}</code></td>
              <td><em className={`eng-action ${item.action}`}>{item.action}</em></td>
              <td>{item.requirement
                ? <code className="eng-req">{item.requirement}</code>
                : <span className="eng-unmatched">unmatched</span>}</td>
              <td>{item.evidence}</td>
            </tr>)}
          </tbody>
        </table>
      </div>

      {dropped.length > 0 && <div className="eng-dropped">
        <h5><X size={12} /> {dropped.length} proposed change(s) dropped as unnecessary</h5>
        <p>
          Relatedness is not necessity. A file being about the same subject as the requirement
          is not a reason to modify it — that is how a two-line change becomes a diff nobody
          reviews.
        </p>
        {dropped.map(item => <p key={item.path} className="eng-drop">
          <code>{item.path}</code>
          <small>{item.evidence}</small>
          <small className="eng-alt">{item.alternative}</small>
        </p>)}
      </div>}

      {necessity.reviewed_unchanged.length > 0 && <div className="eng-unchanged">
        <h5><FileMinus2 size={12} /> Reviewed and left alone ({necessity.reviewed_unchanged.length})</h5>
        <p>
          These ranked as part of the change surface and no proposed change needed them. It is
          the clearest evidence the change is minimal: what it declined to touch.
        </p>
        <div className="eng-paths">
          {necessity.reviewed_unchanged.map(item => <code key={item.path}>{item.path}</code>)}
        </div>
      </div>}
    </div>

    {/* Every requirement, including the ones nothing implements. */}
    <div className="eng-block">
      <h4><CircleSlash size={13} /> Traceability</h4>
      <div className="scroll">
        <table className="eng-table">
          <thead><tr><th>Requirement</th><th>Implementation</th><th>Tests</th><th>Status</th></tr></thead>
          <tbody>
            {traceability.map(row => <tr key={row.requirement} className={row.status}>
              <td>
                <code className="eng-req">{row.requirement}</code>
                <small>{row.statement}</small>
              </td>
              <td>{row.implementation.map(p => <code key={p}>{p}</code>)}
                {!row.implementation.length && <span className="eng-none">nothing</span>}</td>
              <td>{row.tests.map(p => <code key={p}>{p}</code>)}
                {!row.tests.length && <span className="eng-none">none</span>}</td>
              <td>
                <Tooltip label={row.status} detail={STATUS_MEANING[row.status]}>
                  <em className={`eng-status ${row.status}`}>{row.status}</em>
                </Tooltip>
              </td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </div>

    {/* The decision, and what it could not see. */}
    <div className="eng-block">
      <h4><AlertTriangle size={13} /> Decision</h4>
      <p className="eng-reasoning">{decision.reasoning}</p>
      <ul className="eng-evidence">
        <li><b>Build</b><span>{decision.build_status}</span></li>
        <li><b>Tests</b><span>{decision.test_status}</span></li>
        <li><b>Coverage</b><span>{decision.requirement_coverage}% of requirements traced to code and a test</span></li>
        <li><b>Ready for pull request</b><span>{decision.ready_for_pull_request ? 'yes' : 'no'}</span></li>
      </ul>
      {decision.critical_issues.length > 0 && <ul className="eng-issues">
        {decision.critical_issues.map(item => <li key={item}><AlertTriangle size={12} />{item}</li>)}
      </ul>}
    </div>
  </section>
}
