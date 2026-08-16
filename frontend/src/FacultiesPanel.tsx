import { useEffect, useState } from 'react'
import { Check, ChevronDown, Slash } from 'lucide-react'
import { api } from './api'
import { Tooltip } from './Tooltip'
import type { Faculty } from './types'

/**
 * What YUKTI can and — as prominently — cannot do.
 *
 * Every other screen in this application lets you discover a limit by looking: a disabled
 * button, an empty panel, a missing tab. Voice has none of that. A listener has no menu to
 * find something absent from, so an assistant's limits are the one thing they cannot work out
 * for themselves, and the only honest place to put them is in front of them before they ask.
 *
 * The disconnected half is not greyed-out filler. Each entry carries the exact sentence YUKTI
 * says when asked, so what you read here is what you will hear — no gap between the interface's
 * account of the assistant and the assistant's account of itself.
 */
export function FacultiesPanel() {
  const [faculties, setFaculties] = useState<Faculty[]>([])
  const [vault, setVault] = useState(false)
  const [open, setOpen] = useState(false)

  useEffect(() => {
    let live = true
    api.systemStatus()
      .then(status => {
        if (!live || !status.yukti) return
        setFaculties(status.yukti.faculties)
        setVault(status.yukti.vault_configured)
      })
      .catch(() => undefined)  // A missing register must not cost the user the voice screen.
    return () => { live = false }
  }, [])

  if (!faculties.length) return null
  const connected = faculties.filter(item => item.connected)
  const missing = faculties.filter(item => !item.connected)

  return <section className={`faculties ${open ? 'open' : ''}`}>
    <button
      type="button"
      className="faculties-head"
      aria-expanded={open}
      onClick={() => setOpen(value => !value)}
    >
      <span className="faculties-count">
        <b>{connected.length} faculties</b>
        <small>{missing.length} not connected</small>
      </span>
      <ChevronDown size={15} className="faculties-chevron" aria-hidden />
    </button>

    {/* Collapsed, the counts alone already say the honest thing: some of this is missing. */}
    <div className="faculties-body" hidden={!open}>
      <ul className="faculty-list">
        {connected.map(item => <li key={item.id} className="on">
          <Tooltip label={item.title} detail={item.summary}>
            <span><Check size={13} aria-hidden />{item.title}</span>
          </Tooltip>
        </li>)}
      </ul>

      <h4>Not connected</h4>
      <p className="faculties-note">
        YUKTI declines these rather than improvising them. What you read here is what you will
        hear if you ask.
      </p>
      <ul className="faculty-list">
        {missing.map(item => <li key={item.id} className="off">
          <Tooltip label={item.title} detail={item.why_not}>
            <span><Slash size={13} aria-hidden />{item.title}</span>
          </Tooltip>
        </li>)}
      </ul>

      {!vault && <p className="faculties-note warn">
        No notes vault is configured, so the second brain has nothing to read. Set
        <code>YUKTI_VAULT_ROOT</code> in <code>.env</code> to point at your notes directory.
      </p>}
    </div>
  </section>
}
