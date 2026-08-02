import { useEffect, useState } from 'react'

export type AvatarState = 'connecting' | 'idle' | 'listening' | 'thinking' | 'speaking' | 'error'

const EYE_SHAPE = 'M 82 120 Q 94 107.5 109 118 Q 95 130.5 82 120 Z'

const EXPRESSIONS: Record<AvatarState, { lid: number; smile: number; px: number; py: number }> = {
  connecting: { lid: .8, smile: 1.5, px: 0, py: 1.5 },
  idle: { lid: .1, smile: 3, px: 0, py: .3 },
  listening: { lid: 0, smile: 2.4, px: 0, py: .6 },
  thinking: { lid: .45, smile: 1.2, px: -2.6, py: -3 },
  speaking: { lid: .08, smile: 1.8, px: 0, py: .4 },
  error: { lid: .3, smile: -2.2, px: 0, py: 1.8 },
}

function mouthGeometry(open: number, wide: number, smile: number) {
  const hw = 13 + wide * 5 - open * 2
  const lift = smile * 1.3 * (1 - open * .7)
  const cx = 120, cy = 170 - Math.max(0, lift) * .35
  const Lx = cx - hw, Rx = cx + hw, Ly = cy - lift
  const oTop = open * 9, oBot = open * 15
  const inner = `M ${Lx} ${Ly} Q ${cx} ${cy - oTop} ${Rx} ${Ly} Q ${cx} ${cy + oBot} ${Lx} ${Ly} Z`
  const upTop = Ly - 4.6 - open * 1.2
  const upper = `M ${Lx - 1.5} ${Ly} Q ${cx - hw * .5} ${upTop - 1.2} ${cx - 3.4} ${upTop}`
    + ` Q ${cx} ${upTop + 1.7} ${cx + 3.4} ${upTop} Q ${cx + hw * .5} ${upTop - 1.2} ${Rx + 1.5} ${Ly}`
    + ` Q ${cx} ${cy - oTop} ${Lx - 1.5} ${Ly} Z`
  const lowBot = cy + oBot + 4.3 + open * 1.6
  const lower = `M ${Lx - 1} ${Ly} Q ${cx} ${cy + oBot} ${Rx + 1} ${Ly}`
    + ` Q ${cx + hw * .7} ${lowBot} ${cx} ${lowBot} Q ${cx - hw * .7} ${lowBot} ${Lx - 1} ${Ly} Z`
  const topApexY = (Ly + cy - oTop) / 2
  const botApexY = (Ly + cy + oBot) / 2
  const teeth = `M ${cx - hw * .72} ${topApexY - 1} h ${hw * 1.44} v ${Math.min(3.2, oTop * .9 + 1)} q ${-hw * .72} 1.6 ${-hw * 1.44} 0 Z`
  const curl = Math.max(0, lift) * .8
  const curls = curl > .5
    ? `M ${Lx - .5} ${Ly} q -3 -.4 -3.8 -${curl} M ${Rx + .5} ${Ly} q 3 -.4 3.8 -${curl}`
    : ''
  return { inner, upper, lower, teeth, curls, showTeeth: open > .2, showTongue: open > .55, tongueY: botApexY - .5, tongueR: hw * .42 }
}

function Eye({ mirror, lid, px, py }: { mirror?: boolean; lid: number; px: number; py: number }) {
  const id = mirror ? 'eyeClipR' : 'eyeClipL'
  return <g transform={mirror ? 'translate(240 0) scale(-1 1)' : undefined}>
    <clipPath id={id}><path d={EYE_SHAPE}/></clipPath>
    <path d={EYE_SHAPE} fill="#fdf6ee"/>
    <g clipPath={`url(#${id})`}>
      <g style={{ transform: `translate(${mirror ? -px : px}px, ${py}px)`, transition: 'transform .35s ease' }}>
        <circle cx={96} cy={119.5} r={7.2} fill="url(#angelIris)"/>
        <circle cx={96} cy={119.5} r={7.2} fill="none" stroke="#d8e8b0" strokeOpacity={.5} strokeWidth={.7}/>
        <circle cx={96} cy={119.5} r={5.2} fill="none" stroke="#20301a" strokeOpacity={.55} strokeWidth={.6}/>
        <circle cx={96} cy={119.5} r={3.2} fill="#15120f"/>
        <circle cx={93.4} cy={116.6} r={1.9} fill="#fff" opacity={.92}/>
        <circle cx={99.2} cy={122} r={1} fill="#fff" opacity={.55}/>
      </g>
      <rect x={78} y={100} width={36} height={30} fill="url(#angelLid)"
        style={{ transform: `translateY(${(lid - 1) * 26}px)`, transition: 'transform .09s ease-out' }}/>
    </g>
    <path d={EYE_SHAPE} fill="none" stroke="#3a241c" strokeOpacity={.3} strokeWidth={.8}/>
    <path d="M 82 120 Q 94 107.5 109 118" fill="none" stroke="#241511" strokeWidth={2.5} strokeLinecap="round"/>
    <path d="M 83 119.2 q -3.6 -1.2 -5.6 -3.6" stroke="#241511" strokeWidth={1.7} fill="none" strokeLinecap="round"/>
    <path d="M 84 121.6 q -4 .3 -6.4 -1.1" stroke="#241511" strokeWidth={1.3} fill="none" strokeLinecap="round" opacity={.8}/>
  </g>
}

export function AngelAvatar({ state, mouthOpen, mouthWide }: { state: AvatarState; mouthOpen: number; mouthWide: number }) {
  const [blink, setBlink] = useState(false)
  const [glance, setGlance] = useState({ x: 0, y: 0 })

  useEffect(() => {
    let alive = true
    let timer = 0
    const schedule = () => {
      timer = window.setTimeout(() => {
        if (!alive) return
        setBlink(true)
        window.setTimeout(() => alive && setBlink(false), 130)
        if (Math.random() < .2) {
          window.setTimeout(() => alive && setBlink(true), 300)
          window.setTimeout(() => alive && setBlink(false), 430)
        }
        schedule()
      }, 2200 + Math.random() * 3600)
    }
    schedule()
    return () => { alive = false; clearTimeout(timer) }
  }, [])

  useEffect(() => {
    if (state !== 'idle' && state !== 'speaking') { setGlance({ x: 0, y: 0 }); return }
    const timer = window.setInterval(() => {
      setGlance(Math.random() < .4 ? { x: (Math.random() * 2 - 1) * 2.2, y: (Math.random() - .4) * 1.4 } : { x: 0, y: 0 })
    }, 2800)
    return () => clearInterval(timer)
  }, [state])

  const face = EXPRESSIONS[state]
  const lid = blink ? 1 : face.lid
  const talking = state === 'speaking'
  const mouth = mouthGeometry(talking ? mouthOpen : 0, talking ? mouthWide : (state === 'thinking' ? .2 : .35), face.smile)

  return <div className={`angel-avatar state-${state}`} style={{ '--energy': talking ? mouthOpen : 0 } as React.CSSProperties}
    role="img" aria-label="Devvy, your robotic angel companion">
    <svg viewBox="0 0 240 300" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <radialGradient id="angelSkin" cx="50%" cy="36%" r="72%">
          <stop offset="0%" stopColor="#f9ddc4"/><stop offset="62%" stopColor="#efc6a6"/><stop offset="100%" stopColor="#dda486"/>
        </radialGradient>
        <linearGradient id="angelLid" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#eec3a3"/><stop offset="88%" stopColor="#e4b491"/><stop offset="100%" stopColor="#b5765c"/>
        </linearGradient>
        <linearGradient id="angelHair" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#43301f"/><stop offset="45%" stopColor="#2a1c13"/><stop offset="100%" stopColor="#191009"/>
        </linearGradient>
        <linearGradient id="angelHalo" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#ffeeb0"/><stop offset="100%" stopColor="#eec565"/>
        </linearGradient>
        <radialGradient id="angelIris" cx="42%" cy="38%" r="70%">
          <stop offset="0%" stopColor="#b9cf7f"/><stop offset="55%" stopColor="#6d8a4c"/><stop offset="100%" stopColor="#33481f"/>
        </radialGradient>
        <linearGradient id="angelLips" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#d68184"/><stop offset="100%" stopColor="#a9525d"/>
        </linearGradient>
        <linearGradient id="angelRobe" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#2b4634"/><stop offset="100%" stopColor="#152218"/>
        </linearGradient>
        <radialGradient id="angelPod" cx="35%" cy="30%" r="80%">
          <stop offset="0%" stopColor="#55654a"/><stop offset="100%" stopColor="#232b1e"/>
        </radialGradient>
        <filter id="angelGlow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="2.6" result="blur"/>
          <feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
        <clipPath id="mouthClip"><path d={mouth.inner}/></clipPath>
      </defs>

      {/* aura + wings */}
      <circle cx={120} cy={148} r={116} fill="rgba(183,239,153,.05)"/>
      <g className="angel-wing" filter="url(#angelGlow)" stroke="url(#angelHalo)" fill="none" strokeLinecap="round">
        <path d="M 58 240 C 24 216 14 182 26 146" strokeWidth={2}/>
        <path d="M 66 250 C 34 234 22 206 26 176" strokeWidth={1.4}/>
        <path d="M 52 228 C 28 204 26 180 36 156" strokeWidth={1}/>
      </g>
      <g className="angel-wing" filter="url(#angelGlow)" stroke="url(#angelHalo)" fill="none" strokeLinecap="round" transform="translate(240 0) scale(-1 1)">
        <path d="M 58 240 C 24 216 14 182 26 146" strokeWidth={2}/>
        <path d="M 66 250 C 34 234 22 206 26 176" strokeWidth={1.4}/>
        <path d="M 52 228 C 28 204 26 180 36 156" strokeWidth={1}/>
      </g>

      {/* neck + robe */}
      <path d="M 106 178 C 108 206 110 214 102 224 L 138 224 C 130 214 132 206 134 178 Z" fill="#e2b294"/>
      <path d="M 106 184 C 112 197 128 197 134 184 C 131 204 109 204 106 184 Z" fill="rgba(96,42,24,.3)"/>
      <path d="M 30 300 C 40 248 70 224 98 214 L 120 231 L 142 214 C 170 224 200 248 210 300 Z" fill="url(#angelRobe)" stroke="#33503c" strokeWidth={1}/>
      <path d="M 98 214 L 120 231 L 142 214" fill="none" stroke="#caa75f" strokeWidth={1.3} strokeOpacity={.85}/>
      <path d="M 104 220 L 120 262 L 136 220" fill="none" stroke="#caa75f" strokeWidth={.7} strokeOpacity={.4}/>
      <path d="M 104 209.5 h 32 M 105.5 214 h 29" stroke="#b7ef99" strokeOpacity={.35} strokeWidth={1}/>
      <path className="angel-pendant" d="M 120 240 L 125.5 246.5 L 120 253 L 114.5 246.5 Z" fill="#d9ffc5" stroke="#f5ffe9" strokeWidth={.7} filter="url(#angelGlow)"/>

      {/* head (sways as a unit) */}
      <g className="angel-head">
        {/* back hair */}
        <path d="M 120 38 C 68 40 50 88 56 134 C 60 174 52 208 44 242 C 72 256 100 252 112 240 C 100 214 96 190 98 168 L 142 168 C 144 190 140 214 128 240 C 140 252 168 256 196 242 C 188 208 180 174 184 134 C 190 88 172 40 120 38 Z" fill="url(#angelHair)"/>
        <path d="M 68 92 C 58 124 62 164 56 196" stroke="url(#angelHalo)" strokeWidth={1.5} fill="none" opacity={.3}/>
        <path d="M 172 92 C 182 124 178 164 184 196" stroke="url(#angelHalo)" strokeWidth={1.5} fill="none" opacity={.3}/>

        {/* face */}
        <path d="M 120 60 C 152 60 169 82 169 112 C 169 140 158 166 140 184 C 132 192 108 192 100 184 C 82 166 71 140 71 112 C 71 82 88 60 120 60 Z" fill="url(#angelSkin)"/>

        {/* ear pods (robotic) */}
        <circle cx={70} cy={126} r={6} fill="url(#angelPod)" stroke="#45543d" strokeWidth={1}/>
        <circle className="angel-pod-dot" cx={70} cy={126} r={2.1} fill="#d9ffc5" filter="url(#angelGlow)"/>
        <circle cx={170} cy={126} r={6} fill="url(#angelPod)" stroke="#45543d" strokeWidth={1}/>
        <circle className="angel-pod-dot" cx={170} cy={126} r={2.1} fill="#d9ffc5" filter="url(#angelGlow)"/>

        {/* temple circuits */}
        <g stroke="#b7ef99" strokeWidth={1} strokeOpacity={.3} fill="none">
          <path d="M 76 100 l -3.5 10 l 2.5 9"/><path d="M 164 100 l 3.5 10 l -2.5 9"/>
        </g>
        <circle cx={75} cy={119} r={1.2} fill="#b7ef99" opacity={.55}/>
        <circle cx={165} cy={119} r={1.2} fill="#b7ef99" opacity={.55}/>

        {/* blush */}
        <ellipse className="angel-blush" cx={90} cy={150} rx={9.5} ry={5} fill="#e07a68" opacity={.16}/>
        <ellipse className="angel-blush" cx={150} cy={150} rx={9.5} ry={5} fill="#e07a68" opacity={.16}/>

        {/* brows */}
        <path className="angel-brow brow-l" d="M 76 104 Q 88 96.5 101 101.5" fill="none" stroke="#2c1b13" strokeWidth={2.6} strokeLinecap="round"/>
        <path className="angel-brow brow-r" d="M 164 104 Q 152 96.5 139 101.5" fill="none" stroke="#2c1b13" strokeWidth={2.6} strokeLinecap="round"/>

        {/* eyes */}
        <Eye lid={lid} px={face.px + glance.x} py={face.py + glance.y}/>
        <Eye mirror lid={lid} px={face.px + glance.x} py={face.py + glance.y}/>

        {/* nose */}
        <path d="M 120.5 136 q -2 8 -4 10.6 q 3.4 2.8 7.6 .4" fill="none" stroke="#c88f6f" strokeWidth={1.3} strokeOpacity={.55} strokeLinecap="round"/>

        {/* mouth with lip sync */}
        <path d={mouth.inner} fill="#571f22"/>
        <g clipPath="url(#mouthClip)">
          {mouth.showTeeth && <path d={mouth.teeth} fill="#f8f2e7"/>}
          {mouth.showTongue && <ellipse cx={120} cy={mouth.tongueY} rx={mouth.tongueR} ry={2.6} fill="#c25f63"/>}
        </g>
        <path d={mouth.upper} fill="url(#angelLips)" stroke="#8e3f47" strokeWidth={.5} strokeOpacity={.5}/>
        <path d={mouth.lower} fill="url(#angelLips)" stroke="#8e3f47" strokeWidth={.5} strokeOpacity={.4}/>
        {mouth.curls && <path d={mouth.curls} fill="none" stroke="#a25055" strokeWidth={1.1} strokeLinecap="round" opacity={.55}/>}

        {/* forehead gem */}
        <path className="angel-gem" d="M 120 68.4 L 124 73 L 120 77.6 L 116 73 Z" fill="#d9ffc5" stroke="#fff3c9" strokeWidth={.6} filter="url(#angelGlow)"/>

        {/* front hair: crown + parted bangs */}
        <path d="M 71 112 C 68 68 90 44 120 44 C 150 44 172 68 169 112 C 164 78 148 60 120 60 C 92 60 76 78 71 112 Z" fill="url(#angelHair)"/>
        <path d="M 121 50 C 99 53 84 68 77 94 C 74 106 73 114 71 122 C 86 110 95 92 105 78 C 111 69 116 59 121 50 Z" fill="url(#angelHair)"/>
        <path d="M 121 50 C 99 53 84 68 77 94 C 74 106 73 114 71 122 C 86 110 95 92 105 78 C 111 69 116 59 121 50 Z" fill="url(#angelHair)" transform="translate(240 0) scale(-1 1)"/>
        <path d="M 112 54 C 96 62 86 76 80 98" stroke="#caa15f" strokeWidth={.9} fill="none" opacity={.4}/>
        <path d="M 128 54 C 144 62 154 76 160 98" stroke="#caa15f" strokeWidth={.9} fill="none" opacity={.4}/>
      </g>

      {/* halo */}
      <g className="angel-halo" filter="url(#angelGlow)">
        <ellipse cx={120} cy={26} rx={40} ry={9} fill="none" stroke="url(#angelHalo)" strokeWidth={2.4} transform="rotate(-3 120 26)"/>
        <ellipse cx={120} cy={26} rx={40} ry={9} fill="none" stroke="#fff4cd" strokeWidth={.8} opacity={.8} transform="rotate(-3 120 26)"/>
        <circle className="angel-sparkle" cx={86} cy={20} r={1.2} fill="#ffedb2"/>
        <circle className="angel-sparkle" cx={152} cy={30} r={1} fill="#ffedb2"/>
        <circle className="angel-sparkle" cx={121} cy={13} r={.9} fill="#ffedb2"/>
      </g>
    </svg>
  </div>
}
