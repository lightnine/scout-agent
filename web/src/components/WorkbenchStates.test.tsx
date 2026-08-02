import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ErrorBanner } from './ErrorBanner'
import { EventPanel } from './EventPanel'
import { PlanPanel } from './PlanPanel'
import { SessionList } from './SessionList'
import { SourcesPanel } from './SourcesPanel'

afterEach(cleanup)

describe('workbench empty and error states', () => {
  it('shows session loading and empty states', () => {
    const { rerender } = render(
      <SessionList
        sessions={[]}
        selectedId={null}
        loading
        disabled={false}
        onSelect={vi.fn()}
        onNew={vi.fn()}
      />,
    )
    expect(screen.getByText('正在载入会话…')).toBeInTheDocument()

    rerender(
      <SessionList
        sessions={[]}
        selectedId={null}
        loading={false}
        disabled={false}
        onSelect={vi.fn()}
        onNew={vi.fn()}
      />,
    )
    expect(screen.getByText('还没有研究会话')).toBeInTheDocument()
  })

  it('shows empty plan, activity, and source guidance', () => {
    render(
      <>
        <PlanPanel plan="" />
        <EventPanel tools={[]} status="idle" />
        <SourcesPanel sources={[]} />
      </>,
    )

    expect(screen.getByText('提出问题后，Scout 会在这里整理步骤。')).toBeInTheDocument()
    expect(screen.getByText('工具调用与工作节点会显示在这里。')).toBeInTheDocument()
    expect(screen.getByText('引用的网页和文档会汇总在这里。')).toBeInTheDocument()
  })

  it('offers retry and dismiss actions for readable errors', () => {
    const onRetry = vi.fn()
    const onDismiss = vi.fn()
    render(
      <ErrorBanner message="无法连接到本地 Scout 服务" onRetry={onRetry} onDismiss={onDismiss} />,
    )

    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    fireEvent.click(screen.getByRole('button', { name: '关闭错误提示' }))

    expect(onRetry).toHaveBeenCalledOnce()
    expect(onDismiss).toHaveBeenCalledOnce()
  })

  it('omits unsafe source URLs', () => {
    render(
      <SourcesPanel
        sources={[
          { label: 'S1', title: 'Unsafe', url: 'javascript:alert(1)', fetched_at: 0 },
        ]}
      />,
    )

    expect(screen.queryByRole('link', { name: /Unsafe/ })).toBeNull()
    expect(screen.getByText('没有可安全打开的来源。')).toBeInTheDocument()
  })
})
