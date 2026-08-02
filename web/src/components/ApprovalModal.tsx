import { useEffect, useId, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import type { ApprovalAction } from '../api/types'
import type { ApprovalView } from '../state/runReducer'

function displayValue(value: unknown): string {
  if (typeof value === 'string') {
    return value
  }
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value ?? '')
  }
}

export function ApprovalModal({
  approval,
  onDecision,
}: {
  approval: ApprovalView
  onDecision: (action: ApprovalAction, feedback?: string) => void | Promise<void>
}) {
  const [feedback, setFeedback] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState('')
  const dialogRef = useRef<HTMLElement>(null)
  const initialFocusRef = useRef<HTMLButtonElement>(null)
  const titleId = useId()
  const descriptionId = useId()
  const isPlan = approval.kind === 'plan'

  useEffect(() => {
    setFeedback('')
    setSubmitting(false)
    setSubmitError('')
    const previousFocus = document.activeElement
    initialFocusRef.current?.focus()
    return () => {
      if (previousFocus instanceof HTMLElement) {
        previousFocus.focus()
      }
    }
  }, [approval.approval_id])

  const decide = async (action: ApprovalAction, decisionFeedback = '') => {
    if (submitting || (action === 'revise' && !decisionFeedback.trim())) {
      return
    }
    setSubmitting(true)
    setSubmitError('')
    try {
      await onDecision(action, decisionFeedback.trim() || undefined)
    } catch (cause) {
      setSubmitError(cause instanceof Error ? cause.message : '提交审批失败，请重试。')
      setSubmitting(false)
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === 'Escape' && !submitting) {
      event.preventDefault()
      void decide('cancel')
      return
    }
    if (event.key !== 'Tab') {
      return
    }
    const focusable = dialogRef.current?.querySelectorAll<HTMLElement>(
      'button:not(:disabled), textarea:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
    )
    if (!focusable?.length) {
      return
    }
    const first = focusable[0]
    const last = focusable[focusable.length - 1]
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault()
      first.focus()
    }
  }

  const detail = isPlan
    ? displayValue(approval.payload.plan)
    : displayValue(approval.payload.arguments ?? {})

  return (
    <div className="modal-backdrop" role="presentation">
      <section
        ref={dialogRef}
        className="approval-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        onKeyDown={handleKeyDown}
      >
        <div className="modal-heading">
          <span className="eyebrow">{isPlan ? '计划确认' : '权限请求'}</span>
          <h2 id={titleId}>{approval.title}</h2>
          <p id={descriptionId}>
            {isPlan
              ? '请确认 Scout 的研究路径。批准后将开始执行。'
              : '此工具操作需要你的明确授权，请检查名称和参数。'}
          </p>
        </div>

        {!isPlan && (
          <div className="approval-tool">
            <span>工具</span>
            <strong>{displayValue(approval.payload.tool) || '未知工具'}</strong>
          </div>
        )}
        <div className="approval-payload">
          <span>{isPlan ? '研究计划' : '调用参数'}</span>
          <pre>{detail || '未提供内容'}</pre>
        </div>

        {isPlan && (
          <label className="feedback-field">
            <span>修改意见</span>
            <textarea
              aria-label="修改意见"
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              placeholder="说明需要补充、删减或验证的内容"
              disabled={submitting}
            />
            <small>要求修改时必须填写具体意见。</small>
          </label>
        )}

        {submitError && <p className="modal-error" role="alert">{submitError}</p>}

        <div className="actions">
          <button
            ref={initialFocusRef}
            className="button primary"
            disabled={submitting}
            onClick={() => void decide('approve')}
          >
            {isPlan ? '批准计划' : '允许一次'}
          </button>
          {isPlan ? (
            <button
              className="button secondary"
              disabled={submitting || !feedback.trim()}
              onClick={() => void decide('revise', feedback)}
            >
              要求修改
            </button>
          ) : (
            <>
              <button
                className="button secondary"
                disabled={submitting}
                onClick={() => void decide('allow_session')}
              >
                本会话允许
              </button>
              <button
                className="button secondary"
                disabled={submitting}
                onClick={() => void decide('reject')}
              >
                拒绝
              </button>
            </>
          )}
          <button
            className="button danger-quiet"
            disabled={submitting}
            onClick={() => void decide('cancel')}
          >
            取消运行
          </button>
        </div>
      </section>
    </div>
  )
}
