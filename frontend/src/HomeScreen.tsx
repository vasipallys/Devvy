import { ArrowRight, BrainCircuit, Check, Code2, Database, LockKeyhole, MessageSquare, Mic2, ShieldCheck, Sparkles } from 'lucide-react'
import { SystemStatusChip } from './SystemStatusChip'

type Props = { onChat: () => void; onTalk: () => void; onSmartCode: () => void; onEstimateCode: () => void }

export function HomeScreen({ onChat, onTalk, onSmartCode, onEstimateCode }: Props) {
  return <div className="home-screen">
    <div className="home-glow" />
    <header className="home-header"><div className="brand-lockup"><div className="brand-mark"><Sparkles size={18}/></div><span><b>Gemma Studio</b><small>Local agent workspace</small></span></div><SystemStatusChip/></header>
    <main className="home-content">
      <div className="home-kicker"><ShieldCheck/> PRIVATE BY ARCHITECTURE</div>
      <h1>One local intelligence.<br/><em>Four focused workflows.</em></h1>
      <p>Move from idea to evidence-backed outcome. Every run shows the context used, checks performed, and actions waiting for your approval.</p>
      <div className="choice-grid">
        <button className="choice-card chat-choice" onClick={onChat}><div className="choice-top"><div className="choice-icon"><MessageSquare size={23}/></div><span className="choice-badge">Conversational</span></div><div><small>ASK · CREATE · RESEARCH</small><h2>Chat</h2><p>Think through work with grounded documents, cited research, and production-minded code.</p></div><span className="choice-proof"><Check/> Persistent local history</span><ArrowRight className="choice-arrow"/></button>
        <button className="choice-card talk-choice" onClick={onTalk}><div className="choice-top"><div className="choice-icon"><Mic2 size={23}/></div><span className="choice-badge">Real-time</span></div><div><small>SPEAK · SEE · UNDERSTAND</small><h2>Talk</h2><p>A low-friction voice workspace with streaming text and optional local media.</p></div><span className="choice-proof"><Check/> Session-only memory</span><ArrowRight className="choice-arrow"/></button>
        <button className="choice-card smart-choice" onClick={onSmartCode}><div className="choice-top"><div className="choice-icon"><Code2 size={23}/></div><span className="choice-badge">Human-gated</span></div><div><small>RETRIEVE · CODE · VERIFY</small><h2>Smart Code</h2><p>Build repository-aware changes with provenance, structural checks, and diff approval.</p></div><span className="choice-proof"><Check/> No writes before approval</span><ArrowRight className="choice-arrow"/></button>
        <button className="choice-card estimate-choice" onClick={onEstimateCode}><div className="choice-top"><div className="choice-icon"><BrainCircuit size={23}/></div><span className="choice-badge">Defensible</span></div><div><small>SCORE · CALIBRATE · ALIGN</small><h2>Estimate Code</h2><p>Turn story evidence into explainable points, risks, hidden work, and split guidance.</p></div><span className="choice-proof"><Check/> 12-factor policy validation</span><ArrowRight className="choice-arrow"/></button>
      </div>
      <section className="trust-strip" aria-label="Privacy and evidence"><div><LockKeyhole/><span><b>Private by default</b><small>Inference and work stay on this machine</small></span></div><div><Database/><span><b>Evidence, not mystery</b><small>See context, checks, loops, and gates</small></span></div><div><ShieldCheck/><span><b>You stay in control</b><small>External and write actions are explicit</small></span></div></section>
    </main>
    <footer className="home-footer">Gemma can make mistakes. Evidence helps you decide what to trust.</footer>
  </div>
}
