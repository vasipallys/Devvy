import { AlertTriangle, Check, GitCompareArrows, Users } from 'lucide-react'
import { Tooltip } from './Tooltip'
import type { TechniqueOutcome } from './types'

/**
 * What the session actually did — the room, the cards, and the rule that settled it.
 *
 * A story point is a number somebody has to defend in a planning meeting, and the two things
 * that make it defensible are who said what and which rule turned that into the figure. Both
 * are here in full. The steps are the same list the backend recorded, not a summary of it, so
 * the arithmetic can be replayed by hand from this panel alone.
 */

const CONSENSUS: Record<string, { label: string; why: string }> = {
  unanimous: {
    label: 'Unanimous',
    why: 'Every discipline played the same card. The strongest agreement this technique can show.',
  },
  consensus: {
    label: 'Consensus',
    why: 'The cards sat within one step of each other on the Fibonacci ladder — close enough '
      + 'that a second round could not change the band.',
  },
  converged: {
    label: 'Converged',
    why: 'The room started apart. The outliers heard the reasoning they disagreed with and '
      + 'played again.',
  },
  unresolved: {
    label: 'Unresolved',
    why: 'The squad did not converge. The number stands and is flagged — a persistent '
      + 'disagreement is information about the story, not an averaging problem.',
  },
  'n/a': {
    label: 'No cards',
    why: 'This technique does not produce individual cards, so there is no spread to measure.',
  },
}

export function TechniquePanel({ outcome }: { outcome: TechniqueOutcome }) {
  const cards = outcome.votes.filter(item => item.points !== null)
  const dotted = outcome.votes.filter(item => item.dots.length > 0)
  const heatmap = (outcome.detail?.heatmap ?? []) as { label: string; dots: number }[]
  const consensus = CONSENSUS[outcome.consensus] ?? CONSENSUS['n/a']

  return <section className="technique-panel">
    <header>
      <Users size={16} />
      <span>
        <b>{outcome.name}</b>
        <small>{outcome.verdict}</small>
      </span>
      <Tooltip label={`${outcome.points} points`} detail={outcome.definition.rule}>
        <span className="technique-points">{outcome.points}</span>
      </Tooltip>
    </header>

    <div className="technique-summary">
      <Tooltip label={consensus.label} detail={consensus.why}>
        <span className={`technique-chip ${outcome.consensus}`}>{consensus.label}</span>
      </Tooltip>
      {cards.length > 0 && <Tooltip
        label={`Spread ${outcome.spread}`}
        detail={
          'Disagreement measured in steps on the Fibonacci ladder rather than in points. '
          + '21 to 34 is thirteen points and one step; 3 to 5 is two points and also one step.'
        }>
        <span className="technique-chip">{outcome.spread} step spread</span>
      </Tooltip>}
      {outcome.rounds > 1 && <Tooltip
        label={`${outcome.rounds} rounds`}
        detail="Only the seats holding the extreme cards were re-polled. Re-asking everyone when two people disagree costs minutes and moves nothing.">
        <span className="technique-chip">{outcome.rounds} rounds</span>
      </Tooltip>}
      {outcome.divergence !== 0 && <Tooltip
        label="Diverges from the factor arithmetic"
        detail={
          `This technique reached ${outcome.points} where the 16-factor calculation reached `
          + `${outcome.framework_points}. Both are shown; neither is quietly discarded — the `
          + 'gap is where a quick judgement and a scored one part company.'
        }>
        <span className="technique-chip diverged">
          <GitCompareArrows size={12} />
          {outcome.points} vs {outcome.framework_points} framework
        </span>
      </Tooltip>}
      {outcome.needs_human && <Tooltip
        label="Confirm this one"
        detail="Something in this session did not settle cleanly. The number stands, and it is marked rather than presented as though the room agreed.">
        <span className="technique-chip warn"><AlertTriangle size={12} /> Confirm</span>
      </Tooltip>}
    </div>

    {/* The room. Who said what, and whether they actually spoke. */}
    {cards.length > 0 && <div className="technique-block">
      <h4>The table</h4>
      <div className="scroll">
        <table className="technique-table">
          <thead><tr><th>Discipline</th><th>Owns</th><th>Card</th><th>What they said</th></tr></thead>
          <tbody>
            {cards.map(vote => <tr key={vote.role} className={vote.inferred ? 'absent' : ''}>
              <td><b>{vote.label}</b><small>{vote.discipline}</small></td>
              <td>{vote.owns.map(item => <code key={item}>{item}</code>)}</td>
              <td>
                <span className="technique-card-value">{vote.points}</span>
                {vote.revised_from !== null && vote.revised_from !== vote.points && <em>
                  was {vote.revised_from}
                </em>}
              </td>
              <td>
                {vote.inferred
                  ? <span className="technique-absent">
                      Did not answer — the baseline stood in, so these dimensions are not a
                      first-hand judgement.
                    </span>
                  : vote.reasoning}
              </td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </div>}

    {/* Dot voting: where the concern concentrated. */}
    {heatmap.length > 0 && <div className="technique-block">
      <h4>Where the dots went</h4>
      <p className="technique-note">
        Each seat had {outcome.detail?.dots_per_member} dots. Scarcity is the mechanism — three
        dots force a ranking in a way that rating sixteen dimensions does not.
      </p>
      <ul className="dot-heatmap">
        {heatmap.map(row => <li key={row.label}>
          <span className="dot-label">{row.label}</span>
          <span className="dot-bar" style={{ '--dots': row.dots } as React.CSSProperties}>
            {Array.from({ length: row.dots }, (_, index) => <i key={index} />)}
          </span>
          <em>{row.dots}</em>
        </li>)}
      </ul>
      {dotted.length > 0 && <div className="dot-voices">
        {dotted.map(vote => <p key={vote.role}>
          <b>{vote.label}</b> {vote.reasoning}
        </p>)}
      </div>}
    </div>}

    {/* The rules, in the order they fired. This is the replayable part. */}
    <div className="technique-block">
      <h4>How the number was reached</h4>
      <ol className="technique-steps">
        {outcome.steps.map((step, index) => <li key={index}>
          <Check size={12} aria-hidden />{step}
        </li>)}
      </ol>
      <p className="technique-note facilitator">{outcome.facilitator_note}</p>
    </div>
  </section>
}
