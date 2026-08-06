import {
  cloneElement, isValidElement, useCallback, useEffect, useId, useRef, useState,
  type ReactElement, type ReactNode, type Ref,
} from 'react'
import { createPortal } from 'react-dom'

/**
 * Accessible explanation on hover and focus.
 *
 * Devvy's premise is that a reader can tell what happened and why. Most of that reasoning
 * was previously behind a click, which means it only reached people who already suspected
 * there was something to find. A tooltip puts the "why" one hover away.
 *
 * Built to WCAG 1.4.13 (Content on Hover or Focus), which is the part usually skipped:
 *
 * - **Dismissible** — Escape closes it without moving the pointer.
 * - **Hoverable** — the pointer can move into the tooltip without it vanishing, so the text
 *   can be read slowly or selected.
 * - **Persistent** — it stays until the pointer or focus leaves, or Escape is pressed.
 *
 * It also opens on keyboard focus, not only hover, so the explanation is not mouse-only —
 * and a wrapped element that nothing can focus (a badge, a status icon) is given a tab stop
 * so its explanation is reachable at all.
 *
 * **It adds no DOM node.** The handlers are cloned onto the element being explained. An
 * earlier version wrapped children in a `display: contents` span, which is invisible to
 * layout but *not* to CSS: every `.parent > div` rule in the stylesheet stopped matching,
 * and the Smart Code pipeline lost its entire appearance. Wrapping only happens for a child
 * that cannot be cloned, and the tooltip itself is portalled because several panels use
 * `overflow: hidden` and would otherwise clip it.
 */

const SHOW_DELAY_MS = 350
const HIDE_DELAY_MS = 120
/** Gap between trigger and tooltip, and the margin kept from the viewport edge. */
const OFFSET = 8
const EDGE = 10

/** Elements a keyboard already reaches, which must not be given a second tab stop. */
const FOCUSABLE = new Set(['a', 'button', 'input', 'select', 'textarea', 'summary'])

interface Position { top: number; left: number; placement: 'top' | 'bottom' }

type AnchorProps = Record<string, unknown> & { ref?: Ref<HTMLElement> }

/** Run our handler after the child's own, without swallowing it. */
function compose(theirs: unknown, ours: () => void) {
  return (event: unknown) => {
    if (typeof theirs === 'function') (theirs as (value: unknown) => void)(event)
    ours()
  }
}

export function Tooltip({ label, detail, children, disabled = false }: {
  /** The short "what". Rendered emphasised. */
  label: ReactNode
  /** The "why" — the part that actually earns the tooltip. Optional but expected. */
  detail?: ReactNode
  children: ReactNode
  disabled?: boolean
}) {
  const id = useId()
  const [position, setPosition] = useState<Position>()
  const anchorRef = useRef<HTMLElement | null>(null)
  const timerRef = useRef<number | undefined>(undefined)

  const clearTimer = () => { if (timerRef.current) window.clearTimeout(timerRef.current) }

  const place = useCallback(() => {
    const anchor = anchorRef.current
    if (!anchor) return
    const rect = anchor.getBoundingClientRect()
    // Estimated height is enough to choose a side; the real element is clamped after mount.
    const above = rect.top > 140
    setPosition({
      top: above ? rect.top - OFFSET : rect.bottom + OFFSET,
      left: Math.min(
        Math.max(rect.left + rect.width / 2, EDGE + 140),
        window.innerWidth - EDGE - 140,
      ),
      placement: above ? 'top' : 'bottom',
    })
  }, [])

  const open = useCallback((immediate = false) => {
    if (disabled) return
    clearTimer()
    timerRef.current = window.setTimeout(place, immediate ? 0 : SHOW_DELAY_MS)
  }, [disabled, place])

  const close = useCallback((immediate = false) => {
    clearTimer()
    timerRef.current = window.setTimeout(() => setPosition(undefined), immediate ? 0 : HIDE_DELAY_MS)
  }, [])

  useEffect(() => {
    if (!position) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close(true)
    }
    // Scrolling invalidates the measured position, so it has to be taken again. Closing
    // instead would break keyboard use outright: focusing an off-screen element scrolls it
    // into view, and that scroll would dismiss the tooltip the focus just opened.
    let frame = 0
    const onMove = () => {
      if (frame) return
      frame = window.requestAnimationFrame(() => {
        frame = 0
        const rect = anchorRef.current?.getBoundingClientRect()
        if (!rect || rect.bottom < 0 || rect.top > window.innerHeight) close(true)
        else place()
      })
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('scroll', onMove, true)
    window.addEventListener('resize', onMove)
    return () => {
      if (frame) window.cancelAnimationFrame(frame)
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('scroll', onMove, true)
      window.removeEventListener('resize', onMove)
    }
  }, [position, close, place])

  // Block body: a pending show/hide timer must not fire after unmount.
  useEffect(() => {
    return clearTimer
  }, [])

  // An icon-only anchor carries no text, so a keyboard user would land on an unnamed stop.
  // Naming it needs the rendered text, which is only knowable from the DOM.
  useEffect(() => {
    const anchor = anchorRef.current
    if (!anchor || typeof label !== 'string') return
    if (anchor.getAttribute('tabindex') !== '0') return
    if (!anchor.textContent?.trim() && !anchor.getAttribute('aria-label')) {
      anchor.setAttribute('aria-label', label)
    }
  }, [label])

  const tip = position && createPortal(
    <div
      id={id}
      role="tooltip"
      className={`tooltip tooltip-${position.placement}`}
      style={{ top: position.top, left: position.left }}
      // Hoverable: moving into the tooltip keeps it open (WCAG 1.4.13).
      onPointerEnter={() => clearTimer()}
      onPointerLeave={() => close()}
    >
      <b>{label}</b>
      {detail && <span>{detail}</span>}
    </div>,
    document.body,
  )

  if (disabled) return <>{children}</>

  // Cloning onto a host element keeps the DOM — and therefore every CSS selector — unchanged.
  if (isValidElement(children) && typeof children.type === 'string') {
    const tag = children.type
    const child = children as ReactElement<AnchorProps>
    const own = child.props
    const alreadyFocusable = FOCUSABLE.has(tag) || own.tabIndex !== undefined
    return <>
      {cloneElement(child, {
        ref: (node: HTMLElement | null) => {
          anchorRef.current = node
          const theirs = own.ref
          if (typeof theirs === 'function') theirs(node)
          else if (theirs && typeof theirs === 'object') (theirs as { current: unknown }).current = node
        },
        onPointerEnter: compose(own.onPointerEnter, () => open()),
        onPointerLeave: compose(own.onPointerLeave, () => close()),
        onFocus: compose(own.onFocus, () => open(true)),
        onBlur: compose(own.onBlur, () => close(true)),
        // A badge or icon is otherwise unreachable, which would make the explanation
        // mouse-only. A button already has a stop; a second one would just be noise.
        tabIndex: alreadyFocusable ? own.tabIndex : 0,
        'data-tooltip-anchor': '',
        'aria-describedby': position ? id : own['aria-describedby'],
      } as AnchorProps)}
      {tip}
    </>
  }

  // Fragments, text, and component children cannot take handlers, so they get a wrapper.
  return <>
    <span
      ref={node => { anchorRef.current = node }}
      className="tooltip-anchor"
      tabIndex={0}
      onPointerEnter={() => open()}
      onPointerLeave={() => close()}
      onFocus={() => open(true)}
      onBlur={() => close(true)}
      aria-describedby={position ? id : undefined}
    >
      {children}
    </span>
    {tip}
  </>
}
