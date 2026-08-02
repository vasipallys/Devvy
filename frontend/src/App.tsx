import { useEffect, useRef, useState } from 'react'
import { Bot, Code2, FileText, Globe2, Home, Image, Menu, MessageSquare, Paperclip, Plus, Search, Send, Sparkles, Square, Trash2, X } from 'lucide-react'
import { marked } from 'marked'
import { api, API } from './api'
import { EvidencePanel } from './EvidencePanel'
import { SystemStatusChip } from './SystemStatusChip'
import type { AgentEvent, Attachment, Conversation, Message, Mode } from './types'

const modes: { id: Mode; label: string; icon: typeof Bot }[] = [
  { id: 'auto', label: 'Auto', icon: Sparkles }, { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'code', label: 'Code', icon: Code2 }, { id: 'research', label: 'Research', icon: Globe2 },
  { id: 'image', label: 'Image', icon: Image }, { id: 'document', label: 'Document', icon: FileText },
]

function renderMarkdown(content: string): string {
  // Generated files are served by FastAPI, not the Vite/Electron frontend origin.
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

export function App({ onHome }: { onHome?: () => void }) {
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [activeId, setActiveId] = useState<string>()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState(''); const [mode, setMode] = useState<Mode>('auto')
  const [attachments, setAttachments] = useState<Attachment[]>([]); const [sending, setSending] = useState(false)
  const [sidebar, setSidebar] = useState(true); const [error, setError] = useState(''); const [query, setQuery] = useState('')
  const [runEvents, setRunEvents] = useState<AgentEvent[]>([])
  const endRef = useRef<HTMLDivElement>(null); const fileRef = useRef<HTMLInputElement>(null); const abortRef = useRef<AbortController | undefined>(undefined)

  const refresh = async () => setConversations(await api.conversations())
  useEffect(() => { refresh().catch(e => setError(e.message)) }, [])
  useEffect(() => { if (activeId) api.messages(activeId).then(setMessages).catch(e => setError(e.message)); else setMessages([]) }, [activeId])
  useEffect(() => endRef.current?.scrollIntoView({ behavior: 'smooth' }), [messages])

  async function createChat() { const chat = await api.create(); setConversations(x => [chat, ...x]); setActiveId(chat.id); setMessages([]) }
  async function removeChat(id: string) { await api.remove(id); if (activeId === id) { setActiveId(undefined); setMessages([]) }; refresh() }
  async function pickFiles(files: FileList | null) {
    if (!files) return
    try {
      const uploaded = await Promise.all([...files].map(api.upload))
      setAttachments(x => [...x, ...uploaded]); setMode('document')
    } catch (e) { setError((e as Error).message) }
  }
  async function send() {
    const text = input.trim(); if (!text || sending) return
    setError(''); setInput(''); setSending(true); setRunEvents([])
    const user: Message = { id: crypto.randomUUID(), role: 'user', content: text, created_at: new Date().toISOString(), attachments }
    const assistant: Message = { id: crypto.randomUUID(), role: 'assistant', content: '', created_at: new Date().toISOString() }
    setMessages(x => [...x, user, assistant]); const uploaded = attachments; setAttachments([])
    abortRef.current = new AbortController()
    let receivedToken = false
    try {
      await api.stream({ conversation_id: activeId, message: text, attachment_ids: uploaded.map(x => x.id), mode }, event => {
        if (event.type === 'agent_event') setRunEvents(current => [...current, event as AgentEvent])
        if (event.type === 'start' && !activeId) setActiveId(event.conversation_id)
        if (event.type === 'status' && !receivedToken) setMessages(x => x.map(m => m.id === assistant.id ? { ...m, content: `_${event.content}_` } : m))
        if (event.type === 'token') {
          const firstToken = !receivedToken
          receivedToken = true
          setMessages(x => x.map(m => m.id === assistant.id ? { ...m, content: (firstToken ? '' : m.content) + event.content } : m))
        }
        if (event.type === 'error') {
          setMessages(x => x.map(m => m.id === assistant.id ? { ...m, content: `Generation failed: ${event.message}` } : m))
          throw new Error(event.message)
        }
      }, abortRef.current.signal)
      await refresh()
    } catch (e) { if ((e as Error).name !== 'AbortError') setError((e as Error).message) }
    finally { setSending(false) }
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
      <header><button className="icon" aria-label="Toggle navigation" onClick={() => setSidebar(!sidebar)}><Menu size={19}/></button>{onHome && <button className="icon" title="Home" onClick={onHome}><Home size={18}/></button>}<div className="workspace-title"><b>Chat</b><span>Grounded local assistant</span></div><div className="header-spacer"/><SystemStatusChip/></header>
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
            <div className="mode-picker">{modes.map(item => <button key={item.id} className={mode === item.id ? 'selected' : ''} onClick={() => setMode(item.id)}><item.icon size={15}/><span>{item.label}</span></button>)}</div><div className="grow"/>
            {sending ? <button className="send" onClick={() => abortRef.current?.abort()}><Square size={14}/></button> : <button className="send" disabled={!input.trim()} onClick={send}><Send size={17}/></button>}
          </div></div><small className="disclaimer">Devvy runs locally and can make mistakes. Verify important information.</small>
      </footer>
    </main>
    <EvidencePanel events={runEvents}/>
    <div className="sr-only" aria-live="polite">{sending ? 'Devvy is working' : error || (runEvents.length ? runEvents[runEvents.length - 1].label : '')}</div>
  </div>
}
