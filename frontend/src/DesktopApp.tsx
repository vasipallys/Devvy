import { useState } from 'react'
import { App as ChatApp } from './App'
import { ErrorBoundary } from './ErrorBoundary'
import { EstimateCodeScreen } from './EstimateCodeScreen'
import { HomeScreen } from './HomeScreen'
import { SmartCodeScreen } from './SmartCodeScreen'
import { TalkScreen } from './TalkScreen'

type Page = 'home' | 'chat' | 'talk' | 'smart-code' | 'estimate-code'

export function DesktopApp() {
  const [page, setPage] = useState<Page>('home')
  const home = () => setPage('home')
  // Keyed per page so navigating away from a crashed screen clears the boundary, and a
  // crash in one workspace can never blank out the others.
  return <ErrorBoundary key={page} onHome={page === 'home' ? undefined : home}>
    {page === 'chat' ? <ChatApp onHome={home} />
      : page === 'talk' ? <TalkScreen onHome={home} />
      : page === 'smart-code' ? <SmartCodeScreen onHome={home} />
      : page === 'estimate-code' ? <EstimateCodeScreen onHome={home} />
      : <HomeScreen onChat={() => setPage('chat')} onTalk={() => setPage('talk')} onSmartCode={() => setPage('smart-code')} onEstimateCode={() => setPage('estimate-code')} />}
  </ErrorBoundary>
}
