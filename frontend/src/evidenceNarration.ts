import type { AgentEvent } from './types'

/**
 * Plain-language narration for every pipeline stage.
 *
 * The evidence panel used to show a stage's raw measurements and nothing else — "Files
 * Considered 0, Budget 48000, Target Policy ranked retrieval". Those are the *readings*, not
 * the story: they tell you what a number was without telling you what the stage was doing,
 * why it matters, or whether zero is normal. A reader who did not write the pipeline cannot
 * reconstruct any of that from a key-value table.
 *
 * So each stage narrates itself in a sentence, with its numbers folded into the prose, and
 * the raw values stay underneath for anyone who wants to check the arithmetic. Evidence is
 * still evidence — it is now also readable.
 *
 * Narration is deliberately on the client. These are the same events the run already emitted;
 * describing them is a presentation concern, and keeping it here means the wording can improve
 * without changing what a run records or invalidating a stored trajectory.
 */

type Evidence = Record<string, unknown>

const num = (value: unknown): number | undefined =>
  typeof value === 'number' && Number.isFinite(value) ? value : undefined

const count = (value: unknown): number | undefined => {
  if (Array.isArray(value)) return value.length
  return num(value)
}

/** "1 file" / "3 files" — the plural rule every sentence below needs. */
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** Verb agreement: "1 touches" / "2 touch". Prose that says "1 touch" reads as a typo, and
 *  narration nobody trusts to be well written is not obviously trustworthy about anything else. */
const verb = (n: number, singular: string, plural_: string) => (n === 1 ? singular : plural_)

/** A non-empty string from evidence, or ''. Keeps sentences free of "undefined". */
const text = (value: unknown): string =>
  typeof value === 'string' && value.trim() && value !== 'none' ? value.trim() : ''

const list = (value: unknown, limit = 3): string => {
  if (!Array.isArray(value) || !value.length) return ''
  const shown = value.slice(0, limit).map(String)
  const rest = value.length - shown.length
  return shown.join(', ') + (rest > 0 ? ` and ${rest} more` : '')
}

/**
 * What a stage is doing, in one sentence.
 *
 * `running` describes the work in progress; anything terminal describes what came of it. Each
 * entry has access to the event's own evidence so the sentence carries the real figures rather
 * than a generic description sitting next to a table.
 */
const NARRATION: Record<
  string,
  (evidence: Evidence, status: string, event: AgentEvent) => string | undefined
> = {
  // -- Smart Code ---------------------------------------------------------------------
  classify: evidence => {
    const workspace = text(evidence.workspace)
    const mode = text(evidence.mode) || 'this'
    const named = Array.isArray(evidence.targets) ? evidence.targets : []
    const criteria = Array.isArray(evidence.acceptance_criteria)
      ? evidence.acceptance_criteria.length
      : 0
    return `Accepted a ${mode} request${workspace ? ` against ${workspace}` : ''} and fixed its `
      + 'boundaries before reading anything from disk. '
      + (named.length
        ? `You named ${plural(named.length, 'target file')} (${list(named)}), so only those are `
          + 'read or written.'
        : 'You named no target files, so Devvy will rank the repository and choose the most '
          + 'relevant ones itself.')
      + (evidence.risk_tier ? ` Risk tier: ${text(evidence.risk_tier)}.` : '')
      + (criteria ? ` ${plural(criteria, 'acceptance criterion', 'acceptance criteria')} to `
        + 'satisfy.' : '')
      + ' Nothing outside that workspace can be reached by this run.'
  },

  retrieve: evidence => {
    const considered = count(evidence.files_considered) ?? 0
    const included = count(evidence.files_included) ?? 0
    const characters = num(evidence.characters) ?? 0
    const budget = num(evidence.budget)
    const policy = evidence.target_policy === 'explicit allowlist'
      ? 'Only the files you named were read'
      : 'Devvy ranked the repository itself and read the files that looked most relevant'
    if (!considered) {
      return 'No source files were found in this workspace, so the model is working from your '
        + 'objective alone with no repository context. That is expected for an empty folder, and '
        + 'it means nothing in the result is grounded in existing code.'
    }
    const truncated = Array.isArray(evidence.truncated) ? evidence.truncated.length : 0
    const named = list(evidence.files_read, 4)
    const where = text(evidence.workspace)
    return (where ? `Read ${where}. ` : '')
      + `${policy}: ${plural(included, 'file')} of ${considered} considered, `
      + `${characters.toLocaleString()} characters`
      + (budget ? ` against a ${budget.toLocaleString()}-character budget` : '')
      + (truncated ? `. ${plural(truncated, 'file was', 'files were')} cut short to fit` : '')
      + (named ? `. The files read were ${named}` : '')
      + '. Everything read is labelled untrusted evidence, so instructions written inside your '
      + 'own code cannot redirect the change.'
  },

  plan: (evidence, status) => {
    if (status === 'running') {
      return 'Working out the smallest complete change that satisfies the objective, before '
        + 'writing any code — so the diff you review stays small enough to actually read.'
    }
    const files = Array.isArray(evidence.files) ? evidence.files : []
    if (!files.length) return 'Settled on an approach; the steps are listed in the result.'
    const tests = count(evidence.tests) ?? 0
    const docs = count(evidence.docs) ?? 0
    const extras = [
      tests ? plural(tests, 'test file') : '',
      docs ? plural(docs, 'documentation file') : '',
    ].filter(Boolean)
    return `The change needs ${plural(files.length, 'file')}: ${list(files, 6)}. `
      + (extras.length
        ? `That includes ${extras.join(' and ')}, so what comes out is installable and `
          + 'verifiable rather than only runnable on the machine that generated it. '
        : '')
      + 'Each is written and checked on its own, so one failure costs one file rather than '
      + 'the whole change.'
  },

  code: evidence => {
    const edits = count(evidence.edits) ?? 0
    const findings = count(evidence.findings) ?? 0
    if (!edits) {
      return `Produced no file to write${findings ? `, and ${plural(findings, 'review note')}` : ''}. `
        + 'Nothing can be applied from this run.'
    }
    const sizes = evidence.lines_per_file
    const detail = sizes && typeof sizes === 'object' && !Array.isArray(sizes)
      ? Object.entries(sizes as Record<string, unknown>)
          .slice(0, 6)
          .map(([path, lines]) => `${path} (${lines} lines)`)
          .join(', ')
      : list(evidence.edits, 6)
    return `Drafted ${plural(edits, 'complete file')}${detail ? `: ${detail}` : ''}. `
      + 'These are proposals held in memory — nothing has been written to your disk yet.'
  },

  generate: (evidence, status) => {
    if (status === 'running') {
      const attempt = num(evidence.attempt)
      return attempt && attempt > 1
        ? `Asking the model again (attempt ${attempt} of ${num(evidence.max_attempts) ?? 2}) after `
          + 'its previous answer did not fit the required shape.'
        : 'Asking the local model for its answer in a fixed JSON shape. On a CPU this is the slow '
          + 'part of the run — several minutes is normal, and it is not stuck.'
    }
    if (status === 'validated') {
      return 'The model returned output matching the required shape, so no repair was needed.'
    }
    if (status === 'retrying') {
      return evidence.truncated
        ? 'The answer was cut off at the output limit before it was complete, so Devvy is asking '
          + 'for a smaller one rather than replaying a half-written answer.'
        : 'The answer did not match the required shape. Devvy is asking once more, naming the '
          + 'exact problem — a generic "try again" makes a small model repeat itself.'
    }
    return undefined
  },

  verify: (evidence, status) => {
    const checks = count(evidence.checks) ?? 0
    const passed = count(evidence.passed) ?? 0
    if (status === 'retrying') {
      return 'The generated code does not parse. Devvy is showing the model the parser\'s own '
        + 'message and the offending line, and asking for a corrected file — one attempt only.'
    }
    if (!checks) {
      return 'There was no file to check, because the model did not produce one.'
    }
    const results = evidence.results
    const failed = results && typeof results === 'object' && !Array.isArray(results)
      ? Object.entries(results as Record<string, unknown>)
          .filter(([, value]) => value !== 'passed')
          .map(([path, value]) => `${path} — ${value}`)
      : []
    return passed === checks
      ? `Parsed all ${plural(checks, 'proposed file')} successfully. This confirms the code is `
        + 'syntactically valid; it does not run your tests or your build.'
      : `${checks - passed} of ${plural(checks, 'proposed file')} failed to parse`
        + (failed.length ? `: ${failed.slice(0, 3).join('; ')}` : '')
        + `. The other ${plural(passed, 'file')} passed, but nothing is written until every `
        + 'file parses — applying half a change is harder to undo than re-running.'
  },

  critique: evidence => {
    const findings = count(evidence.findings) ?? 0
    return findings
      ? `Recorded ${plural(findings, 'review finding')} against the proposal. These are noted for `
        + 'you, and do not by themselves block applying the change.'
      : 'Reviewed the proposal and raised nothing worth flagging.'
  },

  gate: (evidence, status) =>
    status === 'waiting' || evidence.can_apply
      ? 'Waiting for you. Nothing is written until you approve it, and approval requires the '
        + 'files to be unchanged since the preview and every check still passing.'
      : 'There is no write action to approve from this run.',

  apply: evidence =>
    `Wrote ${plural(count(evidence.files) ?? 0, 'approved file')} to disk, each replaced whole `
    + `rather than patched in place. ${evidence.backup === 'new files only'
      ? 'No backups were needed — every file was new.'
      : `The previous versions were copied to ${String(evidence.backup)} first.`}`,

  // -- Chat and Talk ------------------------------------------------------------------
  context: evidence => {
    const history = count(evidence.history_messages) ?? 0
    const attachments = count(evidence.attachments) ?? 0
    const characters = num(evidence.characters) ?? 0
    return `Assembled what the model will see: ${plural(history, 'earlier message')} from this `
      + `conversation${attachments ? ` and ${plural(attachments, 'attached document')} `
        + `(${characters.toLocaleString()} characters of extracted text)` : ' and no attachments'}. `
      + 'Attached text is marked untrusted, so instructions inside a document cannot take over.'
  },

  route: (evidence, status) =>
    status === 'running'
      ? 'Deciding which workflow answers this best — plain conversation, code, live research, an '
        + 'image, or your attached documents.'
      : `Chose the ${String(evidence.mode ?? 'chat')} workflow. The phrase that decided it is shown `
        + 'below, so the routing is a decision you can check rather than a black box.',

  research: (evidence, status) => {
    if (status === 'failed') {
      return 'Live web research did not return usable sources. The model has been told this '
        + 'explicitly and instructed to say so rather than invent an answer or cite pages it '
        + 'never read.'
    }
    const sources = count(evidence.sources) ?? 0
    return `Searched the public web and read ${plural(sources, 'page')}. Their URLs are listed `
      + 'below and are the citable basis for the answer — this is the only workflow that leaves '
      + 'your machine.'
  },

  media: evidence =>
    `Optional media finished: ${evidence.audio ? 'spoken audio was produced' : 'no audio'}`
    + `${evidence.animation ? ', and an animation was rendered' : ''}`
    + `${count(evidence.warnings) ? `, with ${plural(count(evidence.warnings) ?? 0, 'warning')}` : ''}. `
    + 'A media failure never discards the answer text itself.',

  finalize: evidence =>
    `Saved the reply to this machine (${num(evidence.response_characters)?.toLocaleString() ?? '0'} `
    + 'characters). It stays in your local history and is not sent anywhere.',

  // -- Estimate Code ------------------------------------------------------------------
  normalize: evidence =>
    `Turned the story into a stable set of evidence items (${count(evidence.evidence_items) ?? 0} of `
    + `them) and fingerprinted it, so the same story always produces the same starting point. `
    + `${evidence.untrusted_instructions_detected
      ? 'Text that looked like instructions was found in the story and neutralised.'
      : 'Nothing in the story text tried to give Devvy instructions.'}`,

  readiness: evidence =>
    `Checked whether the story is ready to estimate at all: ${plural(count(evidence.checks) ?? 0, 'check')}, `
    + `${plural(count(evidence.assumptions) ?? 0, 'assumption')} recorded, and `
    + `${plural(count(evidence.questions) ?? 0, 'open question')} worth asking before committing.`,

  specialist_routing: evidence =>
    `Routed the story to ${plural(count(evidence.specialists) ?? 0, 'specialist lens', 'specialist lenses')}`
    + `${list(evidence.specialists) ? ` (${list(evidence.specialists)})` : ''}, chosen from what the `
    + 'story actually involves rather than applied to every story alike.',

  assemble_context: evidence =>
    `Bounded the story evidence to ${num(evidence.characters)?.toLocaleString() ?? '0'} characters `
    + `from ${plural(count(evidence.sources) ?? 0, 'source')}`
    + `${evidence.truncated ? ', trimming what did not fit' : ''}. `
    + `${count(evidence.untrusted_sources) ? 'Third-party text is marked untrusted.' : ''}`,

  declare_stack: evidence =>
    `Loaded the calibration for the declared stack — maturity ${String(evidence.maturity ?? '?')}, `
    + `team experience ${String(evidence.team_experience ?? '?')} of 5. The same work on a different `
    + 'stack is deliberately a different number.',

  primary_estimate: (evidence, status) => {
    if (status === 'running') return undefined  // the generate narration covers the attempt
    const scored = count(evidence.model_scored) ?? 0
    const filled = count(evidence.heuristic_filled) ?? 0
    return `The model scored ${scored} of the 16 factors from the evidence`
      + `${filled ? `, and the ${filled} it skipped ${verb(filled, 'was', 'were')} filled from `
        + 'keyword heuristics and labelled as such' : ''}. `
      + 'It is never asked for the point value — that is arithmetic, not judgement.'
  },

  specialist_analysis: evidence =>
    `${plural(count(evidence.lenses) ?? 0, 'specialist lens', 'specialist lenses')} examined the `
    + `evidence, raising ${plural(count(evidence.material_risks) ?? 0, 'material risk')} and `
    + `${plural(count(evidence.open_questions) ?? 0, 'open question')}.`,

  blind_review: evidence => {
    if (evidence.executed === false) {
      return 'A second independent pass was not needed for this story, so it was skipped rather '
        + 'than spending several more minutes to confirm what the first pass already showed. '
        + `Reason: ${String(evidence.reason ?? 'not near a decision boundary')}.`
    }
    return 'A second estimator scored the same evidence without ever seeing the first pass\'s '
      + 'scores. That independence is what makes any disagreement between them meaningful.'
  },

  disagreement: evidence => {
    const material = count(evidence.material) ?? 0
    const guarded = count(evidence.protected) ?? 0
    return `Compared the two independent assessments: ${count(evidence.differences) ?? 0} differed, `
      + `of which ${material} ${verb(material, 'is', 'are')} material`
      + (guarded
        ? ` and ${guarded} ${verb(guarded, 'touches', 'touch')} a protected risk dimension`
        : '')
      + '.'
  },

  critic: evidence =>
    count(evidence.challenges)
      ? `Challenged ${plural(count(evidence.challenges) ?? 0, 'dimension')} where the two passes `
        + 'disagreed materially, so the resolution is argued rather than averaged.'
      : 'Found nothing worth challenging — the two passes substantially agreed.',

  arbitration: evidence =>
    `Resolved the differences using published rules rather than a judgement call: `
    + `${plural(count(evidence.decisions) ?? 0, 'decision')}`
    + `${count(evidence.human_approval_required)
      ? `, ${count(evidence.human_approval_required)} of which `
        + `${verb(count(evidence.human_approval_required) ?? 0, 'needs', 'need')} a person to confirm`
      : ''}.`,

  score_factors: evidence =>
    `Final scorecard assembled: ${count(evidence.model_scored) ?? 0} factors judged by the model and `
    + `${count(evidence.heuristic_filled) ?? 0} inferred from the story text. Every factor shows `
    + 'which it was, so you can tell judgement from a guess.',

  calculate: evidence => {
    const score = num(evidence.adjusted_score)
    const points = num(evidence.points)
    // A sentence built from missing figures reads "total ? landing in band ? mapping to ?
    // points", which is worse than the raw values it was meant to replace. Say nothing.
    if (score === undefined || points === undefined) return undefined
    return 'Ran the framework arithmetic in application code, not in the model: the scores total '
      + `${score} after adjustments, landing in band ${String(evidence.band ?? 'its band')}, which `
      + `maps to ${points} points. ${count(evidence.rules_fired) ?? 0} adjustment rules applied; `
      + 'you can replay every step by hand.'
  },

  policy_gate: evidence => {
    const failed = Array.isArray(evidence.gates_failed) ? evidence.gates_failed.length : 0
    return failed
      ? `${plural(failed, 'gate')} failed, which overrides the calculated number — the answer `
        + 'becomes an escalation rather than a smaller estimate.'
      : `All ${count(evidence.gates_evaluated) ?? 0} gates passed, so nothing overrides the `
        + `calculated points. Confidence is ${String(evidence.confidence ?? 'unknown')}.`
  },

  consistency_audit: evidence =>
    `Replayed the whole run and checked it against itself: ${String(evidence.status ?? 'checked')
      .replaceAll('_', ' ')}. This is the step that would catch the arithmetic disagreeing with `
    + 'the scorecard.',

  human_review: () =>
    'Every AI-assisted estimate ends here by design. The team owns the final number and may '
    + 'accept it, override it, buy the missing knowledge with a spike, or split the story.',

  // -- Failures -----------------------------------------------------------------------
  error: () =>
    'The run could not complete. What was produced before the failure is kept rather than '
    + 'discarded, and the reason is recorded above.',
}

/** Stages whose own label already reads as a sentence and needs no second one. */
const SELF_EXPLANATORY = new Set(['estimate'])

/**
 * One human sentence describing what a pipeline stage is doing or did.
 *
 * Returns undefined when there is nothing worth adding beyond the event's own label — a
 * narration that only restates the headline is noise, not explanation.
 */
export function narrate(event: AgentEvent): string | undefined {
  if (SELF_EXPLANATORY.has(event.stage)) return undefined
  const describe = NARRATION[event.stage]
  if (!describe) return undefined
  try {
    return describe((event.evidence ?? {}) as Evidence, event.status, event)
  } catch {
    // Narration is commentary. A malformed evidence payload must never cost the reader the
    // event itself, which is the thing that actually records what happened.
    return undefined
  }
}
