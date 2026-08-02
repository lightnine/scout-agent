function metric(value: number | undefined): string {
  return new Intl.NumberFormat('zh-CN').format(value ?? 0)
}

export function MetricsPanel({ usage }: { usage: Record<string, number> }) {
  const prompt = usage.prompt_tokens ?? 0
  const completion = usage.completion_tokens ?? 0
  return (
    <section className="panel metrics-panel" aria-labelledby="metrics-title">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Session metrics</span>
          <h2 id="metrics-title">会话用量</h2>
        </div>
      </div>
      <dl className="metrics-grid">
        <div>
          <dt>模型调用</dt>
          <dd>{metric(usage.calls)}</dd>
        </div>
        <div>
          <dt>总 Tokens</dt>
          <dd>{metric(prompt + completion)}</dd>
        </div>
        <div>
          <dt>输入 / 输出</dt>
          <dd>{metric(prompt)} <small>/ {metric(completion)}</small></dd>
        </div>
        <div>
          <dt>缓存命中</dt>
          <dd>{metric(usage.cached_tokens)}</dd>
        </div>
      </dl>
    </section>
  )
}
