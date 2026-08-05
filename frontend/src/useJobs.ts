import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import { isJobActive, type JobSummary } from './types'

/** How often the activity list refreshes. Jobs are minutes long, so this is unobtrusive. */
const POLL_MS = 2500

/**
 * Tracks every background job and warns before the tab closes while work is in flight.
 *
 * The warning is a courtesy, not a safety mechanism: the work continues on the server
 * either way. It exists so a user does not walk away believing they cancelled something,
 * and so they know a result will be waiting when they return.
 */
export function useJobs(pollWhileIdle = true) {
  const [jobs, setJobs] = useState<JobSummary[]>([])
  const [active, setActive] = useState(0)
  const [error, setError] = useState('')
  const activeRef = useRef(0)

  const refresh = useCallback(async () => {
    try {
      const payload = await api.jobs()
      setJobs(payload.jobs)
      setActive(payload.active)
      activeRef.current = payload.active
      setError('')
    } catch (cause) {
      setError((cause as Error).message)
    }
  }, [])

  useEffect(() => {
    let disposed = false
    let timer: number | undefined
    const tick = async () => {
      if (disposed) return
      await refresh()
      // Poll faster while something is running so status feels live, and back off when idle.
      if (!disposed && (pollWhileIdle || activeRef.current > 0)) {
        timer = window.setTimeout(tick, activeRef.current > 0 ? POLL_MS : POLL_MS * 3)
      }
    }
    tick()
    return () => { disposed = true; if (timer) window.clearTimeout(timer) }
  }, [refresh, pollWhileIdle])

  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => {
      if (activeRef.current === 0) return
      // Browsers ignore custom text and show their own wording; returnValue is what
      // actually triggers the dialog.
      event.preventDefault()
      event.returnValue = ''
      return ''
    }
    window.addEventListener('beforeunload', guard)
    return () => window.removeEventListener('beforeunload', guard)
  }, [])

  const cancel = useCallback(async (id: string) => {
    try { await api.cancelJob(id); await refresh() }
    catch (cause) { setError((cause as Error).message) }
  }, [refresh])

  return {
    jobs,
    active,
    error,
    refresh,
    cancel,
    running: jobs.filter(job => isJobActive(job.status)),
    finished: jobs.filter(job => !isJobActive(job.status)),
  }
}
