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
})
