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
    expect(result.current.sessionsLoaded).toBe(false)
    act(() => result.current.dismissError())
    expect(result.current.error).toBeNull()

    act(() => result.current.reconnect())
    await waitFor(() => expect(apiMock.sessions).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(result.current.sessionsLoaded).toBe(true))
  })

  it('cannot submit against a stale session while a new selection is pending', async () => {
    const pendingB = deferred<SessionDetail>()
    apiMock.session.mockImplementation((id: string) =>
      id === 'b' ? pendingB.promise : Promise.resolve(session(id)),
    )
    apiMock.startRun.mockResolvedValue({ run_id: 'r1', session_id: 'a' })
    const { result } = renderHook(() => useWorkbench())

    await act(async () => {
      await result.current.selectSession('a')
    })
    act(() => {
      void result.current.selectSession('b')
    })

    expect(result.current.loadingSession).toBe(true)
    expect(result.current.session).toBeNull()
    await act(async () => {
      await expect(result.current.start('must target b')).rejects.toThrow('会话仍在加载')
    })
    expect(apiMock.startRun).not.toHaveBeenCalled()
  })

  it('preserves detail failure through dismiss and clears it after retry succeeds', async () => {
    const retry = deferred<SessionDetail>()
    apiMock.session
      .mockRejectedValueOnce(new Error('detail offline'))
      .mockReturnValueOnce(retry.promise)
    const { result } = renderHook(() => useWorkbench())

    await act(async () => {
      await expect(result.current.selectSession('a')).rejects.toThrow('detail offline')
    })
    expect(result.current.session).toBeNull()
    expect(result.current.sessionLoadFailed).toBe(true)
    expect(result.current.error).toBe('detail offline')

    act(() => result.current.dismissError())
    expect(result.current.error).toBeNull()
    expect(result.current.sessionLoadFailed).toBe(true)

    act(() => result.current.reconnect())
    expect(result.current.loadingSession).toBe(true)
    expect(result.current.session).toBeNull()
    await act(async () => {
      await expect(result.current.start('must wait')).rejects.toThrow('会话仍在加载')
    })
    expect(apiMock.startRun).not.toHaveBeenCalled()

    await act(async () => {
      retry.resolve(session('a'))
    })
    await waitFor(() => expect(result.current.loadingSession).toBe(false))
    expect(result.current.session?.id).toBe('a')
    expect(result.current.sessionLoadFailed).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('does not let a late detail retry overwrite a newer selection', async () => {
    const retryA = deferred<SessionDetail>()
    let aRequests = 0
    apiMock.session.mockImplementation((id: string) => {
      if (id === 'a') {
        aRequests += 1
        return aRequests === 1 ? Promise.reject(new Error('detail offline')) : retryA.promise
      }
      return Promise.resolve(session(id))
    })
    const { result } = renderHook(() => useWorkbench())

    await act(async () => {
      await expect(result.current.selectSession('a')).rejects.toThrow('detail offline')
    })
    act(() => result.current.reconnect())
    expect(result.current.loadingSession).toBe(true)

    await act(async () => {
      await result.current.selectSession('b')
    })
    expect(result.current.session?.id).toBe('b')

    await act(async () => {
      retryA.resolve(session('a'))
    })
    expect(result.current.session?.id).toBe('b')
    expect(result.current.selectedSessionId).toBe('b')
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

  it('recovers the active run when the cancel request fails', async () => {
    apiMock.session.mockResolvedValue(session('s1'))
    apiMock.startRun.mockResolvedValue({ run_id: 'r1', session_id: 's1' })
    apiMock.cancel.mockRejectedValue(new Error('cancel offline'))
    const { result } = renderHook(() => useWorkbench())

    await act(async () => {
      await result.current.selectSession('s1')
      await result.current.start('question')
    })
    await act(async () => {
      await expect(result.current.cancel()).rejects.toThrow('cancel offline')
    })

    expect(result.current.run.status).toBe('running')
    expect(result.current.run.error).toBe('cancel offline')
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

  it('reconnects from the highest cursor and ignores retained duplicate events', async () => {
    apiMock.session.mockResolvedValue(session('s1'))
    apiMock.startRun.mockResolvedValue({ run_id: 'r1', session_id: 's1' })
    const listeners: Array<(event: SSEEnvelope) => void> = []
    subscribeMock.mockImplementation(
      (
        _runId: string,
        onEvent: (event: SSEEnvelope) => void,
      ) => {
        listeners.push(onEvent)
        return vi.fn()
      },
    )
    const { result } = renderHook(() => useWorkbench())

    await act(async () => {
      await result.current.selectSession('s1')
      await result.current.start('question')
    })
    await waitFor(() => expect(listeners).toHaveLength(1))

    act(() => {
      listeners[0]({
        id: 5,
        type: 'llm_delta',
        run_id: 'r1',
        session_id: 's1',
        ts: 1,
        agent: 'main',
        data: { text: 'first' },
      })
      listeners[0]({
        id: 6,
        type: 'tool_start',
        run_id: 'r1',
        session_id: 's1',
        ts: 2,
        agent: 'main',
        data: { id: 'tool-1', tool: 'search' },
      })
    })
    act(() => result.current.reconnect())
    await waitFor(() => expect(listeners).toHaveLength(2))

    expect(subscribeMock.mock.calls[1][3]).toEqual({ afterId: 6 })
    act(() => {
      listeners[1]({
        id: 5,
        type: 'llm_delta',
        run_id: 'r1',
        session_id: 's1',
        ts: 1,
        agent: 'main',
        data: { text: 'first' },
      })
      listeners[1]({
        id: 6,
        type: 'tool_start',
        run_id: 'r1',
        session_id: 's1',
        ts: 2,
        agent: 'main',
        data: { id: 'tool-1', tool: 'search' },
      })
      listeners[1]({
        id: 7,
        type: 'llm_delta',
        run_id: 'r1',
        session_id: 's1',
        ts: 3,
        agent: 'main',
        data: { text: ' second' },
      })
    })

    expect(result.current.run.streamingText).toBe('first second')
    expect(result.current.run.tools).toHaveLength(1)
  })

  it('resets the replay cursor when the run identity changes', async () => {
    apiMock.session.mockResolvedValue(session('s1'))
    apiMock.startRun
      .mockResolvedValueOnce({ run_id: 'r1', session_id: 's1' })
      .mockResolvedValueOnce({ run_id: 'r2', session_id: 's1' })
    const listeners: Array<(event: SSEEnvelope) => void> = []
    subscribeMock.mockImplementation(
      (
        _runId: string,
        onEvent: (event: SSEEnvelope) => void,
      ) => {
        listeners.push(onEvent)
        return vi.fn()
      },
    )
    const { result } = renderHook(() => useWorkbench())

    await act(async () => {
      await result.current.selectSession('s1')
      await result.current.start('first')
    })
    await waitFor(() => expect(listeners).toHaveLength(1))
    act(() => {
      listeners[0]({
        id: 9,
        type: 'run_end',
        run_id: 'r1',
        session_id: 's1',
        ts: 1,
        agent: 'main',
        data: {},
      })
    })
    await waitFor(() => expect(result.current.run.status).toBe('idle'))

    await act(async () => {
      await result.current.start('second')
    })
    await waitFor(() => expect(listeners).toHaveLength(2))

    expect(subscribeMock.mock.calls[1][0]).toBe('r2')
    expect(subscribeMock.mock.calls[1][3]).toEqual({ afterId: 0 })
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

  it('ignores session selection while a run is active', async () => {
    const listeners = new Map<string, (event: SSEEnvelope) => void>()
    apiMock.session.mockImplementation((id: string) => Promise.resolve(session(id)))
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
    expect(apiMock.session).not.toHaveBeenCalledWith('target-a')
    expect(result.current.session?.id).toBe('origin-b')

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

    await waitFor(() => expect(result.current.run.status).toBe('idle'))
    expect(result.current.session?.id).toBe('origin-b')
  })

  it('clears run transients before rendering a different completed session', async () => {
    const pendingB = deferred<SessionDetail>()
    let onRunEvent: ((event: SSEEnvelope) => void) | undefined
    apiMock.session.mockImplementation((id: string) =>
      id === 'b' ? pendingB.promise : Promise.resolve(session(id)),
    )
    apiMock.startRun.mockResolvedValue({ run_id: 'r-a', session_id: 'a' })
    subscribeMock.mockImplementation(
      (_runId: string, onEvent: (event: SSEEnvelope) => void) => {
        onRunEvent = onEvent
        return vi.fn()
      },
    )
    const { result } = renderHook(() => useWorkbench())

    await act(async () => {
      await result.current.selectSession('a')
      await result.current.start('question')
    })
    await waitFor(() => expect(onRunEvent).toBeDefined())
    act(() => {
      onRunEvent?.({
        id: 1,
        type: 'llm_delta',
        run_id: 'r-a',
        session_id: 'a',
        ts: 1,
        agent: 'main',
        data: { text: 'answer A' },
      })
      onRunEvent?.({
        id: 2,
        type: 'plan_updated',
        run_id: 'r-a',
        session_id: 'a',
        ts: 2,
        agent: 'main',
        data: { plan: 'plan A' },
      })
      onRunEvent?.({
        id: 3,
        type: 'tool_start',
        run_id: 'r-a',
        session_id: 'a',
        ts: 3,
        agent: 'worker-a',
        data: { id: 't1', tool: 'search' },
      })
      onRunEvent?.({
        id: 4,
        type: 'run_end',
        run_id: 'r-a',
        session_id: 'a',
        ts: 4,
        agent: 'main',
        data: {},
      })
    })
    await waitFor(() => expect(result.current.run.status).toBe('idle'))
    expect(result.current.run.streamingText).toBe('answer A')

    act(() => {
      void result.current.selectSession('b')
    })

    expect(result.current.session).toBeNull()
    expect(result.current.run.streamingText).toBe('')
    expect(result.current.run.plan).toBe('')
    expect(result.current.run.tools).toEqual([])
    await act(async () => {
      pendingB.resolve(session('b'))
    })
    await waitFor(() => expect(result.current.session?.id).toBe('b'))
  })
})
