import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, LoaderCircle, LogOut, ShieldCheck, Square, X } from 'lucide-react'
import { api } from './api'
import { AuthScreen } from './AuthScreen'
import type { AuthState, WorkspaceUser } from './types'

type AuthContextValue = {
  user: WorkspaceUser | null
  authEnabled: boolean
  refresh: () => Promise<void>
  updateUser: (user: WorkspaceUser) => void
  requestLogout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function useAuth() {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}

function LoadingAuth() {
  return <div className="auth-screen"><div className="auth-loading" role="status">
    <LoaderCircle className="spin"/><b>Securing your workspace</b><span>Checking your local session…</span>
  </div></div>
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<AuthState>()
  const [loadError, setLoadError] = useState('')
  const [logoutOpen, setLogoutOpen] = useState(false)
  const [activeJobs, setActiveJobs] = useState(0)
  const [logoutBusy, setLogoutBusy] = useState(false)

  const refresh = useCallback(async () => {
    try { setSession(await api.authSession()); setLoadError('') }
    catch (cause) { setLoadError((cause as Error).message) }
  }, [])

  useEffect(() => {
    refresh()
    const expired = () => { refresh() }
    window.addEventListener('devvy:authentication-required', expired)
    return () => window.removeEventListener('devvy:authentication-required', expired)
  }, [refresh])

  useEffect(() => {
    const density = session?.user?.preferences.density || 'comfortable'
    document.documentElement.dataset.density = density
  }, [session?.user?.preferences.density])

  const requestLogout = useCallback(async () => {
    try {
      const jobs = await api.jobs()
      setActiveJobs(jobs.active)
    } catch { setActiveJobs(0) }
    setLogoutOpen(true)
  }, [])

  const finishLogout = async (action: 'keep' | 'cancel') => {
    setLogoutBusy(true)
    try {
      await api.logout(action)
      setSession(current => current ? { ...current, authenticated: false, user: null } : current)
      setLogoutOpen(false)
      window.location.hash = '#/'
    } finally { setLogoutBusy(false) }
  }

  const value = useMemo<AuthContextValue>(() => ({
    user: session?.user ?? null,
    authEnabled: session?.auth_enabled ?? true,
    refresh,
    updateUser: user => setSession(current => current ? { ...current, user } : current),
    requestLogout,
  }), [session, refresh, requestLogout])

  if (!session && !loadError) return <LoadingAuth />
  if (!session && loadError) return <div className="auth-screen"><div className="auth-card auth-error-card">
    <AlertTriangle/><h1>Devvy could not verify your session</h1><p>{loadError}</p>
    <button className="auth-primary" onClick={refresh}>Try again</button>
  </div></div>
  if (session && !session.authenticated) {
    return <AuthScreen state={session} onAuthenticated={setSession} />
  }

  return <AuthContext.Provider value={value}>
    {children}
    {logoutOpen && <div className="modal-scrim" role="presentation">
      <section className="logout-dialog" role="dialog" aria-modal="true" aria-labelledby="logout-title">
        <button className="modal-close" onClick={() => setLogoutOpen(false)} aria-label="Close"><X/></button>
        <div className="dialog-icon"><LogOut/></div>
        <span className="eyebrow">SIGN OUT SAFELY</span>
        <h2 id="logout-title">{activeJobs ? `${activeJobs} request${activeJobs === 1 ? '' : 's'} still running` : 'Sign out of Devvy?'}</h2>
        <p>{activeJobs
          ? 'Your requests belong to your account, not this browser session. Choose what should happen before Devvy signs you out.'
          : 'Your local history remains attached to your account and will be here next time.'}</p>
        {activeJobs > 0 && <div className="logout-choice-evidence">
          <ShieldCheck/><span><b>Keep running is safe</b><small>Work continues in the durable queue. Sign in later to see the result.</small></span>
        </div>}
        <div className="logout-actions">
          {activeJobs > 0 && <button disabled={logoutBusy} className="auth-primary" onClick={() => finishLogout('keep')}>
            <ShieldCheck/> Keep running & sign out
          </button>}
          {activeJobs > 0 && <button disabled={logoutBusy} className="auth-danger" onClick={() => finishLogout('cancel')}>
            <Square/> Stop requests & sign out
          </button>}
          {activeJobs === 0 && <button disabled={logoutBusy} className="auth-primary" onClick={() => finishLogout('keep')}>
            <LogOut/> Sign out
          </button>}
          <button disabled={logoutBusy} className="auth-secondary" onClick={() => setLogoutOpen(false)}>Stay signed in</button>
        </div>
      </section>
    </div>}
  </AuthContext.Provider>
}

