import { useMemo, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'
import ReactMarkdown from 'react-markdown'
import type { RunState } from '../state/runReducer'

type Message = { role: string; content: string }

const statusCopy: Record<RunState['status'], string> = {
  idle: '开始调研',
  running: '研究进行中',
  awaiting_approval: '等待确认',
  cancelling: '正在停止',
}

export function ChatPanel({
  messages,
  streamingText,
  status,
  onSubmit,
}: {
  messages: Message[]
  streamingText: string
  status: RunState['status']
  onSubmit: (question: string) => Promise<void>
}) {
  const [question, setQuestion] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const visibleMessages = useMemo(
    () => messages.filter((message) => message.role === 'user' || message.role === 'assistant'),
    [messages],
  )
  const lastMessage = visibleMessages[visibleMessages.length - 1]
  const showStream =
    Boolean(streamingText) &&
    !(lastMessage?.role === 'assistant' && lastMessage.content.trim() === streamingText.trim())
  const disabled = status !== 'idle' || submitting

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const value = question.trim()
    if (!value || disabled) {
      return
    }
    setSubmitting(true)
    try {
      await onSubmit(value)
      setQuestion('')
    } catch {
      // The controller owns the API error; retaining the draft makes retry safe.
    } finally {
      setSubmitting(false)
    }
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      event.currentTarget.form?.requestSubmit()
    }
  }

  const empty = visibleMessages.length === 0 && !showStream

  return (
    <section className="chat-column" aria-label="研究对话">
      <header className="chat-heading">
        <div>
          <span className="eyebrow">Research thread</span>
          <h1>研究对话</h1>
        </div>
        <span className={`run-indicator ${status}`}>
          <span aria-hidden="true" />
          {status === 'idle' ? '就绪' : statusCopy[status]}
        </span>
      </header>

      <div className="messages" aria-live="polite">
        {empty && (
          <div className="empty-state chat-empty">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H11l-4.8 4v-4A2.5 2.5 0 0 1 4 13.5Z" />
              <path d="M8 8h8M8 11.5h5" />
            </svg>
            <strong>从一个研究问题开始</strong>
            <p>描述目标、范围和期望的输出，Scout 会规划步骤并整理可追溯的来源。</p>
          </div>
        )}

        {visibleMessages.map((message, index) => (
          <article
            className={`message ${message.role}`}
            key={`${message.role}-${index}-${message.content.slice(0, 24)}`}
          >
            <div className="message-meta">
              <span>{message.role === 'user' ? '你' : 'Scout'}</span>
            </div>
            <div className="markdown">
              <ReactMarkdown>{message.content}</ReactMarkdown>
            </div>
          </article>
        ))}

        {showStream && (
          <article className="message assistant streaming">
            <div className="message-meta">
              <span>Scout</span>
              <span className="stream-label"><i />正在生成</span>
            </div>
            <div className="markdown">
              <ReactMarkdown>{streamingText}</ReactMarkdown>
            </div>
          </article>
        )}
      </div>

      <form className="composer" onSubmit={submit}>
        <label className="sr-only" htmlFor="research-question">研究问题</label>
        <textarea
          id="research-question"
          placeholder="输入研究问题，尽量说明范围与交付形式…"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          rows={3}
        />
        <div className="composer-footer">
          <span><kbd>Enter</kbd> 提交 · <kbd>Shift</kbd> + <kbd>Enter</kbd> 换行</span>
          <button className="button primary submit-button" disabled={disabled || !question.trim()}>
            <span>{submitting ? '正在提交' : statusCopy[status]}</span>
            <svg viewBox="0 0 20 20" aria-hidden="true">
              <path d="m4 10 11-6-3.2 12-2.1-4.1L4 10Z" />
            </svg>
          </button>
        </div>
      </form>
    </section>
  )
}
