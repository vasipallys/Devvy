import { useEffect, useRef, useState } from 'react'
import { Bot, Code2, FileText, Globe2, Home, Image, Menu, MessageSquare, Paperclip, Plus, Search, Send, Sparkles, Square, Trash2, X } from 'lucide-react'
import { marked } from 'marked'
import { api, API, attachToJob } from './api'
import { EvidencePanel } from './EvidencePanel'
import { Tooltip } from './Tooltip'
import { SystemStatusChip } from './SystemStatusChip'
import { ShareButton } from './ShareDialog'
import { isJobActive, type AgentEvent, type Attachment, type Conversation, type Message, type Mode } from './types'

const modes: { id: Mode; label: string; icon: typeof Bot }[] = [
  { id: 'auto', label: 'Auto', icon: Sparkles }, { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'code', label: 'Code', icon: Code2 }, { id: 'research', label: 'Research', icon: Globe2 },
  { id: 'image', label: 'Image', icon: Image }, { id: 'document', label: 'Document', icon: FileText },
]

/** What picking a mode actually changes, since the difference is a routing decision the
 *  user cannot otherwise see. */
const MODE_WHY: Record<Mode, string> = {
  auto: 'The router reads your message and picks a mode itself, then tells you which phrase decided it. Attachments outrank everything else.',
  chat: 'Answers from the model alone. Nothing leaves this machine and no sources are retrieved.',
  code: 'Asks for complete, runnable code rather than a sketch, with the reasoning kept short.',
  research: 'The only mode that reaches the internet. It searches, fetches pages, and answers from what it retrieved — citing each source, and saying plainly when live data was unavailable rather than inventing it.',
  image: 'Generates an image locally with the diffusion extra. Without it installed the run reports that instead of failing silently.',
  document: 'Answers from your attached files. Their text is capped and marked untrusted evidence, so instructions inside a document cannot redirect the answer.',
}

function renderMarkdown(content: string): string {
  // Generated files are served by FastAPI, not the Vite frontend origin.
  // Rewriting here also fixes images loaded later from persisted conversation history.
  const withBackendAssets = content.replace(
    /\]\((\/generated\/[^)\s]+)\)/g,
    (_match, path: string) => `](${new URL(path, API).toString()})`,
  )
  const rendered = marked.parse(withBackendAssets) as string
  const document = new DOMParser().parseFromString(rendered, 'text/html')
  const dangerous = document.body.querySelectorAll('script,style,iframe,object,embed,form,meta,link,svg,math')
  dangerous.forEach(element => element.remove())
  const allowed = new Set(['P','BR','STRONG','EM','DEL','CODE','PRE','BLOCKQUOTE','UL','OL','LI','H1','H2','H3','H4','A','IMG','HR','TABLE','THEAD','TBODY','TR','TH','TD'])
  document.body.querySelectorAll('*').forEach(element => {
    if (!allowed.has(element.tagName)) {
      element.replaceWith(...Array.from(element.childNodes))
      return
    }
    for (const attribute of Array.from(element.attributes)) {
      const permitted = (element.tagName === 'A' && ['href','title'].includes(attribute.name))
        || (element.tagName === 'IMG' && ['src','alt','title'].includes(attribute.name))
        || (element.tagName === 'CODE' && attribute.name === 'class')
      if (!permitted) element.removeAttribute(attribute.name)
    }
    if (element.tagName === 'A') {
      const href = element.getAttribute('href') || ''
      if (!/^(https?:|#)/i.test(href)) element.removeAttribute('href')
      element.setAttribute('target', '_blank'); element.setAttribute('rel', 'noreferrer noopener')
    }
    if (element.tagName === 'IMG') {
      const src = element.getAttribute('src') || ''
      if (!/^https?:/i.test(src)) element.remove()
    }
  })
  return document.body.innerHTML
}

export function App({ onHome, initialConversationId, onConversationChange }: {
  onHome?: () => void
  initialConversationId?: string
  onConversationChange?: (id: string) => void
}) {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string | undefined>(initialConversationId)
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState(''); const [mode, setMode] = useState<Mode>('auto')
  const [attachments, setAttachments] = useState<Attachment[]>([]); const [sending, setSending] = useState(false)
  const [sidebar, setSidebar] = useState(true); const [error, setError] = useState(''); const [query, setQuery] = useState('')
  const [runEvents, setRunEvents] = useState<AgentEvent[]>([])
  const [activeJobId, setActiveJobId] = useState<string>()
  const endRef = useRef<HTMLDivElement>(null); const fileRef = useRef<HTMLInputElement>(null); const abortRef = useRef<AbortController | undefined>(undefined)
  // The conversation currently being streamed into. A brand-new chat gets its id from the
  // `start` event, which would otherwise trip the loader below and replace the optimistic
  // messages with the server's copy — where the assistant row does not exist yet. Every
  // subsequent token would then have no message to attach to and be dropped.
  const streamingRef = useRef<string | undefined>(undefined)

  const refresh = async () => setConversations(await api.conversations())
  useEffect(() => { refresh().catch(e => setError(e.message)) }, [])
  // Keep the URL pointing at whatever conversation is open, so a reload or a shared link
  // lands in the same place. Replaces rather than pushes: opening a chat is not a
  // separate history entry from selecting one within it.
  useEffect(() => {
    if (activeId) onConversationChange?.(activeId)
  }, [activeId, onConversationChange])
  useEffect(() => {
    if (activeId === streamingRef.current) return
    if (!activeId) { setMessages([]); return }
    // Opening a conversation also reattaches to a turn still generating on the server, so
    // a reopened browser resumes mid-answer instead of showing a stalled blank bubble.
    const load = async () => {
      try {
        const [history, { jobs }] = await Promise.all([api.messages(activeId), api.jobs()])
        setMessages(history)
        const live = jobs.find(job =>
          job.kind === 'chat' && job.conversation_id === activeId && isJobActive(job.status))
        if (live) {
          const placeholder: Message = {
            id: crypto.randomUUID(), role: 'assistant', content: '',
            created_at: new Date().toISOString(),
          }
          setMessages(current => [...current, placeholder])
          setActiveJobId(live.id)
          follow(live.id, placeholder.id, activeId)
        }
      } catch (e) { setError((e as Error).message) }
    }
    load()
  }, [activeId])
  // Block body, deliberately: a concise arrow would return whatever scrollIntoView yields,
  // and React treats an effect's return value as its cleanup function. Anything non-callable
  // there fails with "destroy is not a function" when the effect re-runs or unmounts — which
  // is every streamed token here, since `messages` changes on each one.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  async function createChat() { const chat = await api.create(); setConversations(x => [chat, ...x]); setActiveId(chat.id); setMessages([]) }
  async function removeChat(id: string) { await api.remove(id); if (activeId === id) { setActiveId(undefined); setMessages([]) }; refresh() }
  async function pickFiles(files: FileList | null) {
    if (!files) return
    try {
      const uploaded = await Promise.all([...files].map(api.upload))
      setAttachments(x => [...x, ...uploaded]); setMode('document')
    } catch (e) { setError((e as Error).message) }
  }
  /** Follow a job: works for one just submitted and for one already running on the server. */
  async function follow(jobId: string, placeholderId: string, conversationId: string) {
    streamingRef.current = conversationId
    setSending(true)
    abortRef.current = new AbortController()
    try {
      await attachToJob(jobId, {
        onSnapshot: job => setRunEvents(job.events),
        onEvent: event => setRunEvents(current => [...current, event]),
        onStatus: message => setMessages(x => x.map(m =>
          m.id === placeholderId && !m.content ? { ...m, content: `_${message}_` } : m)),
        onText: text => setMessages(x => x.map(m =>
          m.id === placeholderId ? { ...m, content: text } : m)),
        onDone: (status, result, failure) => {
          if (status === 'succeeded' && result?.message) {
            // Swap the placeholder for the persisted row so it carries its real id.
            setMessages(x => x.map(m => m.id === placeholderId ? result.message : m))
          } else if (status !== 'succeeded') {
            const note = status === 'cancelled' ? 'Cancelled.' : `Generation ${status}: ${failure ?? ''}`
            setMessages(x => x.map(m => m.id === placeholderId
              ? { ...m, content: m.content || `_${note}_` } : m))
          }
        },
      }, abortRef.current.signal)
      await refresh()
    } catch (e) {
      // Detaching from the stream never cancels the job; it keeps running server-side.
      if ((e as Error).name !== 'AbortError') setError((e as Error).message)
    } finally { setSending(false); streamingRef.current = undefined; setActiveJobId(undefined) }
  }

  /** Stop means stop: cancel the job on the server, not merely detach this tab from it. */
  async function stop() {
    if (activeJobId) {
      try { await api.cancelJob(activeJobId) } catch { /* already finished */ }
    }
    abortRef.current?.abort()
  }

  async function send() {
    const text = input.trim(); if (!text || sending) return
    setError(''); setInput(''); setRunEvents([])
    const placeholder: Message = { id: crypto.randomUUID(), role: 'assistant', content: '', created_at: new Date().toISOString() }
    const uploaded = attachments; setAttachments([])
    setSending(true)
    try {
      const submitted = await api.submitChat({
        conversation_id: activeId, message: text,
        attachment_ids: uploaded.map(x => x.id), mode,
      })
      streamingRef.current = submitted.conversation_id
      if (!activeId) setActiveId(submitted.conversation_id)
      setMessages(x => [...x, submitted.message, placeholder])
      setActiveJobId(submitted.job_id)
      await follow(submitted.job_id, placeholder.id, submitted.conversation_id)
    } catch (e) {
      setSending(false)
      setError((e as Error).message)
    }
  }
  const filtered = conversations.filter(x => x.title.toLowerCase().includes(query.toLowerCase()))
  return <div className="shell">
    <aside className={sidebar ? 'sidebar' : 'sidebar closed'}>
      <div className="brand"><div className="brand-mark"><Sparkles size={18}/></div><span>Devvy</span><button className="icon" onClick={() => setSidebar(false)}><X size={18}/></button></div>
      {onHome && <button className="home-nav" onClick={onHome}><Home size={17}/> Home</button>}
      <button className="new-chat" onClick={createChat}><Plus size={17}/> New chat</button>
      <div className="search"><Search size={15}/><input aria-label="Search conversations" placeholder="Search conversations" value={query} onChange={e => setQuery(e.target.value)}/></div>
      <div className="history"><div className="section-label">Recent</div>{filtered.map(chat => <div key={chat.id} className={`history-item ${activeId === chat.id ? 'active' : ''}`}><button className="history-open" onClick={() => setActiveId(chat.id)}><MessageSquare size={15}/><span>{chat.title}</span></button><button className="history-delete" aria-label={`Delete ${chat.title}`} onClick={() => removeChat(chat.id)}><Trash2 size={14}/></button></div>)}</div>
      <div className="local-badge"><span className="pulse"/><div><b>Local workspace</b><small>Private on your machine</small></div></div>
    </aside>
    <main>
      <header><button className="icon" aria-label="Toggle navigation" onClick={() => setSidebar(!sidebar)}><Menu size={19}/></button>{onHome && <button className="icon" title="Home" onClick={onHome}><Home size={18}/></button>}<div className="workspace-title"><b>Chat</b><span>Grounded local assistant</span></div><div className="header-spacer"/>{activeId && conversations.some(item => item.id === activeId) && <ShareButton resourceType="conversation" resourceId={activeId}/>}<SystemStatusChip/></header>
      <section className="conversation">
        {!messages.length ? <div className="welcome"><div className="orb"><Sparkles size={31}/></div><h1>What can I help you build?</h1><p>Chat privately with Devvy, write production code, research the web, analyze documents, or generate images.</p><div className="suggestions">{[
          ['Build an API', 'Create a FastAPI service with authentication', Code2], ['Analyze a document', 'Summarize and extract key findings', FileText],
          ['Research a topic', 'Find and synthesize current sources', Globe2], ['Create an image', 'Generate a polished visual concept', Image]
        ].map(([title, desc, Icon]: any) => <button key={title} onClick={() => setInput(desc)}><Icon size={19}/><div><b>{title}</b><span>{desc}</span></div></button>)}</div></div>
        : <div className="messages">{messages.map(message => <article key={message.id} className={message.role}>
          <div className="avatar">{message.role === 'user' ? 'You' : <Sparkles size={17}/>}</div><div className="message-body">
            <div className="message-name">{message.role === 'user' ? 'You' : 'Devvy'}</div>
            {message.attachments?.map(a => <div className="file-chip" key={a.id}><FileText size={15}/>{a.name}</div>)}
            <div className="markdown" dangerouslySetInnerHTML={{ __html: renderMarkdown(message.content || (sending ? 'Thinking…' : '')) }} />
          </div></article>)}<div ref={endRef}/></div>}
      </section>
      <footer>{error && <div className="error"><span>{error}</span><button onClick={() => setError('')}><X size={14}/></button></div>}
        {attachments.length > 0 && <div className="attachment-row">{attachments.map(a => <span key={a.id}><FileText size={14}/>{a.name}<button onClick={() => setAttachments(x => x.filter(y => y.id !== a.id))}><X size={13}/></button></span>)}</div>}
        <div className="composer"><textarea aria-label="Message Devvy" value={input} onChange={e => setInput(e.target.value)} placeholder="Message Devvy…" rows={1} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}/>
          <div className="composer-actions"><input ref={fileRef} hidden type="file" multiple accept=".pdf,.docx,.txt,.md,.py,.js,.ts,.json,.csv" onChange={e => pickFiles(e.target.files)}/><button className="tool" title="Attach documents" onClick={() => fileRef.current?.click()}><Paperclip size={18}/></button>
            <div className="mode-picker">{modes.map(item => <Tooltip key={item.id} label={item.label} detail={MODE_WHY[item.id]}><button className={mode === item.id ? 'selected' : ''} onClick={() => setMode(item.id)}><item.icon size={15}/><span>{item.label}</span></button></Tooltip>)}</div><div className="grow"/>
            {sending ? <button className="send" title="Stop generating" aria-label="Stop generating" onClick={stop}><Square size={14}/></button> : <button className="send" disabled={!input.trim()} onClick={send}><Send size={17}/></button>}
          </div></div><small className="disclaimer">Devvy runs locally and can make mistakes. Verify important information.</small>
      </footer>
    </main>
    <EvidencePanel events={runEvents}/>
    <div className="sr-only" aria-live="polite">{sending ? 'Devvy is working' : error || (runEvents.length ? runEvents[runEvents.length - 1].label : '')}</div>
  </div>
}
