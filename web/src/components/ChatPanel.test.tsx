import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ChatPanel } from './ChatPanel'

afterEach(cleanup)

describe('ChatPanel', () => {
  it('submits with Enter and preserves the draft when submission fails', async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error('network down'))
    render(
      <ChatPanel
        messages={[]}
        streamingText=""
        status="idle"
        onSubmit={onSubmit}
      />,
    )
    const input = screen.getByLabelText('研究问题')

    fireEvent.change(input, { target: { value: '比较两种向量数据库' } })
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: false })

    await waitFor(() => expect(onSubmit).toHaveBeenCalledWith('比较两种向量数据库'))
    expect(input).toHaveValue('比较两种向量数据库')
  })

  it('uses Shift+Enter for a newline without submitting', () => {
    const onSubmit = vi.fn()
    render(
      <ChatPanel
        messages={[]}
        streamingText=""
        status="idle"
        onSubmit={onSubmit}
      />,
    )
    const input = screen.getByLabelText('研究问题')

    fireEvent.change(input, { target: { value: '第一行\n第二行' } })
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })

    expect(onSubmit).not.toHaveBeenCalled()
    expect(input).toHaveValue('第一行\n第二行')
  })

  it('does not submit Enter while a Chinese IME composition is active', () => {
    const onSubmit = vi.fn()
    render(
      <ChatPanel
        messages={[]}
        streamingText=""
        status="idle"
        onSubmit={onSubmit}
      />,
    )
    const input = screen.getByLabelText('研究问题')

    fireEvent.change(input, { target: { value: '向量数' } })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })

    expect(onSubmit).not.toHaveBeenCalled()
    expect(input).toHaveValue('向量数')
  })

  it('does not duplicate a finalized streaming answer', () => {
    render(
      <ChatPanel
        messages={[{ role: 'assistant', content: '最终答案' }]}
        streamingText="最终答案"
        status="idle"
        onSubmit={vi.fn()}
      />,
    )

    expect(screen.getAllByText('最终答案')).toHaveLength(1)
  })

  it('shows the empty conversation state', () => {
    render(
      <ChatPanel
        messages={[]}
        streamingText=""
        status="idle"
        onSubmit={vi.fn()}
      />,
    )

    expect(screen.getByText('从一个研究问题开始')).toBeInTheDocument()
  })

  it('distinguishes detail loading from an empty or stale conversation', () => {
    render(
      <ChatPanel
        messages={[{ role: 'assistant', content: '旧会话回答' }]}
        streamingText="旧流式内容"
        status="idle"
        loading
        onSubmit={vi.fn()}
      />,
    )

    expect(screen.getByText('正在载入对话…')).toBeInTheDocument()
    expect(screen.queryByText('从一个研究问题开始')).toBeNull()
    expect(screen.queryByText('旧会话回答')).toBeNull()
    expect(screen.queryByText('旧流式内容')).toBeNull()
    expect(screen.getByLabelText('研究问题')).toBeDisabled()
  })

  it('keeps a selected-session load failure distinct from onboarding', () => {
    const onRetry = vi.fn()
    render(
      <ChatPanel
        messages={[]}
        streamingText=""
        status="idle"
        failed
        onRetry={onRetry}
        onSubmit={vi.fn()}
      />,
    )

    expect(screen.getByText('无法载入所选会话')).toBeInTheDocument()
    expect(screen.queryByText('从一个研究问题开始')).toBeNull()
    expect(screen.getByLabelText('研究问题')).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '重试载入会话' }))
    expect(onRetry).toHaveBeenCalledOnce()
  })
})
