/**
 * The chat sanitiser, attacked.
 *
 * `renderMarkdown` is the only place in this application that turns text into live HTML, and
 * the text reaching it is not the user's: it is model output, extracted document content, and —
 * in research mode — the body of arbitrary public web pages. That makes it the one function
 * where an injection could actually land, and the one worth attacking directly rather than
 * reading and pronouncing safe.
 *
 * These run in jsdom because the implementation builds a real DOM. Each payload is placed into
 * a live element the way React's `dangerouslySetInnerHTML` would, and the assertion is about
 * what *survived the round trip* — serialise, re-parse, inspect — rather than about the string
 * the function returned. Re-parsing is the part that matters: mutation XSS works precisely by
 * surviving that step looking different than it did going in.
 */
import { describe, expect, it } from 'vitest'

import { renderMarkdown } from './App'

/** What survived, as a browser would actually see it. */
function land(markup: string) {
  const host = document.createElement('div')
  host.innerHTML = renderMarkdown(markup)
  const executable = host.querySelectorAll(
    'script,iframe,object,embed,form,meta,link,svg,math,style,base',
  )
  const handlers: string[] = []
  host.querySelectorAll('*').forEach(element => {
    for (const attribute of Array.from(element.attributes)) {
      if (/^on/i.test(attribute.name)) {
        handlers.push(`${element.tagName}[${attribute.name}]`)
      }
      if (
        /^(href|src|action|formaction|xlink:href)$/i.test(attribute.name)
        && /^\s*(javascript|data|vbscript):/i.test(attribute.value)
      ) {
        handlers.push(`${element.tagName}[${attribute.name}="${attribute.value}"]`)
      }
    }
  })
  return { html: host.innerHTML, executable: [...executable], handlers, host }
}

const PAYLOADS: [name: string, markup: string][] = [
  ['a script tag', '<script>window.pwned = 1</script>'],
  ['an image error handler', '<img src=x onerror="window.pwned = 1">'],
  ['an svg load handler', '<svg onload="window.pwned = 1"></svg>'],
  ['a javascript: link written as markdown', '[click](javascript:window.pwned = 1)'],
  ['a javascript: link with mixed case', '<a href="JaVaScRiPt:window.pwned=1">x</a>'],
  ['a data: link', '<a href="data:text/html,<script>window.pwned=1</script>">x</a>'],
  ['a vbscript: link', '<a href="vbscript:msgbox(1)">x</a>'],
  ['an iframe with srcdoc', '<iframe srcdoc="<script>window.pwned=1</script>"></iframe>'],
  ['a body load handler', '<body onload="window.pwned = 1">'],
  ['a form that posts elsewhere', '<form action="https://evil.test"><input name="a"></form>'],
  ['a style block', '<style>body{background:url("javascript:window.pwned=1")}</style>'],
  ['mutation XSS through noscript', '<noscript><p title="</noscript><img src=x onerror=window.pwned=1>"></noscript>'],
  ['mutation XSS through annotation-xml', '<math><annotation-xml encoding="text/html"><img src=x onerror=window.pwned=1></annotation-xml></math>'],
  ['a handler on an unwrapped element', '<div><span onclick="window.pwned = 1">text</span></div>'],
  ['a details toggle handler', '<details open ontoggle="window.pwned = 1"></details>'],
  ['an object with a javascript: source', '<object data="javascript:window.pwned=1"></object>'],
  ['a meta refresh', '<meta http-equiv="refresh" content="0;url=javascript:window.pwned=1">'],
  ['a base tag that rewrites every link', '<base href="javascript:">'],
  ['a javascript: image written as markdown', '![alt](javascript:window.pwned = 1)'],
  ['an inline event handler on a permitted tag', '<p onmouseover="window.pwned = 1">hover</p>'],
  ['an anchor with a formaction', '<a href="#" formaction="javascript:window.pwned=1">x</a>'],
  ['an svg use with xlink', '<svg><use xlink:href="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="/></svg>'],
]

describe('renderMarkdown', () => {
  it.each(PAYLOADS)('neutralises %s', (_name, markup) => {
    const { executable, handlers } = land(markup)
    expect(executable).toEqual([])
    expect(handlers).toEqual([])
  })

  it('never leaves a live handler or executable element across every payload at once', () => {
    // Combined, because sanitisers sometimes hold individually and fail on interaction.
    const { executable, handlers } = land(PAYLOADS.map(([, markup]) => markup).join('\n\n'))
    expect(executable).toEqual([])
    expect(handlers).toEqual([])
    expect((window as unknown as { pwned?: number }).pwned).toBeUndefined()
  })

  // -- It has to still render the ordinary case, or "safe" is just "broken" ----------------

  it('keeps the formatting a chat answer actually uses', () => {
    const html = renderMarkdown('**bold** and `code`\n\n- one\n- two')
    expect(html).toContain('<strong>bold</strong>')
    expect(html).toContain('<code>code</code>')
    expect(html).toContain('<li>one</li>')
  })

  it('keeps ordinary links and makes them safe to click', () => {
    const { host } = land('[docs](https://example.test/page)')
    const anchor = host.querySelector('a')
    expect(anchor?.getAttribute('href')).toBe('https://example.test/page')
    expect(anchor?.getAttribute('rel')).toBe('noreferrer noopener')
    expect(anchor?.getAttribute('target')).toBe('_blank')
  })

  it('keeps https images and drops every other scheme', () => {
    expect(land('![x](https://example.test/a.png)').host.querySelector('img')).not.toBeNull()
    expect(land('![x](file:///etc/passwd)').host.querySelector('img')).toBeNull()
  })

  it('leaves plain prose untouched', () => {
    expect(renderMarkdown('Just a sentence.')).toContain('Just a sentence.')
  })

  it('escapes rather than executes text that merely looks like markup', () => {
    const { host, executable } = land('The tag `<script>` is written about, not run.')
    expect(executable).toEqual([])
    expect(host.textContent).toContain('<script>')
  })
})
