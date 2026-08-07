import { LogOut, Settings, UserRound } from 'lucide-react'
import { useState } from 'react'
import { useAuth } from './AuthContext'

export function AccountDock({ onAccount }: { onAccount: () => void }) {
  const { user, authEnabled, requestLogout } = useAuth()
  const [open, setOpen] = useState(false)
  if (!authEnabled || !user) return null
  const initials = user.display_name.split(/\s+/).map(value => value[0]).join('').slice(0, 2).toUpperCase()
  return <div className="account-dock">
    {open && <div className="account-popover">
      <div className="account-identity"><span>{initials}</span><div><b>{user.display_name}</b><small>{user.email}</small><em>{user.role}</em></div></div>
      <button onClick={() => { setOpen(false); onAccount() }}><Settings/> Account & access</button>
      <button onClick={() => { setOpen(false); requestLogout() }}><LogOut/> Sign out</button>
    </div>}
    <button className="account-avatar" onClick={() => setOpen(value => !value)} aria-expanded={open} aria-label="Open account menu">
      <span>{initials}</span><UserRound/>
    </button>
  </div>
}

