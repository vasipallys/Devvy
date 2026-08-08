import {
  createContext, useCallback, useContext, useMemo, useRef, useState, useSyncExternalStore,
  type CSSProperties, type ReactNode,
} from 'react'
import {
  ChevronLeft, ChevronRight, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen,
} from 'lucide-react'
import { Tooltip } from './Tooltip'

/**
 * Dockable, resizable side panels.
 *
 * Every screen here had its side panels nailed to a fixed pixel width chosen once, by me, for
 * a window size I happened to have open. That is a guess about someone else's monitor: the
 * same 304px evidence panel is a third of a 1024px laptop and a sliver of an ultrawide, and
 * the person reading a 200-line diff wants the opposite trade from the person watching a
 * pipeline tick over. So the widths belong to the reader, and they persist.
 *
 * Three behaviours, deliberately kept small:
 *
 *   **Resize** by dragging the divider, which is a real focusable separator — arrow keys move
 *   it, Home and End jump to the limits, Enter collapses. A resize handle reachable only by
 *   mouse is a resize handle half the users do not have, and this pattern (WAI-ARIA's window
 *   splitter) is what assistive tech already expects.
 *
 *   **Dock** to either edge. Which side a panel belongs on is genuinely personal — evidence on
 *   the right reads as a footnote, on the left as a table of contents — and the swap is an
 *   `order` change, so no DOM moves, no state resets, and focus stays where it was.
 *
 *   **Collapse** to a labelled strip rather than to nothing. A panel that vanishes leaves the
 *   reader hunting the header for whatever brings it back; a 30px strip with its own name on
 *   it is one click, and it keeps the layout's shape visible.
 *
 * Below a threshold the pane stops displacing content and floats over it instead — three
 * columns cannot be honest on a phone, and shrinking all of them to fit is how you get a
 * layout where nothing is usable rather than one thing being hidden.
 *
 * The preference is stored per browser, not per account, and that is on purpose: it describes
 * the screen you are sitting at, not who you are. It is still keyed by user id so two people
 * sharing a machine do not inherit each other's layout mid-session.
 */

export type DockSide = 'left' | 'right'

interface DockState {
  width: number
  side: DockSide
  collapsed: boolean
}

export interface DockOptions {
  /** Which edge the panel starts on. The reader may move it. */
  side?: DockSide
  width?: number
  min?: number
  max?: number
  collapsed?: boolean
  /** Below this viewport width the panel floats over the content instead of displacing it. */
  overlayBelow?: number
  /** Hidden entirely below this width — for panels that are navigation, not content. */
  hideBelow?: number
}

export interface Dock {
  id: string
  side: DockSide
  width: number
  collapsed: boolean
  /** True when the panel is floating over the content rather than sitting beside it. */
  overlay: boolean
  /** True when the viewport is too narrow for this panel at all. */
  hidden: boolean
  min: number
  max: number
  defaultWidth: number
  setSide: (side: DockSide) => void
  setWidth: (width: number) => void
  setCollapsed: (collapsed: boolean) => void
  toggle: () => void
  reset: () => void
}

const STORE = 'devvy.layout.v1'
const KEYBOARD_STEP = 24

/**
 * Who the stored layout belongs to.
 *
 * A context rather than a `useAuth()` call, because the only thing docking needs from the
 * session is a string to namespace a key by. Reaching into the auth context for that would
 * make every panel in the application unmountable without a signed-in session — including in
 * a test — to answer a question that is one string wide. The default keeps a bare `<DockPane>`
 * working on its own.
 */
const DockScopeContext = createContext('local')

export function DockScope({ value, children }: { value: string; children: ReactNode }) {
  return <DockScopeContext.Provider value={value}>{children}</DockScopeContext.Provider>
}

function readStore(): Record<string, DockState> {
  try {
    const raw = window.localStorage.getItem(STORE)
    const parsed = raw ? JSON.parse(raw) : null
    return parsed && typeof parsed === 'object' ? parsed as Record<string, DockState> : {}
  } catch {
    // A corrupt or unavailable store is not worth failing a render over — private-mode
    // browsers throw on localStorage access, and the defaults are perfectly usable.
    return {}
  }
}

function writeStore(key: string, state: DockState) {
  try {
    const all = readStore()
    all[key] = state
    window.localStorage.setItem(STORE, JSON.stringify(all))
  } catch { /* storage unavailable or full; the layout still works for this session */ }
}

/** Subscribes to a media query without an effect, so the first render already knows. */
function useMatches(query: string): boolean {
  const list = useMemo(() => window.matchMedia(query), [query])
  const subscribe = useCallback((notify: () => void) => {
    list.addEventListener('change', notify)
    return () => { list.removeEventListener('change', notify) }
  }, [list])
  return useSyncExternalStore(subscribe, () => list.matches)
}

export function useDock(id: string, options: DockOptions = {}): Dock {
  const scope = useContext(DockScopeContext)
  const {
    side: defaultSide = 'right', width: defaultWidth = 320, min = 220, max = 620,
    collapsed: defaultCollapsed = false, overlayBelow = 720, hideBelow = 0,
  } = options
  const key = `${scope}:${id}`

  const clamp = useCallback(
    (value: number) => Math.min(max, Math.max(min, Math.round(value))),
    [min, max],
  )

  const [state, setState] = useState<DockState>(() => {
    const stored = readStore()[key]
    return {
      width: clamp(stored?.width ?? defaultWidth),
      side: stored?.side === 'left' || stored?.side === 'right' ? stored.side : defaultSide,
      collapsed: typeof stored?.collapsed === 'boolean' ? stored.collapsed : defaultCollapsed,
    }
  })

  const commit = useCallback((update: Partial<DockState>) => {
    setState(current => {
      const next = { ...current, ...update }
      writeStore(key, next)
      return next
    })
  }, [key])

  const overlay = useMatches(`(max-width:${overlayBelow}px)`)
  const hidden = useMatches(`(max-width:${Math.max(hideBelow, 1)}px)`) && hideBelow > 0

  return {
    id,
    side: state.side,
    width: state.width,
    collapsed: state.collapsed,
    overlay,
    hidden,
    min,
    max,
    defaultWidth,
    setSide: side => commit({ side }),
    setWidth: width => commit({ width: clamp(width) }),
    setCollapsed: collapsed => commit({ collapsed }),
    toggle: () => setState(current => {
      const next = { ...current, collapsed: !current.collapsed }
      writeStore(key, next)
      return next
    }),
    reset: () => commit({ width: clamp(defaultWidth), side: defaultSide, collapsed: false }),
  }
}

/**
 * The divider. It is a separator in the ARIA sense, not decoration: focusable, announced with
 * its current width, and movable from the keyboard.
 */
function Splitter({ dock, label, order }: { dock: Dock; label: string; order: number }) {
  const handleRef = useRef<HTMLDivElement>(null)
  const draggingRef = useRef(false)

  function shellRect(): DOMRect | undefined {
    return handleRef.current?.closest('.dock-shell')?.getBoundingClientRect()
  }

  function widthAt(clientX: number): number {
    const rect = shellRect()
    if (!rect) return dock.width
    return dock.side === 'left' ? clientX - rect.left : rect.right - clientX
  }

  return <div
    ref={handleRef}
    className={`dock-splitter ${dock.side}`}
    style={{ order }}
    role="separator"
    tabIndex={0}
    aria-orientation="vertical"
    aria-label={`Resize ${label}`}
    aria-controls={dock.id}
    aria-valuenow={dock.width}
    aria-valuemin={dock.min}
    aria-valuemax={dock.max}
    onPointerDown={event => {
      // Pointer capture, so a fast drag that leaves the 8px handle keeps resizing instead of
      // dropping the moment the cursor outruns it.
      event.currentTarget.setPointerCapture(event.pointerId)
      draggingRef.current = true
      document.body.classList.add('dock-resizing')
    }}
    onPointerMove={event => {
      if (!draggingRef.current) return
      dock.setWidth(widthAt(event.clientX))
    }}
    onPointerUp={event => {
      draggingRef.current = false
      event.currentTarget.releasePointerCapture(event.pointerId)
      document.body.classList.remove('dock-resizing')
    }}
    onPointerCancel={() => {
      draggingRef.current = false
      document.body.classList.remove('dock-resizing')
    }}
    onDoubleClick={() => dock.setWidth(dock.defaultWidth)}
    onKeyDown={event => {
      // Arrow keys move the divider in the direction pressed, which means the sign flips with
      // the side: on a right-docked panel, "left" makes it bigger.
      const grow = dock.side === 'left' ? 'ArrowRight' : 'ArrowLeft'
      const shrink = dock.side === 'left' ? 'ArrowLeft' : 'ArrowRight'
      if (event.key === grow) dock.setWidth(dock.width + KEYBOARD_STEP)
      else if (event.key === shrink) dock.setWidth(dock.width - KEYBOARD_STEP)
      else if (event.key === 'Home') dock.setWidth(dock.min)
      else if (event.key === 'End') dock.setWidth(dock.max)
      else if (event.key === 'Enter' || event.key === ' ') dock.setCollapsed(true)
      else return
      event.preventDefault()
    }}
  ><i /></div>
}

/** Dock-side and collapse controls, shown in the panel's own header. */
function DockControls({ dock, label }: { dock: Dock; label: string }) {
  const other: DockSide = dock.side === 'left' ? 'right' : 'left'
  return <span className="dock-controls">
    <Tooltip
      label={`Move ${label} to the ${other}`}
      detail="Which edge a panel belongs on is a matter of how you read: on the right it is a
        footnote to the main column, on the left it is a table of contents for it."
    >
      <button className="dock-btn" onClick={() => dock.setSide(other)}
        aria-label={`Move ${label} to the ${other}`}>
        {other === 'left' ? <ChevronLeft size={14} /> : <ChevronRight size={14} />}
      </button>
    </Tooltip>
    <Tooltip label={`Collapse ${label}`} detail="Collapses to a labelled strip on the same edge,
      so it is one click to bring back and the layout keeps its shape.">
      <button className="dock-btn" onClick={dock.toggle} aria-label={`Collapse ${label}`}>
        {dock.side === 'left' ? <PanelLeftClose size={14} /> : <PanelRightClose size={14} />}
      </button>
    </Tooltip>
  </span>
}

/**
 * A docked panel: the pane, its divider, and — when collapsed — the strip that brings it back.
 *
 * `order` puts the pieces on the right side of the centre column without moving any DOM, so
 * docking left or right neither remounts the contents nor loses focus or scroll position.
 */
export function DockPane({ dock, label, icon, children, className = '' }: {
  dock: Dock
  /** Human name, used in the header, the strip and every control's accessible name. */
  label: string
  icon?: ReactNode
  children: ReactNode
  className?: string
}) {
  if (dock.hidden) return null

  const paneOrder = dock.side === 'left' ? -2 : 2
  const splitOrder = dock.side === 'left' ? -1 : 1

  if (dock.collapsed) {
    return <button
      className={`dock-strip ${dock.side}`}
      style={{ order: paneOrder }}
      onClick={dock.toggle}
      aria-expanded={false}
      /* No aria-controls: the pane is unmounted while collapsed, and pointing at an id that
         is not in the document is worse than saying nothing. */
    >
      {dock.side === 'left' ? <PanelLeftOpen size={15} /> : <PanelRightOpen size={15} />}
      <span>{label}</span>
    </button>
  }

  const style: CSSProperties = dock.overlay
    ? { order: paneOrder, width: `min(${dock.width}px, 92vw)` }
    : { order: paneOrder, width: dock.width }

  return <>
    {dock.overlay && <button
      className="dock-scrim"
      aria-label={`Close ${label}`}
      onClick={dock.toggle}
    />}
    <aside
      id={dock.id}
      className={`dock-pane ${dock.side} ${dock.overlay ? 'floating' : ''} ${className}`}
      style={style}
      aria-label={label}
    >
      <div className="dock-head">
        {icon}
        <b>{label}</b>
        <DockControls dock={dock} label={label} />
      </div>
      <div className="dock-body">{children}</div>
    </aside>
    {/* No wrapper around the splitter: `display:contents` would leave `order` on an element
        that generates no box, and the divider would drift to whichever end of the flex line
        the DOM happened to put it. */}
    {!dock.overlay && <Splitter dock={dock} label={label} order={splitOrder} />}
  </>
}

/** The header control that brings a hidden panel back, with its progress readable in place. */
export function DockToggle({ dock, label, children }: {
  dock: Dock
  label: string
  children?: ReactNode
}) {
  if (dock.hidden) return null
  return <Tooltip
    label={dock.collapsed ? `Show ${label}` : `Hide ${label}`}
    detail="Panels remember their width, their side and whether you left them open."
  >
    <button className="dock-toggle" onClick={dock.toggle}
      aria-label={dock.collapsed ? `Show ${label}` : `Hide ${label}`} aria-expanded={!dock.collapsed}>
      {dock.side === 'left'
        ? (dock.collapsed ? <PanelLeftOpen size={16} /> : <PanelLeftClose size={16} />)
        : (dock.collapsed ? <PanelRightOpen size={16} /> : <PanelRightClose size={16} />)}
      {children}
    </button>
  </Tooltip>
}
