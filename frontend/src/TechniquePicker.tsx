import { Tooltip } from './Tooltip'
import type { TechniqueDefinition, TechniqueId } from './types'

/**
 * Which estimation technique runs this session.
 *
 * The five genuinely differ — in precision, in wall-clock cost, and in the answer they can
 * arrive at — so the card shows all three rather than a name and a shrug. On a CPU model the
 * difference between "one model call" and "one per seat" is the difference between a minute
 * and ten, and a picker that hid that would be choosing for the user by omission.
 *
 * The definitions come from the backend, never a hardcoded list here: the picker cannot offer
 * a technique the service does not implement.
 */
export function TechniquePicker({ techniques, value, onChange, disabled }: {
  techniques: TechniqueDefinition[]
  value: TechniqueId
  onChange: (id: TechniqueId) => void
  disabled?: boolean
}) {
  if (!techniques.length) return null
  const chosen = techniques.find(item => item.id === value) ?? techniques[0]

  return <div className="technique-picker">
    <div className="technique-head">
      <span className="eyebrow">ESTIMATION TECHNIQUE</span>
      <p>
        How the squad estimates this story. Each technique reaches its number by a different
        published rule, and they can disagree — that disagreement is a finding, not a fault.
      </p>
    </div>

    <div className="technique-grid" role="radiogroup" aria-label="Estimation technique">
      {techniques.map(item => <Tooltip key={item.id} label={item.name} detail={item.how}>
        <button
          type="button"
          role="radio"
          aria-checked={item.id === value}
          className={`technique-card ${item.id === value ? 'selected' : ''}`}
          disabled={disabled}
          onClick={() => onChange(item.id)}
        >
          <b>{item.name}</b>
          <small>{item.tagline}</small>
          <span className="technique-meta">
            <em>{item.precision} precision</em>
            <em>{item.speed}</em>
          </span>
        </button>
      </Tooltip>)}
    </div>

    {/* The rule for the chosen technique, in full. It is what makes the number checkable, so
        it is shown rather than tucked behind a tooltip nobody opens. */}
    <div className="technique-rule">
      <h4>How {chosen.name} reaches a number</h4>
      <p>{chosen.rule}</p>
      <dl>
        <div><dt>Best for</dt><dd>{chosen.best_for}</dd></div>
        <div><dt>Model calls</dt><dd>{chosen.model_calls}</dd></div>
      </dl>
    </div>
  </div>
}
