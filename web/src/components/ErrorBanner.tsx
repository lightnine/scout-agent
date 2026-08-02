export function ErrorBanner({
  message,
  onRetry,
  onDismiss,
}: {
  message: string
  onRetry?: () => void | Promise<void>
  onDismiss: () => void
}) {
  return (
    <div className="error-banner" role="alert">
      <svg viewBox="0 0 20 20" aria-hidden="true">
        <path d="M10 2.7 18 17H2L10 2.7Z" />
        <path d="M10 7v4.5M10 14.5v.1" />
      </svg>
      <div>
        <strong>Scout 暂时无法完成操作</strong>
        <span>{message}</span>
      </div>
      <div className="error-actions">
        {onRetry && <button onClick={() => void onRetry()}>重试</button>}
        <button className="icon-button" onClick={onDismiss} aria-label="关闭错误提示">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="m5 5 10 10M15 5 5 15" />
          </svg>
        </button>
      </div>
    </div>
  )
}
