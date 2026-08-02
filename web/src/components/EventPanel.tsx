import type { RunState } from '../state/runReducer'

const runLabels: Record<RunState['status'], string> = {
  idle: '空闲',
  running: '运行中',
  awaiting_approval: '等待确认',
  cancelling: '停止中',
}

function toolState(tool: Record<string, unknown>) {
  if (tool.status === 'running') {
    return { className: 'running', label: '运行中' }
  }
  if (tool.ok === false) {
    return { className: 'failed', label: '失败' }
  }
  return { className: 'success', label: '完成' }
}

export function EventPanel({
  tools,
  status,
}: {
  tools: Array<Record<string, unknown>>
  status: RunState['status']
}) {
  return (
    <section className="panel event-panel" aria-labelledby="activity-title">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Live trace</span>
          <h2 id="activity-title">实时活动</h2>
        </div>
        <span className={`panel-status ${status}`}>{runLabels[status]}</span>
      </div>

      {tools.length === 0 ? (
        <div className="compact-state empty">
          <strong>暂无活动</strong>
          <span>工具调用与工作节点会显示在这里。</span>
        </div>
      ) : (
        <ol className="event-list">
          {tools.map((tool, index) => {
            const state = toolState(tool)
            return (
              <li key={`${String(tool.id ?? tool.tool ?? 'tool')}-${index}`}>
                <span className={`event-dot ${state.className}`} aria-hidden="true" />
                <div className="event-content">
                  <strong>{String(tool.tool ?? '未知工具')}</strong>
                  <span>{String(tool.worker ?? 'main')}</span>
                </div>
                <span className={`event-state ${state.className}`}>{state.label}</span>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
