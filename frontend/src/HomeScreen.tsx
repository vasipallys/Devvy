import { ArrowRight, BrainCircuit, Code2, LockKeyhole, MessageSquare, Mic2, Sparkles } from 'lucide-react'

type Props = { onChat: () => void; onTalk: () => void; onSmartCode: () => void; onEstimateCode: () => void }

export function HomeScreen({ onChat, onTalk, onSmartCode, onEstimateCode }: Props) {
  return <div className="home-screen">
    <div className="home-glow" />
    <header className="home-header"><div className="brand-mark"><Sparkles size={18}/></div><b>Gemma Studio</b><span><LockKeyhole size={13}/> Private & local</span></header>
    <main className="home-content">
      <div className="home-kicker">YOUR LOCAL AI WORKSPACE</div>
      <h1>What would you like<br/>to build today?</h1>
      <p>Chat, talk, engineer production code, or create a defensible estimate — all with one private local Gemma runtime.</p>
      <div className="choice-grid">
        <button className="choice-card chat-choice" onClick={onChat}><div className="choice-icon"><MessageSquare size={28}/></div><div><small>FOCUS & CREATE</small><h2>Chat</h2><p>Write code, analyze documents, research the web, and build ideas.</p></div><ArrowRight className="choice-arrow"/></button>
        <button className="choice-card talk-choice" onClick={onTalk}><div className="choice-icon"><Mic2 size={28}/></div><div><small>SPEAK & EXPLORE</small><h2>Talk</h2><p>Have a hands-free conversation with voice, audio, and visual explanations.</p></div><ArrowRight className="choice-arrow"/></button>
        <button className="choice-card smart-choice" onClick={onSmartCode}><div className="choice-icon"><Code2 size={28}/></div><div><small>PLAN · CODE · VERIFY</small><h2>Smart Code</h2><p>Make repository-aware changes with diff review, verification, and an explicit write gate.</p></div><ArrowRight className="choice-arrow"/></button>
        <button className="choice-card estimate-choice" onClick={onEstimateCode}><div className="choice-icon"><BrainCircuit size={28}/></div><div><small>SCORE · EXPLAIN · ALIGN</small><h2>Estimate Code</h2><p>Build evidence-led story points from delivery factors, anchors, risks, and hidden work.</p></div><ArrowRight className="choice-arrow"/></button>
      </div>
    </main>
    <footer className="home-footer">Gemma runs on this machine · Your work stays private</footer>
  </div>
}
