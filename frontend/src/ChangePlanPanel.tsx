import { AlertTriangle, FileCode2, FilePlus2, FolderGit2, GitBranch, ShieldCheck } from 'lucide-react'
import { Tooltip } from './Tooltip'
import type { RepositoryReport } from './types'

/**
 * What the estimate found in the repository, and what it says would change.
 *
 * This is the half of an estimate a team can actually act on. A number tells you whether to
 * commit to the sprint; a change surface tells you where to start and who should look at it.
 *
 * The panel is built around one distinction, because it is the one that decides whether any of
 * this can be trusted: every path here was **found on disk**. The model was never asked to name
 * a file — it was shown a ranked list and asked what changes inside each. Paths it invented
 * anyway are reported as rejected rather than quietly dropped, because "the model named three
 * files that do not exist" is a fact about the estimate worth seeing.
 */

const SIGNAL_MEANING: Record<string, string> = {
  migrations: 'Migration tooling. Its presence means a schema change joins an existing chain '
    + 'rather than establishing one.',
  tests: 'A test tree. Its presence means a verification pattern exists to extend.',
  ci: 'A CI pipeline. Without one, verification and release for this change are manual.',
  containers: 'Container or deployment manifests.',
  docs: 'A documentation tree — somewhere for this change to be recorded.',
  infra: 'Infrastructure-as-code.',
  auth: 'Authentication or authorization code.',
  observability: 'Logging, metrics or tracing.',
  audit: 'Audit, consent or retention code — the machinery a compliance obligation needs.',
  api_surface: 'Routes, controllers or resolvers — the integration surface.',
  data_model: 'Models, entities or repositories.',
  feature_flags: 'Feature flags. Their presence means this change can be reversed without a deploy.',
  queues: 'Messaging infrastructure.',
}

export function ChangePlanPanel({ report }: { report: RepositoryReport }) {
  const { evidence, change_plan: plan, answered_factors: answered, counts } = report
  if (!evidence.reachable) {
    return <section className="repo-panel">
      <header className="repo-head">
        <FolderGit2 size={16} />
        <span><b>No repository analysed</b><small>{evidence.reason}</small></span>
      </header>
      <p className="repo-note">
        This estimate was made from the story text alone. Supplying a workspace path lets the
        codebase answer questions the story left open, instead of those questions being priced
        as unbounded.
      </p>
    </section>
  }

  const present = evidence.signals.filter(item => item.present)
  const absent = evidence.signals.filter(item => !item.present)

  return <section className="repo-panel">
    <header className="repo-head">
      <FolderGit2 size={16} />
      <span>
        <b>Repository evidence</b>
        <small>
          {evidence.root}
          {evidence.commit ? ` · ${evidence.commit.slice(0, 12)}` : ' · not a git checkout'}
        </small>
      </span>
      <span className="repo-scale">
        {counts.source_files} files · {counts.modules} modules · {counts.candidates} in the
        change surface
      </span>
    </header>

    <div className="repo-stack">
      {evidence.languages.map(item => <span key={item} className="repo-chip">{item}</span>)}
      {evidence.frameworks.map(item => <span key={item} className="repo-chip framework">{item}</span>)}
    </div>

    {/* What the repository settled that the story did not. */}
    {answered.length > 0 && <div className="repo-block">
      <h4><ShieldCheck size={13} /> The repository answered {answered.length} factor(s) the story left open</h4>
      <p className="repo-note">
        Each of these was an inferred score. The codebase replaced it with a fact — which is the
        difference between charging a team for what the story omitted and pricing what is
        actually there.
      </p>
      <ul className="repo-answers">
        {answered.map(item => <li key={item.factor}>
          <span className="repo-delta">
            <em>{item.was}</em><GitBranch size={11} /><b>{item.now}</b>
          </span>
          <span>
            <b>{item.factor.replaceAll('_', ' ')}</b>
            <small>{item.reason}</small>
            {item.evidence.length > 0 && <small className="repo-cite">{item.evidence.join(' · ')}</small>}
          </span>
        </li>)}
      </ul>
    </div>}

    {/* The deliverable: what to change, and what to create. */}
    <div className="repo-block">
      <h4>
        <FileCode2 size={13} /> Change plan
        <span className="repo-count">{plan.modified} modify · {plan.created} create</span>
      </h4>
      <p className="repo-note">{plan.note}</p>
      {plan.changes.length > 0
        ? <ul className="repo-changes">
          {plan.changes.map(item => <li key={item.path} className={item.action}>
            {item.action === 'create' ? <FilePlus2 size={13} /> : <FileCode2 size={13} />}
            <span>
              <b>{item.path}</b>
              <small>{item.detail}</small>
              <small className="repo-cite">{item.reason}</small>
            </span>
            <em className={`repo-action ${item.action}`}>{item.action}</em>
          </li>)}
        </ul>
        : <p className="repo-empty">
          No file in the repository matched this story's terms. Either the work is entirely new
          here, or the story describes a different system — both are worth confirming before
          committing to a number.
        </p>}

      {plan.rejected_paths.length > 0 && <p className="repo-rejected">
        <AlertTriangle size={13} />
        <span>
          <b>{plan.rejected_paths.length} proposed path(s) do not exist and were discarded</b>
          <small>{plan.rejected_paths.join(' · ')}</small>
          <small className="repo-cite">
            A file path is the easiest thing for a model to invent convincingly, so every one is
            checked against the repository before it is shown.
          </small>
        </span>
      </p>}
    </div>

    {/* Structural facts, present and absent — absence is the more useful half. */}
    <div className="repo-block">
      <h4>Structural signals</h4>
      <div className="repo-signals">
        {present.map(item => <Tooltip key={item.name} label={item.name.replaceAll('_', ' ')}
          detail={`${SIGNAL_MEANING[item.name] ?? 'A structural signal.'} Found in ${item.count} file(s): ${item.examples.join(', ')}`}>
          <span className="repo-signal present">{item.name.replaceAll('_', ' ')}<em>{item.count}</em></span>
        </Tooltip>)}
        {absent.map(item => <Tooltip key={item.name} label={`no ${item.name.replaceAll('_', ' ')}`}
          detail={`${SIGNAL_MEANING[item.name] ?? 'A structural signal.'} No file or directory in this repository is named for it — which is a finding, not a gap.`}>
          <span className="repo-signal absent">{item.name.replaceAll('_', ' ')}</span>
        </Tooltip>)}
      </div>
    </div>

    {evidence.related_tests.length > 0 && <div className="repo-block">
      <h4>Existing tests beside the change surface</h4>
      <div className="repo-signals">
        {evidence.related_tests.map(path => <span key={path} className="repo-signal present">{path}</span>)}
      </div>
    </div>}
  </section>
}
