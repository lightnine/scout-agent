type Source = { label: string; title: string; url: string; fetched_at: number }

function safeExternalUrl(value: string): string | null {
  try {
    const url = new URL(value)
    return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null
  } catch {
    return null
  }
}

function sourceHost(value: string): string {
  try {
    return new URL(value).hostname.replace(/^www\./, '')
  } catch {
    return ''
  }
}

export function SourcesPanel({
  sources,
  loading = false,
  failed = false,
}: {
  sources: Source[]
  loading?: boolean
  failed?: boolean
}) {
  const safeSources = sources.flatMap((source) => {
    const url = safeExternalUrl(source.url)
    return url ? [{ ...source, url }] : []
  })

  return (
    <section className="panel sources-panel" aria-labelledby="sources-title">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Evidence</span>
          <h2 id="sources-title">研究来源</h2>
        </div>
        <span className="count-badge">{loading || failed ? '—' : safeSources.length}</span>
      </div>

      {loading ? (
        <div className="compact-state" role="status">
          <span className="spinner" aria-hidden="true" />
          正在载入来源…
        </div>
      ) : failed ? (
        <div className="compact-state empty">
          <strong>研究来源载入失败</strong>
          <span>重试会话后将恢复来源。</span>
        </div>
      ) : sources.length === 0 ? (
        <div className="compact-state empty">
          <strong>暂无来源</strong>
          <span>引用的网页和文档会汇总在这里。</span>
        </div>
      ) : safeSources.length === 0 ? (
        <div className="compact-state empty">
          <strong>来源不可用</strong>
          <span>没有可安全打开的来源。</span>
        </div>
      ) : (
        <ul className="source-list">
          {safeSources.map((source, index) => (
            <li key={`${source.label}-${source.url}-${index}`}>
              <a href={source.url} target="_blank" rel="noopener noreferrer">
                <span className="source-label">[{source.label || index + 1}]</span>
                <span className="source-copy">
                  <strong>{source.title || source.url}</strong>
                  <span>{sourceHost(source.url)}</span>
                </span>
                <svg viewBox="0 0 20 20" aria-hidden="true">
                  <path d="M8 5h-3v10h10v-3M10 5h5v5M15 5l-7 7" />
                </svg>
              </a>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
