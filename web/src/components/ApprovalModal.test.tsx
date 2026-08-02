import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ApprovalModal } from './ApprovalModal'

afterEach(cleanup)

const planApproval = {
  approval_id: 'a1',
  kind: 'plan',
  title: '确认研究计划',
  payload: { plan: '1. 搜索资料\n2. 交叉验证' },
}

it('requires feedback before submitting a plan revision', () => {
  const onDecision = vi.fn()
  render(<ApprovalModal approval={planApproval} onDecision={onDecision} />)

  const revise = screen.getByRole('button', { name: '要求修改' })
  expect(revise).toBeDisabled()

  fireEvent.change(screen.getByLabelText('修改意见'), {
    target: { value: '请补充官方来源' },
  })
  fireEvent.click(revise)

  expect(onDecision).toHaveBeenCalledWith('revise', '请补充官方来源')
})

it('shows tool-specific actions and safely renders arguments', () => {
  render(
    <ApprovalModal
      approval={{
        approval_id: 'a2',
        kind: 'tool',
        title: '允许执行工具',
        payload: {
          tool: 'write_file',
          arguments: { path: '<script>alert(1)</script>' },
        },
      }}
      onDecision={vi.fn()}
    />,
  )

  expect(screen.getByText('write_file')).toBeInTheDocument()
  expect(screen.getByText(/<script>alert\(1\)<\/script>/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '本会话允许' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '拒绝' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '要求修改' })).toBeNull()
})

it('prevents duplicate decisions while submission is pending', async () => {
  const onDecision = vi.fn(() => new Promise<void>(() => undefined))
  render(<ApprovalModal approval={planApproval} onDecision={onDecision} />)

  const approve = screen.getByRole('button', { name: '批准计划' })
  fireEvent.click(approve)
  fireEvent.click(approve)

  expect(onDecision).toHaveBeenCalledTimes(1)
  await waitFor(() => expect(approve).toBeDisabled())
})

it('enables decisions when a new approval replaces a submitted approval', async () => {
  const onDecision = vi.fn().mockResolvedValue(undefined)
  const { rerender } = render(
    <ApprovalModal approval={planApproval} onDecision={onDecision} />,
  )

  fireEvent.click(screen.getByRole('button', { name: '批准计划' }))
  await waitFor(() => expect(onDecision).toHaveBeenCalledOnce())

  rerender(
    <ApprovalModal
      approval={{
        approval_id: 'a2',
        kind: 'tool',
        title: '执行工具 http_request',
        payload: { tool: 'http_request', arguments: { url: 'http://127.0.0.1' } },
      }}
      onDecision={onDecision}
    />,
  )

  expect(screen.getByRole('button', { name: '本会话允许' })).toBeEnabled()
})
