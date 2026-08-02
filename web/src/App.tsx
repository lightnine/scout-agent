import { ApprovalModal } from './components/ApprovalModal'
import { ChatPanel } from './components/ChatPanel'
import { ErrorBanner } from './components/ErrorBanner'
import { EventPanel } from './components/EventPanel'
import { MetricsPanel } from './components/MetricsPanel'
import { PlanPanel } from './components/PlanPanel'
import { SessionList } from './components/SessionList'
import { SourcesPanel } from './components/SourcesPanel'
import { TopBar } from './components/TopBar'
import { useWorkbench } from './state/useWorkbench'
import './styles.css'

export default function App() {
  const workbench = useWorkbench()
  const error = workbench.error ?? workbench.run.error
  const currentPlan = workbench.run.plan || workbench.session?.plan || ''

  return (
    <main className="app-shell">
      <TopBar
        title={workbench.session?.title ?? '新研究'}
        status={workbench.run.status}
        onCancel={workbench.cancel}
      />

      {error && (
        <ErrorBanner
          message={error}
          onRetry={workbench.reconnect}
          onDismiss={workbench.dismissError}
        />
      )}

      <div className="workbench">
        <aside className="left-column" aria-label="会话与计划">
          <SessionList
            sessions={workbench.sessions}
            selectedId={workbench.session?.id ?? null}
            loading={workbench.loadingSessions}
            disabled={false}
            onSelect={workbench.selectSession}
            onNew={async () => {
              await workbench.createSession()
            }}
          />
          <PlanPanel plan={currentPlan} />
        </aside>

        <div className="center-column" aria-busy={workbench.loadingSession}>
          {workbench.loadingSession && (
            <div className="session-loading" role="status">
              <span className="spinner" aria-hidden="true" />
              正在打开会话…
            </div>
          )}
          <ChatPanel
            messages={workbench.session?.messages ?? []}
            streamingText={workbench.run.streamingText}
            status={workbench.run.status}
            onSubmit={workbench.start}
          />
        </div>

        <aside className="right-column" aria-label="活动与来源">
          <EventPanel tools={workbench.run.tools} status={workbench.run.status} />
          <SourcesPanel sources={workbench.session?.sources ?? []} />
          <MetricsPanel usage={workbench.session?.usage ?? {}} />
        </aside>
      </div>

      {workbench.run.approval && (
        <ApprovalModal approval={workbench.run.approval} onDecision={workbench.decide} />
      )}
    </main>
  )
}
