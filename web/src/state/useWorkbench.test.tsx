import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SessionDetail } from '../api/types'

const apiMock = vi.hoisted(() => ({
  sessions: vi.fn(),
  session: vi.fn(),
  createSession: vi.fn(),
  startRun: vi.fn(),
  approve: vi.fn(),
  cancel: vi.fn(),
}))
const subscribeMock = vi.hoisted(() => vi.fn())

vi.mock('../api/client', () => ({ api: apiMock }))
vi.mock('../api/sse', () => ({ subscribeToRun: subscribeMock }))

import { useWorkbench } from './useWorkbench'

function session(id: string): SessionDetail {
  return {
    id,
    title: id,
    messages: [],
    plan: '',
    plan_steps: [],
    plan_current: 0,
    sources: [],
    usage: {},
    active_run_id: null,
    run_status: 'idle',
  }
}

describe('useWorkbench', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMock.sessions.mockResolvedValue([])
    subscribeMock.mockReturnValue(vi.fn())
  })

  it('keeps the latest selected session when an earlier selection resolves late', async () => {
    let resolveFirst: (detail: SessionDetail) => void = () => undefined
    apiMock.session
      .mockImplementationOnce(() => new Promise<SessionDetail>((resolve) => {
        resolveFirst = resolve
      }))
      .mockResolvedValueOnce(session('s2'))

    const { result } = renderHook(() => useWorkbench())

    act(() => {
      void result.current.selectSession('s1')
      void result.current.selectSession('s2')
    })
    await waitFor(() => expect(result.current.session?.id).toBe('s2'))

    await act(async () => {
      resolveFirst(session('s1'))
    })
    expect(result.current.session?.id).toBe('s2')
  })

  it('transitions to cancelling before the cancel request resolves', async () => {
    apiMock.session.mockResolvedValue(session('s1'))
    apiMock.startRun.mockResolvedValue({ run_id: 'r1', session_id: 's1' })
    apiMock.cancel.mockReturnValue(new Promise(() => undefined))

    const { result } = renderHook(() => useWorkbench())
    await act(async () => {
      await result.current.selectSession('s1')
      await result.current.start('question')
    })
    await waitFor(() => expect(result.current.run.status).toBe('running'))

    act(() => {
      void result.current.cancel()
    })

    expect(result.current.run.status).toBe('cancelling')
  })

  it('reconnects the active run without changing the selected session', async () => {
    apiMock.session.mockResolvedValue(session('s1'))
    apiMock.startRun.mockResolvedValue({ run_id: 'r1', session_id: 's1' })
    const close = vi.fn()
    subscribeMock.mockReturnValue(close)
    const { result } = renderHook(() => useWorkbench())

    await act(async () => {
      await result.current.selectSession('s1')
      await result.current.start('question')
    })
    await waitFor(() => expect(subscribeMock).toHaveBeenCalledTimes(1))

    act(() => {
      result.current.reconnect()
    })

    await waitFor(() => expect(subscribeMock).toHaveBeenCalledTimes(2))
    expect(close).toHaveBeenCalled()
    expect(result.current.session?.id).toBe('s1')
  })
})
