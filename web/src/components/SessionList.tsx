import type { SessionSummary } from '../api/types'

function formatUpdatedAt(timestamp: number): string {
  if (!timestamp) {
    return '尚未更新'
  }
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(timestamp * 1000))
}

export function SessionList({
  sessions,
  selectedId,
  loading,
  loaded,
  disabled,
  onSelect,
  onNew,
}: {
  sessions: SessionSummary[]
  selectedId: string | null
  loading: boolean
  loaded: boolean
  disabled: boolean
  onSelect: (id: string) => void | Promise<void>
  onNew: () => void | Promise<void>
}) {
  return (
    <section className="panel session-panel" aria-labelledby="sessions-title">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Workspace</span>
          <h2 id="sessions-title">研究会话</h2>
        </div>
        <button
          className="icon-button"
          onClick={() => void onNew()}
          disabled={disabled}
          aria-label="新建会话"
          title="新建会话"
        >
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="M10 4v12M4 10h12" />
          </svg>
        </button>
      </div>

      {loading ? (
        <div className="compact-state" role="status">
          <span className="spinner" aria-hidden="true" />
          正在载入会话…
        </div>
      ) : !loaded ? (
        <div className="compact-state empty">
          <strong>无法载入会话</strong>
          <span>请检查连接后重试。</span>
        </div>
      ) : sessions.length === 0 ? (
        <div className="compact-state empty">
          <strong>还没有研究会话</strong>
          <span>新问题会自动创建会话。</span>
        </div>
      ) : (
        <ul className="session-list">
          {sessions.map((session) => {
            const selected = session.id === selectedId
            return (
              <li key={session.id}>
                <button
                  className={selected ? 'selected' : ''}
                  aria-current={selected ? 'page' : undefined}
                  onClick={() => void onSelect(session.id)}
                  disabled={disabled}
                >
                  <span className="session-title">{session.title || '未命名调研'}</span>
                  <span className="session-meta">
                    <span>{session.message_count} 条消息</span>
                    <time dateTime={new Date(session.updated_at * 1000).toISOString()}>
                      {formatUpdatedAt(session.updated_at)}
                    </time>
                  </span>
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
