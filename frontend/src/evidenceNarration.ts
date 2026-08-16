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

/**
 * The story's own material, appended under the sentence that explains the stage.
 *
 * A stage that says what it is *for* tells the reader something that was equally true before
 * they typed anything. What earns its place is what this stage found in *this* story — the
 * criteria it froze, the checks that came back unready, the rules that fired. Every stage
 * below appends through `detail`, so the shape is identical throughout.
 */
/** One evidence item as a phrase.
 *
 *  Structured evidence is the norm, not the exception — a numbered requirement arrives as
 *  `{id, statement, source}`, an assumption as `{id, about, assumed, because}`. Filtering to
 *  strings dropped every one of them, leaving the heading empty and the reader with the
 *  generic sentence these details exist to replace. */
const phrase = (item: unknown): string => {
  if (typeof item === 'string') return item.trim()
  if (!item || typeof item !== 'object') return ''
  const row = item as Record<string, unknown>
  const at = (key: string) => typeof row[key] === 'string' ? (row[key] as string).trim() : ''
  const body = ['statement', 'assumed', 'label', 'name', 'why', 'reason', 'path', 'text']
    .map(at).find(Boolean) ?? ''
  const about = at('about')
  return [at('id'), about && body ? `${about}: ${body}` : about || body]
    .filter(Boolean).join(' ').trim()
}

const detail = (heading: string, value: unknown, limit = 6): string => {
  const items = Array.isArray(value) ? value.map(phrase).filter(Boolean) : []
  if (!items.length) return ''
  const shown = items.slice(0, limit)
  const rest = items.length - shown.length
  return `\n\n${heading} — ${shown.join('   ')}${rest > 0 ? `   (+${rest} more)` : ''}`
}

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
      if (attempt && attempt > 1) {
        return `Asking the model again (attempt ${attempt} of ${num(evidence.max_attempts) ?? 2}) `
          + 'after its previous answer did not fit the required shape.'
      }
      // Chat and Talk share this stage name with the structured workflows but ask for nothing
      // of the sort: they stream free-form prose. Telling a voice user their spoken answer is
      // being requested "in a fixed JSON shape" describes a different product.
      return evidence.attempt === undefined
        ? 'Composing the answer, token by token, from the evidence gathered above. On a CPU '
          + 'model this is the slow part of the turn — a minute or two is normal, and it is not '
          + 'stuck.'
        : 'Asking the local model for its answer in a fixed JSON shape. On a CPU this is the '
          + 'slow part of the run — several minutes is normal, and it is not stuck.'
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

  // -- YUKTI (Talk) -----------------------------------------------------------------------

  faculty: (evidence, _status, event) =>
    'This turn asked for something YUKTI is not wired to. It was declined in code, before the '
    + 'model saw the question — a small model told not to describe a screen it cannot see will '
    + 'still occasionally describe one, and spoken aloud there is nothing for you to check it '
    + 'against.'
    + detail('Said instead', [String(event.detail ?? '')])
    + detail('Connected', evidence.connected, 8)
    + detail('Not connected', evidence.not_connected, 8),

  second_brain: evidence => {
    const notes = count(evidence.notes) ?? 0
    const memories = count(evidence.memories) ?? 0
    return `Read your own material before answering: ${plural(notes, 'note')} and `
      + `${plural(memories, 'remembered fact')}. Where a note and a web result disagree about `
      + 'your project, the note wins — it is yours, and it is specific.'
      + detail('Notes', evidence.notes, 5)
      + detail('Matched on', evidence.matched_terms, 8)
      + detail('Recalled', evidence.memories, 5)
  },

  memory: (evidence, _status, event) =>
    'Stored this because you asked for it in so many words. Nothing is inferred into the '
    + 'memory bank: a memory you did not state would be recalled months later with all the '
    + 'authority of something you did.'
    + detail('Remembered', [String(evidence.content ?? event.detail ?? '')]),

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
      : 'Nothing in the story text tried to give Devvy instructions.'}`
    + detail('Not supplied', evidence.missing_fields),

  readiness: evidence =>
    `Checked whether the story is ready to estimate at all: ${plural(count(evidence.checks) ?? 0, 'check')}, `
    + `${plural(count(evidence.assumptions) ?? 0, 'assumption')} recorded, and `
    + `${plural(count(evidence.questions) ?? 0, 'open question')} worth asking before committing.`
    + detail('Not ready', Array.isArray(evidence.unready)
      ? (evidence.unready as { area: string; status: string; detail: string }[])
          .map(item => `${item.area} (${item.status}): ${item.detail}`)
      : [])
    + detail('Worth asking', evidence.questions),

  specialist_routing: evidence => {
    const roles = Array.isArray(evidence.roles)
      ? (evidence.roles as { role: string; owns: string[]; why: string }[]) : []
    return `Routed the story to `
      + plural(count(evidence.specialists) ?? 0, 'specialist lens', 'specialist lenses')
      + ', chosen from what the story actually involves rather than applied to every '
      + 'story alike.'
      + (roles.length
        ? `\n\nRoles — `
          + roles.map(item => `${item.role} owns ${item.owns.join(' and ')}`).join('   ')
        : '')
  },

  assemble_context: evidence =>
    `Bounded the story evidence to ${num(evidence.characters)?.toLocaleString() ?? '0'} characters `
    + `from ${plural(count(evidence.sources) ?? 0, 'source')}`
    + `${evidence.truncated ? ', trimming what did not fit' : ''}. `
    + `${count(evidence.untrusted_sources) ? 'Third-party text is marked untrusted.' : ''}`
    + detail('Included', evidence.included),

  declare_stack: evidence =>
    `Loaded the calibration for the declared stack — maturity ${String(evidence.maturity ?? '?')}, `
    + `team experience ${String(evidence.team_experience ?? '?')} of 5. The same work on a different `
    + 'stack is deliberately a different number.'
    + detail('Anchors', evidence.anchors),

  primary_estimate: (evidence, status) => {
    if (status === 'running') return undefined  // the generate narration covers the attempt
    const scored = count(evidence.model_scored) ?? 0
    const filled = count(evidence.heuristic_filled) ?? 0
    return `The model scored ${scored} of the 16 factors from the evidence`
      + `${filled ? `, and the ${filled} it skipped ${verb(filled, 'was', 'were')} filled from `
        + 'keyword heuristics and labelled as such' : ''}. `
      + 'It is never asked for the point value — that is arithmetic, not judgement.'
      + detail('Cross-check', num(evidence.point_cross_check)
        ? [`the model's own reading maps to ${num(evidence.point_cross_check)} points`] : [])
  },

  specialist_analysis: evidence =>
    `${plural(count(evidence.lenses) ?? 0, 'specialist lens', 'specialist lenses')} examined the `
    + `evidence, raising ${plural(count(evidence.material_risks) ?? 0, 'material risk')} and `
    + `${plural(count(evidence.open_questions) ?? 0, 'open question')}.`
    + detail('Raised', evidence.risks),

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
      + detail('Where they differ', evidence.where)
  },

  critic: evidence =>
    (count(evidence.challenges)
      ? `Challenged ${plural(count(evidence.challenges) ?? 0, 'dimension')} where the two passes `
        + 'disagreed materially, so the resolution is argued rather than averaged.'
      : 'Found nothing worth challenging — the two passes substantially agreed.')
      + detail('Challenged', evidence.raised),

  arbitration: evidence =>
    `Resolved the differences using published rules rather than a judgement call: `
    + `${plural(count(evidence.decisions) ?? 0, 'decision')}`
    + `${count(evidence.human_approval_required)
      ? `, ${count(evidence.human_approval_required)} of which `
        + `${verb(count(evidence.human_approval_required) ?? 0, 'needs', 'need')} a person to confirm`
      : ''}.`
    + detail('Resolved', evidence.resolved),

  // -- EAGLE governance -------------------------------------------------------------------

  requirements: evidence =>
    `Read ${plural(count(evidence.functional) ?? 0, 'functional requirement')} and `
    + `${plural(count(evidence.non_functional) ?? 0, 'non-functional requirement')} out of the `
    + 'story, each numbered so a score can point at what asked for it. Anything the story did '
    + 'not define becomes a named gap rather than a general complaint about clarity.'
    + detail('Functional', evidence.functional)
    + detail('Non-functional', evidence.non_functional)
    + detail('Assumed', evidence.assumptions)
    + detail('Not defined by the story', evidence.open_questions, 4),

  // -- Repository evidence, when a workspace was supplied ---------------------------------

  repo_intelligence: (evidence, status) => (status === 'failed'
    ? 'Could not read the repository at the path given, so every question the story leaves '
      + 'open stays open. Unanswered questions are priced as unbounded, which is why an '
      + 'estimate without a codebase runs higher than one with it.'
    : `Read the codebase this story lands in: `
      + `${plural(count(evidence.source_files) ?? 0, 'source file')}`
      + `${text(evidence.commit) ? ` at commit ${text(evidence.commit).slice(0, 8)}` : ''}, `
      + `and ranked ${plural(count(evidence.change_surface) ?? 0, 'file')} as the surface this `
      + 'story would touch. What the repository can answer, the model is not asked to guess.')
    + detail('Stack', [...(evidence.languages as string[] ?? []),
      ...(evidence.frameworks as string[] ?? [])])
    + detail('Already present', evidence.signals_present, 8)
    + detail('Not found in this repository', evidence.signals_absent, 8)
    + detail('Change surface', evidence.change_surface, 8)
    + detail('Tests beside it', evidence.related_tests, 5),

  repo_answers: evidence =>
    `Replaced ${plural(count(evidence.factors) ?? 0, 'inferred score')} with a fact read from `
    + 'disk. A factor the story never mentions is not automatically unknown — the codebase may '
    + 'already settle it, and a score that came from a file beats one that came from silence. '
    + 'Scores the model grounded in the story itself were left alone.'
    + detail('Now grounded in the repository', evidence.changes, 8),

  change_plan: evidence => {
    const modify = count(evidence.modify) ?? 0
    const create = count(evidence.create) ?? 0
    return (modify + create
      ? `Named the work: ${plural(modify, 'existing file')} to change and `
        + `${plural(create, 'new file')} to add. Every path was checked against disk before it `
        + 'was allowed into the plan, so the estimate is sized against files that exist.'
      : 'Could not verify a change surface for this story, so the estimate is sized from the '
        + 'story text alone and the unknowns stay priced as unknowns.')
      + detail('Change', evidence.modify, 8)
      + detail('Create', evidence.create, 6)
      + detail('Rejected — no such path', evidence.rejected, 5)
  },

  contract: evidence => {
    // The generic sentence first, then what was actually sealed. A stage that explains what
    // "sealing a contract" means without quoting the contract tells the reader something that
    // was equally true before they typed anything.
    const criteria = Array.isArray(evidence.acceptance_criteria)
      ? (evidence.acceptance_criteria as string[]) : []
    const stack = evidence.stack && typeof evidence.stack === 'object'
      ? Object.entries(evidence.stack as Record<string, string>)
          .map(([layer, value]) => `${layer} ${value}`).join(', ')
      : ''
    const objective = text(evidence.objective)
    return `Sealed the estimation contract for ${text(evidence.story_id) || 'this story'}: `
      + `objective, acceptance criteria, stack and roles are now fixed for the run, so nothing `
      + `can move underneath the estimate while it is being made. `
      + `${plural(count(evidence.required_evidence) ?? 0, 'kind')} of evidence `
      + `${verb(count(evidence.required_evidence) ?? 0, 'is', 'are')} required before publishing, `
      + `and the run may spend at most ${num(evidence.max_debate_rounds) ?? 2} debate round(s).`
      + (objective ? `\n\nObjective — ${objective}` : '')
      + (criteria.length
        ? `\n\nAcceptance criteria (${criteria.length}) — `
          + criteria.map((item, index) => `${index + 1}. ${item}`).join('   ')
        : `\n\nAcceptance criteria — none were supplied, so there is nothing to `
          + 'verify against.')
      + (stack
        ? `\n\nStack — ${stack}.`
        : `\n\nStack — none declared, so no calibration applies.`)
      + (text(evidence.affected_application)
        && evidence.affected_application !== 'unspecified'
        ? `\n\nApplication — ${text(evidence.affected_application)}.` : '')
  },

  eagle_conflict: evidence => {
    const disputed = count(evidence.disputed) ?? 0
    const estimators = num(evidence.estimator_count) ?? 0
    if (!disputed) {
      return `The ${plural(estimators, 'independent assessment')} agreed on every factor — no `
        + 'spread of two or more, and no elevated score left standing without evidence.'
    }
    return `Compared ${plural(estimators, 'independent assessment')} factor by factor and found `
      + `${plural(disputed, 'disputed factor')}${list(evidence.disputed) ? ` (${list(evidence.disputed)})` : ''}. `
      + `A spread of two or more disputes, and so does an elevated score with nothing behind it — `
      + `which is what stops a missing answer settling quietly on a middling number.`
      + detail('Disputed', evidence.disputed)
      + detail('Owed by', evidence.owners)
  },

  eagle_review: evidence => {
    const blocker = num(evidence.blocker) ?? 0
    const material = num(evidence.material) ?? 0
    const advisory = num(evidence.advisory) ?? 0
    const total = blocker + material + advisory
    if (!total) {
      return 'The critic, the adversarial reviewer and the optimistic reviewer all passed: '
        + 'nothing was found that was missed, under-counted, or counted twice.'
    }
    return `Three reviewers argued against the estimate from opposite directions and raised `
      + `${plural(total, 'finding')}`
      + `${blocker ? `, ${blocker} blocking` : ''}${material ? `, ${material} material` : ''}. `
      + 'The adversarial pass looks only for reasons this is too low; the optimistic pass only '
      + 'for complexity counted twice — so neither can inflate the number unopposed.'
      + detail('Found', evidence.raised, 4)
      + detail('Suggested', evidence.corrections, 4)
  },

  eagle_debate: (evidence, status) => {
    const factors = count(evidence.factors) ?? 0
    const unresolved = count(evidence.unresolved) ?? 0
    const base = `Re-examined ${plural(factors, 'disputed factor')} only — the rest of the `
      + 'pipeline was not re-run, because one contested score is not a reason to re-do the work '
      + `that was already agreed. Bounded at ${num(evidence.max_rounds) ?? 2} rounds.`
    if (status === 'waiting' || unresolved) {
      return `${base} ${plural(unresolved, 'factor')} still `
        + `${verb(unresolved, 'has', 'have')} no agreed score; further rounds would not converge, `
        + 'so this goes to a human specialist.'
        + detail('Debated', evidence.factors)
        + detail('Unresolved', evidence.unresolved)
    }
    return base + detail('Debated', evidence.factors)
  },

  eagle_validation: evidence => {
    const failed = count(evidence.failed_rules) ?? 0
    const gate = text(evidence.spike_gate)
    const triggers = count(evidence.spike_triggers) ?? 0
    const rules = failed
      ? `${plural(failed, 'validation rule')} failed${list(evidence.failed_rules) ? `: ${list(evidence.failed_rules)}` : ''}. `
        + 'The estimate is still shown, but it has not satisfied its own contract.'
      : 'Every deterministic rule passed: sixteen factors, all in range, every elevated score '
        + 'evidenced, and the adjustments still reconcile to the adjusted total.'
    if (gate && gate !== 'PROCEED') {
      return `${rules} The spike gate then fired on ${plural(triggers, 'rule')} and returned `
        + `${gate.replaceAll('_', ' ').toLowerCase()} — refusing to estimate is a valid answer, `
        + 'and a more honest one than a number nobody can support.'
        + detail('Spike triggers', evidence.spike_triggers)
        + detail('Failed rules', evidence.failed_rules)
    }
    return `${rules} No spike rule fired, so the story is safe to estimate as written.`
      + detail('Failed rules', evidence.failed_rules)
  },

  eagle_reference: evidence => {
    const matches = count(evidence.matches) ?? 0
    if (!matches) {
      return 'Nothing comparable exists in history yet, so this estimate has no anchor. The '
        + 'first estimates for a stack are the ones most worth reviewing, not the ones to trust.'
    }
    const relative = text(evidence.relative) || 'similar'
    const range = evidence.implied_range as { lower?: number; upper?: number } | undefined
    const best = (evidence.matches as { similarity?: number; points?: number }[] | undefined)?.[0]
    // The event's own detail repeats this sentence almost word for word, so it is deliberately
    // not appended: narration that says the same thing twice reads as a bug, not as emphasis.
    return `Anchored against ${plural(matches, 'historical story', 'historical stories')} that `
      + `${verb(matches, 'was', 'were')} the same shape of work, not merely the same words — `
      + `similarity compares all sixteen factor scores, so a story matches on how it was built `
      + `rather than on its vocabulary. `
      + `${best?.similarity !== undefined
        ? `The closest is ${(best.similarity * 100).toFixed(0)}% similar at ${best.points} points, and t`
        : 'T'}`
      + `his story reads as ${relative}`
      + `${range?.lower !== undefined ? `, implying ${range.lower}–${range.upper} points` : ''}.`
      + detail('Compared against', Array.isArray(evidence.matches)
        ? (evidence.matches as { title: string; points: number; similarity: number }[])
            .map(item => `${item.title} — ${item.points} pts, ${(item.similarity * 100).toFixed(0)}%`)
        : [])
  },

  focus_pass: (evidence, status) => {
    if (status === 'failed') {
      return 'The model could not answer the simpler question either, so the scorecard falls '
        + 'back to keyword evidence from the story text. Every inferred factor is labelled as '
        + 'such rather than presented as judgement.'
    }
    const touched = count(evidence.touched) ?? 0
    const largest = count(evidence.largest) ?? 0
    const unclear = count(evidence.unclear) ?? 0
    return `Asked the model a question it can actually answer — which factors this story `
      + `touches, which are biggest, which it left unanswered — rather than sixteen scored `
      + `objects in one response. It read ${plural(touched, 'factor')} as involved, `
      + `${largest} as largest and ${plural(unclear, 'as unanswered', 'as unanswered')}. `
      + 'The model judged; the arithmetic stayed in code.'
  },

  score_factors: evidence =>
    `Final scorecard assembled: ${count(evidence.model_scored) ?? 0} factors judged by the model and `
    + `${count(evidence.heuristic_filled) ?? 0} inferred from the story text. Every factor shows `
    + 'which it was, so you can tell judgement from a guess.'
    + detail('Costs most', evidence.highest, 4)
    + detail('Costs least', evidence.lowest, 3),

  calculate: evidence => {
    const score = num(evidence.adjusted_score)
    const points = num(evidence.points)
    // A sentence built from missing figures reads "total ? landing in band ? mapping to ?
    // points", which is worse than the raw values it was meant to replace. Say nothing.
    if (score === undefined || points === undefined) return undefined
    return 'Ran the framework arithmetic in application code, not in the model: the scores total '
      + `${score} after adjustments, landing in band ${String(evidence.band ?? 'its band')}, which `
      + `maps to ${points} points. ${plural(count(evidence.rules_fired) ?? 0, 'adjustment rule')} `
      + 'applied; '
      + 'you can replay every step by hand.'
      + detail('Adjustments that fired', evidence.applied)
  },

  policy_gate: evidence => {
    const failed = Array.isArray(evidence.gates_failed) ? evidence.gates_failed.length : 0
    return (failed
      ? `${plural(failed, 'gate')} failed, which overrides the calculated number — the answer `
        + 'becomes an escalation rather than a smaller estimate.'
      : `All ${count(evidence.gates_evaluated) ?? 0} gates passed, so nothing overrides the `
        + `calculated points. Confidence is ${String(evidence.confidence ?? 'unknown')}.`)
      + detail('Failed', evidence.failed_detail)
      + detail('Risk flags', evidence.flags)
      + detail('Why that confidence', text(evidence.confidence_detail)
        ? [text(evidence.confidence_detail)] : [])
  },

  consistency_audit: evidence =>
    `Replayed the whole run and checked it against itself: ${String(evidence.status ?? 'checked')
      .replaceAll('_', ' ')}. This is the step that would catch the arithmetic disagreeing with `
    + 'the scorecard.'
    + detail('Warnings', evidence.warnings),

  human_review: () =>
    'Every AI-assisted estimate ends here by design. The team owns the final number and may '
    + 'accept it, override it, buy the missing knowledge with a spike, or split the story.',

  // -- Failures -----------------------------------------------------------------------
  error: () =>
    'The run could not complete. What was produced before the failure is kept rather than '
    + 'discarded, and the reason is recorded above.',
}

/** Stages whose own label already reads as a sentence and needs no second one. */
/**
 * Narration for a checklist *step*, where several steps share one event.
 *
 * The arithmetic is one indivisible operation and emits one event, but a reader thinks of it as
 * three things: sum what the factors triggered, then what the stack triggered, then map the
 * total onto the ladder. Rendering one sentence against all three says the pipeline has nothing
 * to tell you about two of them — and the same applied to the two gate steps.
 *
 * Anything not split here falls through to the stage narration unchanged, which is true for
 * twenty of the twenty-five steps.
 */
const STEP_NARRATION: Record<string, (evidence: Evidence) => string | undefined> = {
  apply_base_adjustments: evidence => {
    const applied = Array.isArray(evidence.base_applied) ? evidence.base_applied.length : 0
    const total = num(evidence.base_total) ?? 0
    return `The sixteen scores total ${num(evidence.base_sum) ?? 0}. `
      + (applied
        ? `${plural(applied, 'base rule')} then fired for ${total >= 0 ? '+' : ''}${total} — `
          + 'these are compounding effects the framework prices explicitly, not extra judgement '
          + 'about the story.'
        : 'No base adjustment applied: nothing in the scorecard crossed a threshold.')
      + detail('Fired', evidence.base_applied)
      + detail('Evaluated and did not fire', evidence.base_skipped, 5)
  },

  apply_stack_adjustments: evidence => {
    const applied = Array.isArray(evidence.stack_applied) ? evidence.stack_applied.length : 0
    const total = num(evidence.stack_total) ?? 0
    return (applied
      ? `${plural(applied, 'stack rule')} fired for ${total >= 0 ? '+' : ''}${total}. The same `
        + 'work costs differently on different stacks, and this is where that is applied — from '
        + 'the profile you declared, not from an impression of it.'
      : 'No stack adjustment applied: the declared profile carries no maturity, experience or '
        + 'boundary penalty for this story.')
      + detail('Fired', evidence.stack_applied)
      + detail('Evaluated and did not fire', evidence.stack_skipped, 5)
  },

  map_to_fibonacci: evidence => {
    const score = num(evidence.adjusted_score)
    const points = num(evidence.points)
    if (score === undefined || points === undefined) return undefined
    return `${score} lands in band ${String(evidence.band ?? '?')}, which maps to ${points} `
      + 'points. The ladder is deliberately coarse: the gap between 8 and 13 is where a team '
      + 'should be arguing, and a scale offering 9, 10 and 11 would invite false precision.'
      + (evidence.cap_exceeded
        ? detail('Cap', [`the declared framework maturity caps this at ${num(evidence.cap) ?? '?'} `
          + 'points; the mapped value is reported as-is and the recommendation escalates instead'])
        : '')
  },

  evaluate_gates: evidence => {
    const failed = Array.isArray(evidence.gates_failed) ? evidence.gates_failed.length : 0
    return `${count(evidence.gates_evaluated) ?? 0} gates were evaluated on this run. `
      + (failed
        ? `${plural(failed, 'gate')} failed, and a failed gate overrides the calculated number `
          + 'rather than adjusting it.'
        : 'All passed, so nothing overrides the calculated points.')
      + detail('Failed', evidence.failed_detail)
      + detail('Passed', evidence.gates_passed, 8)
  },

  decide: evidence => {
    const recommendation = text(evidence.recommendation)
    if (!recommendation) return undefined
    return `The framework's recommendation is ${recommendation}. It comes from the gates, the `
      + 'points and the uncertainty score together, not from an opinion about the story.'
      + detail('Why', text(evidence.recommendation_detail)
        ? [text(evidence.recommendation_detail)] : [])
      + detail('Confidence', text(evidence.confidence)
        ? [`${text(evidence.confidence)} — ${text(evidence.confidence_detail)}`] : [])
      + detail('Risk flags', evidence.flags)
  },
}

/** One sentence for a checklist step, falling back to the stage narration where they are one. */
export function narrateStep(step: string, event: AgentEvent): string | undefined {
  const describe = STEP_NARRATION[step]
  if (!describe) return narrate(event)
  try {
    return describe((event.evidence ?? {}) as Evidence)
  } catch {
    // Narration is commentary; a malformed payload must never cost the reader the step itself.
    return undefined
  }
}

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
