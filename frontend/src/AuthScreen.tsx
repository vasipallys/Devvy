import { useState } from 'react'
import { Eye, EyeOff, KeyRound, LockKeyhole, ShieldCheck, Sparkles, Users } from 'lucide-react'
import { api } from './api'
import type { AuthState } from './types'

function inviteFromHash() {
  const query = window.location.hash.split('?')[1] || ''
  return new URLSearchParams(query).get('invite') || ''
}

export function AuthScreen({ state, onAuthenticated }: {
  state: AuthState
  onAuthenticated: (state: AuthState) => void
}) {
  const invite = inviteFromHash()
  const [mode, setMode] = useState<'login' | 'register'>(state.needs_setup || invite ? 'register' : 'login')
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [visible, setVisible] = useState(false)
  const [remember, setRemember] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy(true); setError('')
    try {
      const result = mode === 'login'
        ? await api.login(email, password, remember)
        : await api.register({ display_name: name, email, password, invite_token: invite || undefined, remember })
      onAuthenticated(result)
      const destination = result.user?.preferences.default_workspace || 'home'
      window.location.hash = destination === 'home'
        ? '#/'
        : `#/${destination === 'estimate-code' ? 'estimate' : destination}`
    } catch (cause) { setError((cause as Error).message) }
    finally { setBusy(false) }
  }

  const setup = state.needs_setup
  return <div className="auth-screen">
    <div className="auth-ambient"/>
    <header className="auth-brand"><div className="brand-mark"><Sparkles/></div><span><b>Devvy</b><small>Evidence-based development</small></span></header>
    <main className="auth-layout">
      <section className="auth-story">
        <span className="auth-kicker"><ShieldCheck/> PRIVATE, PERSONAL, ACCOUNTABLE</span>
        <h1>{setup ? 'Create the workspace owner.' : 'Your evidence. Your activity. Your history.'}</h1>
        <p>{setup
          ? 'This first account becomes the workspace owner and securely claims the conversations, jobs, and estimates from your existing Devvy installation.'
          : 'Sign in to return to your own running work and history. Shared evidence stays explicit and permissioned.'}</p>
        <div className="auth-proof-grid">
          <div><LockKeyhole/><span><b>Local identity</b><small>Opaque sessions; passwords never leave this machine.</small></span></div>
          <div><Users/><span><b>Clear ownership</b><small>Every conversation, job, and estimate belongs to one user.</small></span></div>
          <div><KeyRound/><span><b>Explicit access</b><small>Share viewer or editor access, and revoke it at any time.</small></span></div>
        </div>
      </section>

      <section className="auth-card">
        <div className="auth-card-head">
          <span className="eyebrow">{setup ? 'FIRST-RUN SETUP' : invite ? 'WORKSPACE INVITATION' : 'SECURE WORKSPACE'}</span>
          <h2>{mode === 'login' ? 'Welcome back' : setup ? 'Set up Devvy' : 'Join the workspace'}</h2>
          <p>{mode === 'login' ? 'Sign in to continue to your private workspace.' : 'Create your personal account.'}</p>
        </div>
        <form onSubmit={submit}>
          {mode === 'register' && <label>Display name<input autoFocus autoComplete="name" value={name} onChange={event => setName(event.target.value)} required minLength={2}/></label>}
          <label>Email address<input autoFocus={mode === 'login'} type="email" autoComplete="email" value={email} onChange={event => setEmail(event.target.value)} required/></label>
          <label>Password<div className="password-field"><input type={visible ? 'text' : 'password'} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} value={password} onChange={event => setPassword(event.target.value)} required minLength={mode === 'register' ? 12 : undefined}/><button type="button" onClick={() => setVisible(value => !value)} aria-label={visible ? 'Hide password' : 'Show password'}>{visible ? <EyeOff/> : <Eye/>}</button></div>{mode === 'register' && <small>Use at least 12 characters. A memorable passphrase works well.</small>}</label>
          <label className="remember-check"><input type="checkbox" checked={remember} onChange={event => setRemember(event.target.checked)}/><span><b>Keep me signed in</b><small>Uses a revocable local session on this browser.</small></span></label>
          {error && <div className="auth-form-error" role="alert">{error}</div>}
          <button className="auth-primary" disabled={busy}>{busy ? 'Securing your account…' : mode === 'login' ? 'Sign in' : setup ? 'Create owner account' : 'Accept invitation'}</button>
        </form>
        {!setup && !invite && <div className="auth-switch">{mode === 'login'
          ? <>Have an invitation? <button onClick={() => setMode('register')}>Create account</button></>
          : <>Already a member? <button onClick={() => setMode('login')}>Sign in</button></>}</div>}
        <div className="auth-privacy"><LockKeyhole/><span>Credentials and sessions are stored only in your local Devvy data directory.</span></div>
      </section>
    </main>
  </div>
}
