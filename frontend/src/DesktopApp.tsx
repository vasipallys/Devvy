import { useState } from 'react'
import { App as ChatApp } from './App'
import { EstimateCodeScreen } from './EstimateCodeScreen'
import { HomeScreen } from './HomeScreen'
import { SmartCodeScreen } from './SmartCodeScreen'
import { TalkScreen } from './TalkScreen'

type Page = 'home' | 'chat' | 'talk' | 'smart-code' | 'estimate-code'

export function DesktopApp() {
  const [page, setPage] = useState<Page>('home')
  if (page === 'chat') return <ChatApp onHome={() => setPage('home')} />
  if (page === 'talk') return <TalkScreen onHome={() => setPage('home')} />
  if (page === 'smart-code') return <SmartCodeScreen onHome={() => setPage('home')} />
  if (page === 'estimate-code') return <EstimateCodeScreen onHome={() => setPage('home')} />
  return <HomeScreen onChat={() => setPage('chat')} onTalk={() => setPage('talk')} onSmartCode={() => setPage('smart-code')} onEstimateCode={() => setPage('estimate-code')} />
}
