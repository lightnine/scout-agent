import type { RunState } from '../state/runReducer'

const statusCopy: Record<RunState['status'], string> = {
  idle: '已就绪',
  running: '调研中',
  awaiting_approval: '等待确认',
  cancelling: '正在停止',
}

export function TopBar({
  title,
  status,
  onCancel,
}: {
  title: string
  status: RunState['status']
  onCancel: () => void | Promise<void>
}) {
  const active = status !== 'idle'
  return (
    <header className="top-bar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M4 18.5V7.2L12 3l8 4.2v9.6L12 21l-5-2.6" />
            <path d="m8.2 12 2.3 2.3 5.4-5.5" />
          </svg>
        </span>
        <div>
          <strong>Scout</strong>
          <span>本地 AI 研究工作台</span>
        </div>
      </div>

      <div className="top-context">
        <span>当前会话</span>
        <strong title={title}>{title || '未命名调研'}</strong>
      </div>

      <div className="top-actions">
        <span className={`status-pill ${status}`}>
          <i aria-hidden="true" />
          {statusCopy[status]}
        </span>
        {active && (
          <button
            className="stop-button"
            disabled={status === 'cancelling'}
            onClick={() => void onCancel()}
          >
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <rect x="6" y="6" width="8" height="8" rx="1" />
            </svg>
            {status === 'cancelling' ? '停止中' : '停止运行'}
          </button>
        )}
      </div>
    </header>
  )
}
