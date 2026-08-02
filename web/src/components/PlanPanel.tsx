export function PlanPanel({
  plan,
  loading = false,
  failed = false,
}: {
  plan: string
  loading?: boolean
  failed?: boolean
}) {
  return (
    <section className="panel plan-panel" aria-labelledby="plan-title">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Strategy</span>
          <h2 id="plan-title">研究计划</h2>
        </div>
      </div>
      {loading ? (
        <div className="compact-state" role="status">
          <span className="spinner" aria-hidden="true" />
          正在载入计划…
        </div>
      ) : failed ? (
        <div className="compact-state empty">
          <strong>研究计划载入失败</strong>
          <span>重试会话后将恢复计划。</span>
        </div>
      ) : plan ? (
        <pre className="plan">{plan}</pre>
      ) : (
        <div className="compact-state empty">
          <strong>尚未制定计划</strong>
          <span>提出问题后，Scout 会在这里整理步骤。</span>
        </div>
      )}
    </section>
  )
}
