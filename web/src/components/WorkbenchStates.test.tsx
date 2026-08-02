import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ErrorBanner } from './ErrorBanner'
import { EventPanel } from './EventPanel'
import { MetricsPanel } from './MetricsPanel'
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
        loaded={false}
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
        loaded
        disabled={false}
        onSelect={vi.fn()}
        onNew={vi.fn()}
      />,
    )
    expect(screen.getByText('还没有研究会话')).toBeInTheDocument()
  })

  it('does not describe a failed initial session load as empty', () => {
    render(
      <SessionList
        sessions={[]}
        selectedId={null}
        loading={false}
        loaded={false}
        disabled={false}
        onSelect={vi.fn()}
        onNew={vi.fn()}
      />,
    )

    expect(screen.getByText('无法载入会话')).toBeInTheDocument()
    expect(screen.queryByText('还没有研究会话')).toBeNull()
  })

  it('disables new and select session controls during an active run', () => {
    render(
      <SessionList
        sessions={[{
          id: 's1',
          title: '运行中的会话',
          message_count: 2,
          created_at: 1,
          updated_at: 1,
        }]}
        selectedId="s1"
        loading={false}
        loaded
        disabled
        onSelect={vi.fn()}
        onNew={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: '新建会话' })).toBeDisabled()
    expect(screen.getByRole('button', { name: /运行中的会话/ })).toBeDisabled()
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

  it('shows detail loading without stale plan, sources, or metrics', () => {
    render(
      <>
        <PlanPanel plan="旧计划" loading />
        <SourcesPanel
          sources={[{ label: 'A', title: '旧来源', url: 'https://example.com', fetched_at: 0 }]}
          loading
        />
        <MetricsPanel usage={{ calls: 9 }} loading />
      </>,
    )

    expect(screen.getByText('正在载入计划…')).toBeInTheDocument()
    expect(screen.getByText('正在载入来源…')).toBeInTheDocument()
    expect(screen.getByText('正在载入用量…')).toBeInTheDocument()
    expect(screen.queryByText('旧计划')).toBeNull()
    expect(screen.queryByText('旧来源')).toBeNull()
    expect(screen.queryByText('9')).toBeNull()
  })

  it('shows selected-session failure instead of empty detail states', () => {
    render(
      <>
        <PlanPanel plan="" failed />
        <SourcesPanel sources={[]} failed />
        <MetricsPanel usage={null} failed />
      </>,
    )

    expect(screen.getByText('研究计划载入失败')).toBeInTheDocument()
    expect(screen.getByText('研究来源载入失败')).toBeInTheDocument()
    expect(screen.getByText('会话用量载入失败')).toBeInTheDocument()
    expect(screen.queryByText('尚未制定计划')).toBeNull()
    expect(screen.queryByText('暂无来源')).toBeNull()
    expect(screen.queryByText('选择会话后查看用量。')).toBeNull()
  })

  it('distinguishes no selected session from a loaded zero-usage session', () => {
    const { rerender } = render(<MetricsPanel usage={null} loading={false} />)
    expect(screen.getByText('选择会话后查看用量。')).toBeInTheDocument()

    rerender(<MetricsPanel usage={{}} loading={false} />)
    expect(screen.queryByText('选择会话后查看用量。')).toBeNull()
    expect(screen.getByText('模型调用')).toBeInTheDocument()
    expect(screen.getAllByText('0', { exact: true })).toHaveLength(4)
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
