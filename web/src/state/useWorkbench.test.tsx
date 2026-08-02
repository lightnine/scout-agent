import { act, renderHook, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SSEEnvelope, SessionDetail, SessionSummary } from '../api/types'

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

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}

function summary(id: string): SessionSummary {
  return { id, title: id, message_count: 0, created_at: 0, updated_at: 0 }
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

  it('reports initial session loading until the list request settles', async () => {
    const sessions = deferred<SessionSummary[]>()
    apiMock.sessions.mockReturnValue(sessions.promise)
    const { result } = renderHook(() => useWorkbench())

    expect(result.current.loadingSessions).toBe(true)

    await act(async () => {
      sessions.resolve([])
    })
    await waitFor(() => expect(result.current.loadingSessions).toBe(false))
  })

  it('dismisses controller errors and retries the session list', async () => {
    apiMock.sessions.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([])
    const { result } = renderHook(() => useWorkbench())

    await waitFor(() => expect(result.current.error).toBe('offline'))
    act(() => result.current.dismissError())
    expect(result.current.error).toBeNull()

    act(() => result.current.reconnect())
    await waitFor(() => expect(apiMock.sessions).toHaveBeenCalledTimes(2))
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

  it('keeps a later selection while a pending create starts and completes its own run', async () => {
    const created = deferred<SessionSummary>()
    const createdDetail = deferred<SessionDetail>()
    const listeners = new Map<string, (event: SSEEnvelope) => void>()
    apiMock.createSession.mockReturnValue(created.promise)
    apiMock.session.mockImplementation((id: string) =>
      id === 'created' ? createdDetail.promise : Promise.resolve(session(id)),
    )
    apiMock.startRun.mockResolvedValue({ run_id: 'r-created', session_id: 'created' })
    subscribeMock.mockImplementation(
      (runId: string, onEvent: (event: SSEEnvelope) => void) => {
        listeners.set(runId, onEvent)
        return vi.fn()
      },
    )
    const { result } = renderHook(() => useWorkbench())
    let starting: Promise<void> = Promise.resolve()

    act(() => {
      starting = result.current.start('question')
    })
    await waitFor(() => expect(apiMock.createSession).toHaveBeenCalledOnce())

    await act(async () => {
      await result.current.selectSession('existing')
    })
    expect(result.current.session?.id).toBe('existing')

    await act(async () => {
      created.resolve(summary('created'))
    })
    await waitFor(() => expect(apiMock.session).toHaveBeenCalledWith('created'))
    await act(async () => {
      createdDetail.resolve(session('created'))
      await starting
    })

    expect(apiMock.startRun).toHaveBeenCalledWith('created', 'question')
    expect(result.current.session?.id).toBe('existing')
    await waitFor(() => expect(listeners.get('r-created')).toBeDefined())

    act(() => {
      listeners.get('r-created')?.({
        id: 1,
        type: 'run_end',
        run_id: 'r-created',
        session_id: 'created',
        ts: 0,
        agent: 'main',
        data: {},
      })
    })

    expect(apiMock.session.mock.calls.filter(([id]) => id === 'created')).toHaveLength(1)
    expect(result.current.session?.id).toBe('existing')
  })

  it('does not invalidate a pending selection when an unrelated run completes', async () => {
    const target = deferred<SessionDetail>()
    const listeners = new Map<string, (event: SSEEnvelope) => void>()
    apiMock.session.mockImplementation((id: string) =>
      id === 'target-a' ? target.promise : Promise.resolve(session(id)),
    )
    apiMock.startRun.mockResolvedValue({ run_id: 'r-origin-b', session_id: 'origin-b' })
    subscribeMock.mockImplementation(
      (runId: string, onEvent: (event: SSEEnvelope) => void) => {
        listeners.set(runId, onEvent)
        return vi.fn()
      },
    )
    const { result } = renderHook(() => useWorkbench())

    await act(async () => {
      await result.current.selectSession('origin-b')
      await result.current.start('question')
    })
    await waitFor(() => expect(listeners.get('r-origin-b')).toBeDefined())

    act(() => {
      void result.current.selectSession('target-a')
    })
    await waitFor(() => expect(apiMock.session).toHaveBeenCalledWith('target-a'))

    act(() => {
      listeners.get('r-origin-b')?.({
        id: 1,
        type: 'run_end',
        run_id: 'r-origin-b',
        session_id: 'origin-b',
        ts: 0,
        agent: 'main',
        data: {},
      })
    })

    await act(async () => {
      target.resolve(session('target-a'))
    })

    await waitFor(() => expect(result.current.session?.id).toBe('target-a'))
  })
})
