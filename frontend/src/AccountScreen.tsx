import { useEffect, useState } from 'react'
import { ArrowLeft, Check, Copy, ExternalLink, KeyRound, LogOut, Save, ShieldCheck, UserPlus, Users } from 'lucide-react'
import { api } from './api'
import { useAuth } from './AuthContext'
import type { ResourceShare, UserRole, WorkspaceUser } from './types'

type Tab = 'profile' | 'members' | 'sharing'

function resourceRoute(item: ResourceShare) {
  if (item.resource_type === 'conversation') return `#/chat/${item.resource_id}`
  if (item.resource_type === 'estimate') return `#/estimate/history/${item.resource_id}`
  return `#/activity/${item.resource_id}`
}

export function AccountScreen({ onHome }: { onHome: () => void }) {
  const { user, updateUser, requestLogout } = useAuth()
  const [tab, setTab] = useState<Tab>('profile')
  const [name, setName] = useState(user?.display_name || '')
  const [density, setDensity] = useState(user?.preferences.density || 'comfortable')
  const [defaultWorkspace, setDefaultWorkspace] = useState(user?.preferences.default_workspace || 'home')
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const [members, setMembers] = useState<WorkspaceUser[]>([])
  const [incoming, setIncoming] = useState<ResourceShare[]>([])
  const [outgoing, setOutgoing] = useState<ResourceShare[]>([])
  const [inviteEmail, setInviteEmail] = useState('')
  const [inviteRole, setInviteRole] = useState<'admin' | 'member'>('member')
  const [inviteLink, setInviteLink] = useState('')
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [passwordNotice, setPasswordNotice] = useState('')

  const canManage = user?.role === 'owner' || user?.role === 'admin'
  const load = async () => {
    try {
      const [received, granted] = await Promise.all([api.shares(true), api.shares(false)])
      setIncoming(received); setOutgoing(granted)
      if (canManage) setMembers(await api.users())
      setError('')
    } catch (cause) { setError((cause as Error).message) }
  }
  useEffect(() => { load() }, [canManage])

  const saveProfile = async (event: React.FormEvent) => {
    event.preventDefault(); setSaved(false); setError('')
    try {
      const updated = await api.updateMe({ display_name: name, preferences: {
        density, default_workspace: defaultWorkspace,
      } })
      updateUser(updated); setSaved(true)
    } catch (cause) { setError((cause as Error).message) }
  }

  const invite = async (event: React.FormEvent) => {
    event.preventDefault(); setError('')
    try {
      const result = await api.invite(inviteEmail, inviteRole)
      setInviteLink(`${window.location.origin}${window.location.pathname}${result.invite_route}`)
      setInviteEmail('')
    } catch (cause) { setError((cause as Error).message) }
  }

  const changePassword = async (event: React.FormEvent) => {
    event.preventDefault(); setError(''); setPasswordNotice('')
    try {
      const result = await api.changePassword(currentPassword, newPassword)
      setCurrentPassword(''); setNewPassword('')
      setPasswordNotice(
        `Password changed. ${result.other_sessions_revoked} other session${result.other_sessions_revoked === 1 ? '' : 's'} signed out.`,
      )
    } catch (cause) { setError((cause as Error).message) }
  }

  const updateMember = async (member: WorkspaceUser, patch: { role?: 'admin' | 'member'; active?: boolean }) => {
    try { await api.updateUser(member.id, patch); await load() }
    catch (cause) { setError((cause as Error).message) }
  }

  if (!user) return null
  return <div className="account-screen">
    <header className="account-header"><button onClick={onHome}><ArrowLeft/> Home</button><div><ShieldCheck/><span><b>Account & access</b><small>Identity, personalization, members, and sharing</small></span></div><button className="account-signout" onClick={requestLogout}><LogOut/> Sign out</button></header>
    <div className="account-layout">
      <aside className="account-nav">
        <div className="account-person"><span>{user.display_name.split(/\s+/).map(value => value[0]).join('').slice(0, 2)}</span><div><b>{user.display_name}</b><small>{user.email}</small><em>{user.role}</em></div></div>
        <nav><button className={tab === 'profile' ? 'active' : ''} onClick={() => setTab('profile')}><KeyRound/> Profile & preferences</button>{canManage && <button className={tab === 'members' ? 'active' : ''} onClick={() => setTab('members')}><Users/> Members & invitations</button>}<button className={tab === 'sharing' ? 'active' : ''} onClick={() => setTab('sharing')}><ShieldCheck/> Shared access</button></nav>
        <div className="access-evidence"><ShieldCheck/><span><b>Least privilege</b><small>New members receive only their own data until something is explicitly shared.</small></span></div>
      </aside>
      <main className="account-main">
        {error && <div className="auth-form-error" role="alert">{error}</div>}
        {tab === 'profile' && <section className="account-panel"><span className="eyebrow">PERSONAL WORKSPACE</span><h1>Profile & preferences</h1><p>These choices follow your account on this Devvy installation.</p><form onSubmit={saveProfile} className="settings-form"><label>Display name<input value={name} onChange={event => setName(event.target.value)} minLength={2}/></label><label>Email address<input value={user.email} disabled/><small>Contact an owner to change account identity.</small></label><div className="settings-row"><label>Interface density<select value={density} onChange={event => setDensity(event.target.value as 'comfortable' | 'compact')}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></label><label>Default workspace<select value={defaultWorkspace} onChange={event => setDefaultWorkspace(event.target.value)}><option value="home">Home</option><option value="chat">Chat</option><option value="estimate-code">Estimate Code</option><option value="activity">Activity</option></select></label></div>{saved && <div className="settings-saved"><Check/> Preferences saved</div>}<button className="auth-primary"><Save/> Save changes</button></form><form onSubmit={changePassword} className="settings-form password-settings"><span className="eyebrow">SECURITY</span><h2>Change password</h2><div className="settings-row"><label>Current password<input type="password" autoComplete="current-password" value={currentPassword} onChange={event => setCurrentPassword(event.target.value)} required/></label><label>New password<input type="password" autoComplete="new-password" value={newPassword} onChange={event => setNewPassword(event.target.value)} minLength={12} required/><small>Use at least 12 characters.</small></label></div>{passwordNotice && <div className="settings-saved"><Check/> {passwordNotice}</div>}<button className="auth-secondary"><KeyRound/> Change password</button></form><div className="security-facts"><div><b>Session</b><span>Opaque, revocable, HttpOnly cookie</span></div><div><b>Write protection</b><span>SameSite cookie plus CSRF token</span></div><div><b>Password</b><span>Salted scrypt hash; plaintext is never stored</span></div></div></section>}

        {tab === 'members' && <section className="account-panel"><span className="eyebrow">WORKSPACE ADMINISTRATION</span><h1>Members & invitations</h1><p>Invite people deliberately. Members see only what they own or what someone shares with them.</p><form className="invite-form" onSubmit={invite}><label>Email address<input type="email" value={inviteEmail} onChange={event => setInviteEmail(event.target.value)} placeholder="teammate@company.com" required/></label><label>Starting role<select value={inviteRole} onChange={event => setInviteRole(event.target.value as 'admin' | 'member')}><option value="member">Member</option><option value="admin">Administrator</option></select></label><button className="auth-primary"><UserPlus/> Create invitation</button></form>{inviteLink && <div className="invite-result"><span><b>Invitation ready</b><small>It expires in 7 days. Send it through a trusted channel.</small></span><code>{inviteLink}</code><button onClick={() => navigator.clipboard.writeText(inviteLink)}><Copy/> Copy link</button></div>}<div className="member-list"><span className="eyebrow">ACTIVE DIRECTORY</span>{members.map(member => <div key={member.id} className={!member.active ? 'inactive' : ''}><span className="member-avatar">{member.display_name[0]}</span><span><b>{member.display_name}{member.id === user.id ? ' (you)' : ''}</b><small>{member.email} · {member.active ? 'Active' : 'Deactivated'}</small></span><select value={member.role} disabled={member.role === 'owner' || member.id === user.id} onChange={event => updateMember(member, { role: event.target.value as Exclude<UserRole, 'owner'> })}><option value="owner">Owner</option><option value="admin">Admin</option><option value="member">Member</option></select>{member.role !== 'owner' && member.id !== user.id && <button onClick={() => updateMember(member, { active: !member.active })}>{member.active ? 'Deactivate' : 'Reactivate'}</button>}</div>)}</div></section>}

        {tab === 'sharing' && <section className="account-panel"><span className="eyebrow">RESOURCE ACCESS</span><h1>Shared access</h1><p>A complete view of evidence you received and access you granted.</p><div className="share-columns"><div><h2>Shared with me</h2>{incoming.length === 0 ? <p className="empty-copy">Nothing has been shared with you.</p> : incoming.map(item => <div className="share-record" key={item.id}><span><b>{item.resource_type}</b><small>From {item.owner?.display_name} · {item.permission}</small><code>{item.resource_id}</code></span><a href={resourceRoute(item)}><ExternalLink/> Open</a></div>)}</div><div><h2>Shared by me</h2>{outgoing.length === 0 ? <p className="empty-copy">You have not shared anything.</p> : outgoing.map(item => <div className="share-record" key={item.id}><span><b>{item.resource_type}</b><small>To {item.recipient?.display_name} · {item.permission}</small><code>{item.resource_id}</code></span><button onClick={async () => { await api.revokeShare(item.id); await load() }}>Revoke</button></div>)}</div></div></section>}
      </main>
    </div>
  </div>
}
