import { FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowLeft, Brain, Code2, FileText, Globe2, History, Image, MessageSquare, Mic, NotebookPen, Paperclip, RotateCcw, Send, Sparkles, Square, Video, X } from 'lucide-react'
import { api, API } from './api'
import { EvidencePanel } from './EvidencePanel'
import { FacultiesPanel } from './FacultiesPanel'
import { Tooltip } from './Tooltip'
import { isJobActive } from './types'
import type { AgentEvent, Attachment, Mode } from './types'
// 640px WebP: the avatar renders at 178px, so the 1254px PNG it replaced shipped
// 2.5 MB — roughly seven times the JavaScript bundle — to fill a small circle.
import robotGirl from './assets/robot-girl.webp'

type AgentState = 'connecting' | 'idle' | 'listening' | 'thinking' | 'speaking' | 'error'
const modes: { id: Mode; label: string; icon: typeof Sparkles }[] = [
  { id: 'auto', label: 'Auto', icon: Sparkles }, { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'code', label: 'Code', icon: Code2 }, { id: 'research', label: 'Research', icon: Globe2 },
  { id: 'image', label: 'Image', icon: Image }, { id: 'document', label: 'Document', icon: FileText },
]

function LinkedText({ text }: { text: string }) {
  return <>{text.split(/(https?:\/\/[^\s)]+)/g).map((part, index) =>
    part.startsWith('http')
      ? <a key={index} href={part} target="_blank" rel="noreferrer">{part}</a>
      : part
  )}</>
}

/** What actually went wrong with the voice, in words that point somewhere.
 *
 *  The browser's own text for a rejected fetch is "no supported source was found", which reads
 *  as a codec problem and sends you looking at the WAV. It is far more often the response not
 *  being audio at all — an expired session answering the media URL with JSON. */
function mediaErrorMessage(error: MediaError | null): string {
  switch (error?.code) {
    case MediaError.MEDIA_ERR_SRC_NOT_SUPPORTED:
      return 'The voice audio could not be loaded. This is usually an expired session — the '
        + 'answer is above; reload the page to hear the next one.'
    case MediaError.MEDIA_ERR_NETWORK:
      return 'The connection dropped while loading the voice audio. The answer is above.'
    case MediaError.MEDIA_ERR_DECODE:
      return 'The generated audio file is corrupt and could not be decoded. The answer is above.'
    default:
      return 'The answer could not be spoken. It is shown above.'
  }
}

function GeometricAgentFace({ mouthOpen, speaking }: { mouthOpen: number; speaking: boolean }) {
  return <div className={`agent-portrait ${speaking ? 'portrait-speaking' : ''}`} role="img" aria-label="YUKTI, your executive AI butler">
    <img src={robotGirl} alt="YUKTI, an AI butler" draggable={false}/>
    <span className="portrait-mouth" style={{ transform: `translate(-50%,-50%) scale(${1 + mouthOpen * .12},${.18 + mouthOpen * 1.3})`, opacity: .2 + mouthOpen * .75 }}/>
    <span className="portrait-halo"/>
  </div>
}

/** The orbit is Talk's only status display, so each state says what is happening and why. */
const STATE_WHY: Record<string, { label: string; why: string }> = {
  connecting: { label: 'Connecting', why: 'Opening the WebSocket to the local backend. Talk keeps its history in this connection only — nothing is written to disk.' },
  idle: { label: 'Ready', why: 'At your service. Speak by pressing Talk, or type — both take the same path through the agent.' },
  listening: { label: 'Listening', why: 'Recording your turn. Transcription runs locally on this machine; no audio leaves it.' },
  thinking: { label: 'Thinking', why: 'YUKTI is deciding where this turn’s evidence comes from — your notes, the memory bank, the live web, or nothing — and then composing. On a CPU model this is the slow part.' },
  speaking: { label: 'Speaking', why: 'Reading the answer aloud with the local voice. What is spoken is the answer with its markup stripped — the screen keeps the formatting, the speaker gets prose.' },
  error: { label: 'Error', why: 'Something in the turn failed. The reason is shown rather than hidden, and the session stays open so you can try again.' },
}

export function TalkScreen({ onHome, initialJobId }: {
  onHome: () => void
  /** A past spoken turn to show alongside a fresh session.
   *
   *  Talk keeps its conversation in the socket only, by design — there is no server-side
   *  history to reopen. So a run opened from Activity is shown as what it is: that turn's
   *  answer and evidence, beside a live session you can carry on with. Pretending the old
   *  session had been resumed would be a lie the next message would immediately expose. */
  initialJobId?: string
}) {
  const [state, setState] = useState<AgentState>('connecting')
  const [transcript, setTranscript] = useState('')
  const [response, setResponse] = useState('')
  const [status, setStatus] = useState('Connecting to your local butler…')
  const [error, setError] = useState('')
  const [videoUrl, setVideoUrl] = useState('')
  const [text, setText] = useState('')
  const [mode, setMode] = useState<Mode>('auto')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [uploading, setUploading] = useState(false)
  const [imageUrl, setImageUrl] = useState('')
  const [mouthOpen, setMouthOpen] = useState(0)
  const [subtitleWord, setSubtitleWord] = useState(0)
  const [runEvents, setRunEvents] = useState<AgentEvent[]>([])
  /** Set when this screen was opened on a past turn from Activity. */
  const [pastTurn, setPastTurn] = useState(false)
  /** What the second brain contributed to the turn on screen, derived from the run's own
   *  events so a client attaching mid-run rebuilds it from the snapshot alone. */
  const recalled = useMemo(() => {
    const event = [...runEvents].reverse().find(item => item.stage === 'second_brain')
    const evidence = (event?.evidence ?? {}) as { notes?: string[]; memories?: string[] }
    return { notes: evidence.notes ?? [], memories: evidence.memories ?? [] }
  }, [runEvents])

  // Show the turn that was asked for. Talk holds its conversation in the socket only, so
  // there is nothing to "resume" — the honest thing is to show that turn's answer and
  // evidence beside a live session, clearly labelled, rather than staging it as though the
  // old session were still open.
  useEffect(() => {
    if (!initialJobId) return
    let disposed = false
    api.job(initialJobId).then(job => {
      if (disposed || isJobActive(job.status)) return
      setRunEvents(job.events)
      setPastTurn(true)
      const request = (job.request ?? {}) as { transcript?: string }
      if (request.transcript) setTranscript(request.transcript)
      const answer = String(job.result?.response ?? job.output_text ?? '')
      if (answer) setResponse(answer)
    }).catch(() => {
      // Missing and not-yours are the same 404 by design; a live session is still usable.
      if (!disposed) setPastTurn(false)
    })
    return () => { disposed = true }
  }, [initialJobId])
  const socketRef = useRef<WebSocket | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const responseRef = useRef('')
  const audioFrameRef = useRef<number | null>(null)
  const audioContextRef = useRef<AudioContext | null>(null)
  const fileRef = useRef<HTMLInputElement | null>(null)

  function stopAudioAnalysis() {
    if (audioFrameRef.current !== null) cancelAnimationFrame(audioFrameRef.current)
    audioFrameRef.current = null; setMouthOpen(0)
    audioContextRef.current?.close().catch(() => undefined); audioContextRef.current = null
  }

  function stopAgentAudio() {
    const audio = audioRef.current
    audioRef.current = null
    if (audio) {
      audio.onplay = null; audio.onended = null; audio.onerror = null
      audio.pause(); audio.currentTime = 0; audio.removeAttribute('src'); audio.load()
    }
    stopAudioAnalysis(); setSubtitleWord(0)
  }

  function playAgentAudio(url: string) {
    stopAgentAudio()
    // `use-credentials`, not `anonymous`. Generated media is owner-checked behind the session
    // cookie, and `anonymous` is a CORS request with credentials *omitted* — the cookie never
    // goes, the server answers 401 with a JSON body, and the browser reports it as the far
    // less helpful "no supported source was found".
    //
    // Dropping crossOrigin entirely would send the cookie, but the element feeds an
    // AnalyserNode: without CORS the graph is tainted and plays silence. So the request has to
    // be CORS *and* credentialed, which the API allows (`allow_credentials=True` against a
    // specific origin).
    const audio = new Audio(API + url)
    audio.crossOrigin = 'use-credentials'
    audioRef.current = audio
    const context = new AudioContext(); audioContextRef.current = context
    const source = context.createMediaElementSource(audio); const analyser = context.createAnalyser()
    analyser.fftSize = 256; analyser.smoothingTimeConstant = 0.55
    source.connect(analyser); analyser.connect(context.destination)
    const samples = new Uint8Array(analyser.fftSize)
    const words = responseRef.current.trim().split(/\s+/).filter(Boolean)
    setSubtitleWord(0)
    const animate = () => {
      analyser.getByteTimeDomainData(samples)
      let energy = 0
      for (const sample of samples) { const centered = (sample - 128) / 128; energy += centered * centered }
      setMouthOpen(Math.min(1, Math.sqrt(energy / samples.length) * 5.5))
      if (Number.isFinite(audio.duration) && audio.duration > 0 && words.length) {
        setSubtitleWord(Math.min(words.length - 1, Math.floor((audio.currentTime / audio.duration) * words.length)))
      }
      if (!audio.paused && !audio.ended) audioFrameRef.current = requestAnimationFrame(animate)
    }
    audio.onplay = () => { setState('speaking'); audioFrameRef.current = requestAnimationFrame(animate) }
    audio.onended = () => { audioRef.current = null; stopAudioAnalysis(); setSubtitleWord(words.length); setState('idle'); setStatus('At your service') }
    // A failure here must hand the session back. The socket announces `speaking` before the
    // audio is fetched, and the Talk button is disabled in that state — so an unhandled media
    // error left the UI reading "Speaking" with nothing playing and no way to continue short
    // of a reload. The answer is already on screen; losing the voice must not also cost the
    // conversation.
    const failAudio = (message: string) => {
      audioRef.current = null
      stopAudioAnalysis()
      setError(message)
      setState('idle')
      setStatus('At your service — the answer is above')
    }
    audio.onerror = () => failAudio(mediaErrorMessage(audio.error))
    context.resume()
      .then(() => audio.play())
      .catch(e => failAudio(`The answer could not be spoken: ${e.message}. It is shown above.`))
  }

  useEffect(() => {
    const endpoint = API.replace(/^http/, 'ws') + '/api/talk/ws'
    let disposed = false; let attempts = 0; let retryTimer: number | undefined
    const connect = () => {
      if (disposed) return
      const socket = new WebSocket(endpoint); socketRef.current = socket
      socket.onopen = () => { attempts = 0; setError(''); setState('idle'); setStatus('At your service') }
      socket.onmessage = event => {
      const data = JSON.parse(event.data)
      if (data.type === 'state') {
        if (!(data.value === 'idle' && audioRef.current && !audioRef.current.paused)) setState(data.value)
        if (data.value === 'thinking') { stopAgentAudio(); setStatus('YUKTI is thinking locally…') }
      }
      if (data.type === 'status') setStatus(data.content)
      if (data.type === 'agent_event') setRunEvents(current => [...current, data as AgentEvent])
      if (data.type === 'transcript') { setTranscript(data.content); setResponse(''); responseRef.current = '' }
      if (data.type === 'token') setResponse(value => { const next = value + data.content; responseRef.current = next; return next })
      if (data.type === 'text_complete') { setResponse(data.content); responseRef.current = data.content }
      if (data.type === 'animation_state') setStatus('Creating a visual explanation…')
      if (data.type === 'video_ready') setVideoUrl(API + data.url)
      if (data.type === 'image_ready') setImageUrl(API + data.url)
      if (data.type === 'audio_ready') {
        playAgentAudio(data.url)
      }
      if (data.type === 'media_warning') setError(data.message)
      if (data.type === 'error') { setError(data.message); setState('error') }
      }
      socket.onerror = () => setError('Talk service connection was interrupted. Reconnecting…')
      socket.onclose = () => {
        if (disposed || socketRef.current !== socket) return
        socketRef.current = null; setState('connecting')
        const delay = Math.min(10_000, 500 * 2 ** attempts++); setStatus(`Reconnecting in ${Math.ceil(delay / 1000)}s…`)
        retryTimer = window.setTimeout(connect, delay)
      }
    }
    connect()
    return () => { disposed = true; if (retryTimer) window.clearTimeout(retryTimer); recorderRef.current?.stop(); stopAgentAudio(); socketRef.current?.close(); socketRef.current = null }
  }, [])

  async function beginListening() {
    stopAgentAudio(); setError(''); setVideoUrl(''); setRunEvents([])
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } })
      const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm'
      const recorder = new MediaRecorder(stream, { mimeType: mime }); recorderRef.current = recorder; chunksRef.current = []
      recorder.ondataavailable = event => { if (event.data.size) chunksRef.current.push(event.data) }
      recorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop())
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType })
        if (socketRef.current?.readyState === WebSocket.OPEN) {
          socketRef.current.send(await blob.arrayBuffer())
          socketRef.current.send(JSON.stringify({ type: 'commit', mime: recorder.mimeType }))
          setState('thinking'); setStatus('Transcribing locally with Whisper…')
        }
      }
      recorder.start(); setState('listening'); setStatus('Listening… tap again when finished')
    } catch (e) { setError(`Microphone unavailable: ${(e as Error).message}`); setState('error') }
  }

  function stopListening() { recorderRef.current?.stop(); recorderRef.current = null }
  async function pickFiles(files: FileList | null) {
    if (!files?.length || uploading) return
    const selected = [...files]
    if (attachments.length + selected.length > 10) { setError('You can attach up to 10 documents at a time.'); return }
    if (selected.some(file => file.size > 25 * 1024 * 1024)) { setError('Each attachment must be 25 MB or smaller.'); return }
    setUploading(true); setError('')
    try {
      const uploaded = await Promise.all(selected.map(api.upload))
      setAttachments(current => [...current, ...uploaded]); setMode('document')
    } catch (e) { setError((e as Error).message) }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = '' }
  }
  function submitText(event: FormEvent) {
    event.preventDefault(); const content = text.trim(); if (!content || socketRef.current?.readyState !== WebSocket.OPEN) return
    stopAgentAudio()
    const attachmentIds = attachments.map(item => item.id)
    setText(''); setAttachments([]); setTranscript(content); setResponse(''); responseRef.current = ''; setError(''); setVideoUrl(''); setImageUrl(''); setRunEvents([]); setState('thinking')
    socketRef.current.send(JSON.stringify({ type: 'text', content, mode, attachment_ids: attachmentIds }))
  }
  function reset() { stopAgentAudio(); setTranscript(''); setResponse(''); setVideoUrl(''); setImageUrl(''); setAttachments([]); setRunEvents([]); setError(''); socketRef.current?.send(JSON.stringify({ type: 'reset' })) }

  return <div className="talk-screen">
    <header className="talk-header"><button onClick={onHome}><ArrowLeft size={18}/> Home</button><div><Sparkles size={18}/><b>YUKTI</b><span>Executive AI butler · your second brain</span></div><button onClick={reset}><RotateCcw size={16}/> Reset</button></header>
    <main className="talk-main">
      {/* Talk holds its conversation in the socket, so there is no old session to resume.
          Showing that turn's answer beside a live session is the honest presentation; staging
          it as a resumed conversation would be a lie the next message immediately exposes. */}
      {pastTurn && <div className="restored-banner" role="status">
        <History size={15}/>
        <span>
          <b>Showing a past turn</b>
          <small>
            Talk keeps each conversation in its live connection only, so this one cannot be
            resumed. Speak or type to start a new session — this answer stays until you do.
            Anything YUKTI was asked to remember survives regardless; the memory bank is not
            part of the connection.
          </small>
        </span>
      </div>}
      <section className="voice-stage">
        <Tooltip label={STATE_WHY[state]?.label ?? state} detail={STATE_WHY[state]?.why ?? 'The voice session is in this state.'}>
        <div className={`voice-orbit state-${state}`}><div className="orbit-ring ring-one"/><div className="orbit-ring ring-two"/><div className="voice-core face-core"><GeometricAgentFace mouthOpen={mouthOpen} speaking={state === 'speaking'}/></div></div></Tooltip>
        <div className="state-label">{state}</div><h1>{status}</h1>
        {state === 'speaking' && response && <div className="live-subtitles" aria-live="polite">{response.split(/\s+/).slice(Math.max(0, subtitleWord - 5), subtitleWord + 7).map((word, index) => { const absolute = Math.max(0, subtitleWord - 5) + index; return <span key={`${absolute}-${word}`} className={absolute === subtitleWord ? 'current' : absolute < subtitleWord ? 'spoken' : ''}>{word} </span>})}</div>}
        <Tooltip label={state === 'listening' ? 'Stop recording' : 'Hold a spoken turn'}
          detail={state === 'listening'
            ? 'Recording now. Audio is transcribed on this machine by the local Whisper model and never uploaded.'
            : 'Records a spoken turn, transcribes it locally, and answers out loud. Requires the voice extra; without it, type instead.'}>
        <button className={`talk-button ${state === 'listening' ? 'recording' : ''}`} disabled={!['idle','listening','error'].includes(state)} onClick={state === 'listening' ? stopListening : beginListening}>{state === 'listening' ? <Square size={21}/> : <Mic size={23}/>}<span>{state === 'listening' ? 'Finish' : 'Talk'}</span></button></Tooltip>
      </section>
      <section className="talk-dialogue">
        <FacultiesPanel/>
        {/* What the turn actually drew on, named before the answer rather than after it. A
            spoken answer sourced from the user's own notes and one invented wholesale sound
            identical; this is the only thing that tells them apart. */}
        {(recalled.notes.length > 0 || recalled.memories.length > 0) && <div className="recalled">
          {recalled.notes.length > 0 && <p>
            <NotebookPen size={14}/>
            <span>
              <b>Read from your notes</b>
              {recalled.notes.map(path => <code key={path}>{path}</code>)}
            </span>
          </p>}
          {recalled.memories.length > 0 && <p>
            <Brain size={14}/>
            <span>
              <b>Recalled</b>
              {recalled.memories.map(item => <small key={item}>{item}</small>)}
            </span>
          </p>}
        </div>}
        <EvidencePanel events={runEvents} compact title="Conversation evidence"/>
        {(transcript || response) && <div className="voice-conversation">{transcript && <div className="voice-turn user-turn"><small>YOU SAID</small><p>{transcript}</p></div>}{response && <div className="voice-turn agent-turn"><small>YUKTI</small><p><LinkedText text={response}/></p></div>}</div>}
        {videoUrl && <div className="visual-player"><div><Video size={16}/> Visual explanation</div><video src={videoUrl} controls autoPlay/></div>}
        {imageUrl && <div className="talk-image"><div><Image size={16}/> Generated image</div><img src={imageUrl} alt="Generated by YUKTI"/></div>}
        {error && <div className="talk-error">{error}</div>}
      </section>
    </main>
    <form className="talk-composer" onSubmit={submitText}>
      {attachments.length > 0 && <div className="talk-attachments">{attachments.map(item => <span key={item.id}><FileText size={13}/>{item.name}<button type="button" aria-label={`Remove ${item.name}`} onClick={() => setAttachments(current => current.filter(x => x.id !== item.id))}><X size={12}/></button></span>)}</div>}
      <textarea value={text} onChange={e => setText(e.target.value)} placeholder="Message YUKTI…" rows={1} disabled={state === 'thinking'} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit() } }}/>
      <div className="talk-composer-actions">
        <input ref={fileRef} hidden type="file" multiple accept=".pdf,.docx,.txt,.md,.py,.js,.ts,.json,.csv" onChange={e => pickFiles(e.target.files)}/>
        <button type="button" className="talk-tool" title="Attach documents" aria-label="Attach documents" disabled={uploading || state === 'thinking'} onClick={() => fileRef.current?.click()}><Paperclip size={18}/></button>
        <div className="talk-modes" role="group" aria-label="Response mode">{modes.map(item => <button type="button" key={item.id} className={mode === item.id ? 'selected' : ''} aria-pressed={mode === item.id} onClick={() => setMode(item.id)}><item.icon size={14}/><span>{item.label}</span></button>)}</div>
        <span className="talk-grow"/><button className="talk-send" aria-label="Send message" disabled={!text.trim() || uploading || state === 'thinking' || state === 'connecting'}><Send size={17}/></button>
      </div>
      <small>{uploading ? 'Uploading securely to the local workspace…' : 'YUKTI runs entirely on this machine and can make mistakes. Verify anything that matters.'}</small>
    </form>
  </div>
}
