import { useState } from 'react'
import { ActivityScreen } from './ActivityScreen'
import { App as ChatApp } from './App'
import { ErrorBoundary } from './ErrorBoundary'
import { EstimateCodeScreen } from './EstimateCodeScreen'
import { HomeScreen } from './HomeScreen'
import { SmartCodeScreen } from './SmartCodeScreen'
import { TalkScreen } from './TalkScreen'
import { useJobs } from './useJobs'

type Page = 'home' | 'chat' | 'talk' | 'smart-code' | 'estimate-code' | 'activity'

export function DesktopApp() {
  const [page, setPage] = useState<Page>('home')
  const [openConversation, setOpenConversation] = useState<string>()
  // Mounted once at the shell so the close guard and the activity badge apply on every
  // screen, not only where a request happened to be started.
  const { active } = useJobs()
  const home = () => setPage('home')

  // Keyed per page so navigating away from a crashed screen clears the boundary, and a
  // crash in one workspace can never blank out the others.
  return <ErrorBoundary key={page} onHome={page === 'home' ? undefined : home}>
    {page === 'chat' ? <ChatApp onHome={home} initialConversationId={openConversation} />
      : page === 'talk' ? <TalkScreen onHome={home} />
      : page === 'smart-code' ? <SmartCodeScreen onHome={home} />
      : page === 'estimate-code' ? <EstimateCodeScreen onHome={home} />
      : page === 'activity' ? <ActivityScreen onHome={home} onOpenConversation={id => {
        setOpenConversation(id); setPage('chat')
      }} />
      : <HomeScreen
          activeJobs={active}
          onChat={() => { setOpenConversation(undefined); setPage('chat') }}
          onTalk={() => setPage('talk')}
          onSmartCode={() => setPage('smart-code')}
          onEstimateCode={() => setPage('estimate-code')}
          onActivity={() => setPage('activity')}
        />}
  </ErrorBoundary>
}
