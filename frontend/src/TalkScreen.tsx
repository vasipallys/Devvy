import { FormEvent, useEffect, useRef, useState } from 'react'
import { ArrowLeft, Code2, FileText, Globe2, Image, MessageSquare, Mic, Paperclip, RotateCcw, Send, Sparkles, Square, Video, X } from 'lucide-react'
import { api, API } from './api'
import { EvidencePanel } from './EvidencePanel'
import { Tooltip } from './Tooltip'
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

function GeometricAgentFace({ mouthOpen, speaking }: { mouthOpen: number; speaking: boolean }) {
  return <div className={`agent-portrait ${speaking ? 'portrait-speaking' : ''}`} role="img" aria-label="Devvy, your angelic voice companion">
    <img src={robotGirl} alt="Devvy feminine robotic companion" draggable={false}/>
    <span className="portrait-mouth" style={{ transform: `translate(-50%,-50%) scale(${1 + mouthOpen * .12},${.18 + mouthOpen * 1.3})`, opacity: .2 + mouthOpen * .75 }}/>
    <span className="portrait-halo"/>
  </div>
}

/** The orbit is Talk's only status display, so each state says what is happening and why. */
const STATE_WHY: Record<string, { label: string; why: string }> = {
  connecting: { label: 'Connecting', why: 'Opening the WebSocket to the local backend. Talk keeps its history in this connection only — nothing is written to disk.' },
  idle: { label: 'Ready', why: 'Waiting for you. Speak by pressing Talk, or type — both take the same path through the agent.' },
  listening: { label: 'Listening', why: 'Recording your turn. Transcription runs locally on this machine; no audio leaves it.' },
  thinking: { label: 'Thinking', why: 'The agent is deciding whether this turn needs live research or a rendered animation, then generating. On a CPU model this is the slow part.' },
  speaking: { label: 'Speaking', why: 'Reading the answer aloud with the local voice while the text streams alongside it.' },
  error: { label: 'Error', why: 'Something in the turn failed. The reason is shown rather than hidden, and the session stays open so you can try again.' },
}

export function TalkScreen({ onHome }: { onHome: () => void }) {
  const [state, setState] = useState<AgentState>('connecting')
  const [transcript, setTranscript] = useState('')
  const [response, setResponse] = useState('')
  const [status, setStatus] = useState('Connecting to your local companion…')
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
    const audio = new Audio(API + url); audio.crossOrigin = 'anonymous'; audioRef.current = audio
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
    audio.onended = () => { audioRef.current = null; stopAudioAnalysis(); setSubtitleWord(words.length); setState('idle'); setStatus('Ready when you are') }
    audio.onerror = () => { audioRef.current = null; stopAudioAnalysis(); setError('The generated voice audio could not be played.') }
    context.resume().then(() => audio.play()).catch(e => setError(`Audio playback failed: ${e.message}`))
  }

  useEffect(() => {
    const endpoint = API.replace(/^http/, 'ws') + '/api/talk/ws'
    let disposed = false; let attempts = 0; let retryTimer: number | undefined
    const connect = () => {
      if (disposed) return
      const socket = new WebSocket(endpoint); socketRef.current = socket
      socket.onopen = () => { attempts = 0; setError(''); setState('idle'); setStatus('Ready when you are') }
      socket.onmessage = event => {
      const data = JSON.parse(event.data)
      if (data.type === 'state') {
        if (!(data.value === 'idle' && audioRef.current && !audioRef.current.paused)) setState(data.value)
        if (data.value === 'thinking') { stopAgentAudio(); setStatus('Devvy is thinking locally…') }
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
    <header className="talk-header"><button onClick={onHome}><ArrowLeft size={18}/> Home</button><div><Sparkles size={18}/><b>Talk with Devvy</b><span>Local voice companion</span></div><button onClick={reset}><RotateCcw size={16}/> Reset</button></header>
    <main className="talk-main">
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
        <EvidencePanel events={runEvents} compact title="Conversation evidence"/>
        {(transcript || response) && <div className="voice-conversation">{transcript && <div className="voice-turn user-turn"><small>YOU SAID</small><p>{transcript}</p></div>}{response && <div className="voice-turn agent-turn"><small>DEVVY</small><p><LinkedText text={response}/></p></div>}</div>}
        {videoUrl && <div className="visual-player"><div><Video size={16}/> Visual explanation</div><video src={videoUrl} controls autoPlay/></div>}
        {imageUrl && <div className="talk-image"><div><Image size={16}/> Generated image</div><img src={imageUrl} alt="Generated by Devvy"/></div>}
        {error && <div className="talk-error">{error}</div>}
      </section>
    </main>
    <form className="talk-composer" onSubmit={submitText}>
      {attachments.length > 0 && <div className="talk-attachments">{attachments.map(item => <span key={item.id}><FileText size={13}/>{item.name}<button type="button" aria-label={`Remove ${item.name}`} onClick={() => setAttachments(current => current.filter(x => x.id !== item.id))}><X size={12}/></button></span>)}</div>}
      <textarea value={text} onChange={e => setText(e.target.value)} placeholder="Message Devvy…" rows={1} disabled={state === 'thinking'} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit() } }}/>
      <div className="talk-composer-actions">
        <input ref={fileRef} hidden type="file" multiple accept=".pdf,.docx,.txt,.md,.py,.js,.ts,.json,.csv" onChange={e => pickFiles(e.target.files)}/>
        <button type="button" className="talk-tool" title="Attach documents" aria-label="Attach documents" disabled={uploading || state === 'thinking'} onClick={() => fileRef.current?.click()}><Paperclip size={18}/></button>
        <div className="talk-modes" role="group" aria-label="Response mode">{modes.map(item => <button type="button" key={item.id} className={mode === item.id ? 'selected' : ''} aria-pressed={mode === item.id} onClick={() => setMode(item.id)}><item.icon size={14}/><span>{item.label}</span></button>)}</div>
        <span className="talk-grow"/><button className="talk-send" aria-label="Send message" disabled={!text.trim() || uploading || state === 'thinking' || state === 'connecting'}><Send size={17}/></button>
      </div>
      <small>{uploading ? 'Uploading securely to the local workspace…' : 'Devvy runs locally and can make mistakes. Verify important information.'}</small>
    </form>
  </div>
}
