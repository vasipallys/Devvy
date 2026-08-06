import { ActivityScreen } from './ActivityScreen'
import { App as ChatApp } from './App'
import { ErrorBoundary } from './ErrorBoundary'
import { EstimateCodeScreen } from './EstimateCodeScreen'
import { HomeScreen } from './HomeScreen'
import { SmartCodeScreen } from './SmartCodeScreen'
import { TalkScreen } from './TalkScreen'
import { useJobs } from './useJobs'
import { useRoute } from './useRoute'

export function DesktopApp() {
  // Pages come from the URL hash, so Back works, a reload keeps its place, and an estimate
  // or conversation can be linked to a colleague.
  const { route, navigate } = useRoute()
  // Mounted once at the shell so the close guard and the activity badge apply on every
  // screen, not only where a request happened to be started.
  const { active } = useJobs()
  const home = () => navigate({ page: 'home' })

  // Keyed per page so navigating away from a crashed screen clears the boundary, and a
  // crash in one workspace can never blank out the others.
  return <ErrorBoundary key={route.page} onHome={route.page === 'home' ? undefined : home}>
    {route.page === 'chat'
      ? <ChatApp
          onHome={home}
          initialConversationId={route.id}
          onConversationChange={id => navigate({ page: 'chat', id }, true)}
        />
      : route.page === 'talk' ? <TalkScreen onHome={home} />
      : route.page === 'smart-code' ? <SmartCodeScreen onHome={home} />
      : route.page === 'estimate-code'
        ? <EstimateCodeScreen
            onHome={home}
            initialView={route.view === 'history' ? 'history' : 'new'}
            initialHistoryId={route.view === 'history' ? route.id : undefined}
            onViewChange={(view, id) =>
              navigate({ page: 'estimate-code', view: view === 'history' ? 'history' : undefined, id }, true)}
          />
      : route.page === 'activity'
        ? <ActivityScreen onHome={home}
            onOpenConversation={id => navigate({ page: 'chat', id })} />
      : <HomeScreen
          activeJobs={active}
          onChat={() => navigate({ page: 'chat' })}
          onTalk={() => navigate({ page: 'talk' })}
          onSmartCode={() => navigate({ page: 'smart-code' })}
          onEstimateCode={() => navigate({ page: 'estimate-code' })}
          onActivity={() => navigate({ page: 'activity' })}
        />}
  </ErrorBoundary>
}
