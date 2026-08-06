import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
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
    let loaded = false
    const tick = async () => {
      if (disposed) return
      // A hidden tab has nobody to show the result to, and the work does not depend on
      // being watched — the whole point of the job runner. Polling it anyway is a request
      // every few seconds, forever, for a window that may stay in the background all day.
      // Visibility change wakes it immediately, so nothing is ever stale on return.
      //
      // The first fetch always runs, even hidden: a tab restored from a previous session
      // starts hidden, and skipping it would render an empty activity list under a heading
      // that says work is in flight.
      if (!loaded || !document.hidden) {
        loaded = true
        await refresh()
      }
      if (!disposed && (pollWhileIdle || activeRef.current > 0)) {
        timer = window.setTimeout(tick, activeRef.current > 0 ? POLL_MS : POLL_MS * 3)
      }
    }
    const onVisible = () => { if (!document.hidden) refresh() }
    document.addEventListener('visibilitychange', onVisible)
    tick()
    return () => {
      disposed = true
      document.removeEventListener('visibilitychange', onVisible)
      if (timer) window.clearTimeout(timer)
    }
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

  const running = useMemo(() => jobs.filter(job => isJobActive(job.status)), [jobs])
  const finished = useMemo(() => jobs.filter(job => !isJobActive(job.status)), [jobs])

  return { jobs, active, error, refresh, cancel, running, finished }
}
