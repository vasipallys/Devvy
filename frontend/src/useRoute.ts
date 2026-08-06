import { useCallback, useEffect, useState } from 'react'

/**
 * Minimal hash router.
 *
 * Pages were selected by in-memory state, which meant no back button, no reload-safety, and
 * — for a tool whose whole value is a defensible estimate — no way to send a colleague a link
 * to one. A hash route fixes all three without a routing dependency or server rewrite rules,
 * and keeps working when `dist/` is served as plain static files from any path.
 *
 * Routes:
 *   #/                          home
 *   #/chat                      chat, newest conversation
 *   #/chat/{conversationId}     a specific conversation
 *   #/talk  #/smart-code        those workspaces
 *   #/estimate                  new estimate
 *   #/estimate/history          estimate history
 *   #/estimate/history/{id}     one stored estimate
 *   #/activity                  request activity
 */

export type Page = 'home' | 'chat' | 'talk' | 'smart-code' | 'estimate-code' | 'activity'

export interface Route {
  page: Page
  /** Conversation id on #/chat/…, estimate record id on #/estimate/history/… */
  id?: string
  /** Sub-view within a page, currently only `history` for Estimate Code. */
  view?: string
}

const PAGES: Record<string, Page> = {
  '': 'home',
  chat: 'chat',
  talk: 'talk',
  'smart-code': 'smart-code',
  estimate: 'estimate-code',
  activity: 'activity',
}

export function parseRoute(hash: string): Route {
  const segments = hash.replace(/^#\/?/, '').split('/').filter(Boolean)
  const page = PAGES[segments[0] ?? ''] ?? 'home'
  if (page === 'chat') return { page, id: segments[1] }
  if (page === 'estimate-code' && segments[1] === 'history') {
    return { page, view: 'history', id: segments[2] }
  }
  return { page }
}

export function buildRoute(route: Route): string {
  if (route.page === 'home') return '#/'
  if (route.page === 'chat') return route.id ? `#/chat/${route.id}` : '#/chat'
  if (route.page === 'estimate-code') {
    if (route.view !== 'history') return '#/estimate'
    return route.id ? `#/estimate/history/${route.id}` : '#/estimate/history'
  }
  const slug = Object.entries(PAGES).find(([, value]) => value === route.page)?.[0]
  return `#/${slug ?? ''}`
}

export function useRoute() {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash))

  useEffect(() => {
    const onChange = () => setRoute(parseRoute(window.location.hash))
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])

  /** Navigate by writing the hash, so the browser records history and Back works. */
  const navigate = useCallback((next: Route, replace = false) => {
    const target = buildRoute(next)
    if (window.location.hash === target) return
    if (replace) window.history.replaceState(null, '', target)
    else window.location.hash = target
    setRoute(parseRoute(target))
  }, [])

  return { route, navigate }
}
