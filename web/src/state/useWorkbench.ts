import { useCallback, useEffect, useReducer, useRef, useState } from 'react'
import { api } from '../api/client'
import { subscribeToRun } from '../api/sse'
import type { ApprovalAction, SSEEnvelope, SessionDetail, SessionSummary } from '../api/types'
import { initialRunState, runReducer } from './runReducer'

interface ActiveRun {
  runId: string
  sessionId: string
}

function runStartEvent(runId: string, sessionId: string, input = ''): SSEEnvelope {
  return {
    id: 0,
    type: 'run_start',
    run_id: runId,
    session_id: sessionId,
    ts: Date.now() / 1000,
    agent: 'main',
    data: input ? { input } : {},
  }
}

export function useWorkbench() {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [session, setSession] = useState<SessionDetail | null>(null)
  const [run, dispatch] = useReducer(runReducer, initialRunState)
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadingSessions, setLoadingSessions] = useState(true)
  const [loadingSession, setLoadingSession] = useState(false)
  const [connectionRevision, setConnectionRevision] = useState(0)
  const sessionRef = useRef<SessionDetail | null>(null)
  const selectedSessionIdRef = useRef<string | null>(null)
  const selectionRequestRef = useRef(0)
  const sessionsRequestRef = useRef(0)
  const activeRunRef = useRef<ActiveRun | null>(null)
  const subscribedRunIdRef = useRef<string | null>(null)
  const previousStatusRef = useRef(run.status)

  const applySession = useCallback((next: SessionDetail | null) => {
    sessionRef.current = next
    setSession(next)
  }, [])

  const refreshSessions = useCallback(async () => {
    const requestId = ++sessionsRequestRef.current
    setLoadingSessions(true)
    setError(null)
    try {
      const next = await api.sessions()
      if (requestId === sessionsRequestRef.current) {
        setSessions(next)
      }
    } catch (cause) {
      if (requestId === sessionsRequestRef.current) {
        setError(cause instanceof Error ? cause.message : '无法刷新会话列表')
      }
      throw cause
    } finally {
      if (requestId === sessionsRequestRef.current) {
        setLoadingSessions(false)
      }
    }
  }, [])

  const activateRun = useCallback((next: ActiveRun, input = '') => {
    if (activeRunRef.current?.runId === next.runId) {
      return
    }
    activeRunRef.current = next
    setActiveRun(next)
    dispatch(runStartEvent(next.runId, next.sessionId, input))
  }, [])

  const selectSession = useCallback(
    async (id: string) => {
      const requestId = ++selectionRequestRef.current
      selectedSessionIdRef.current = id
      setError(null)
      setLoadingSession(true)
      try {
        const detail = await api.session(id)
        if (requestId !== selectionRequestRef.current || selectedSessionIdRef.current !== id) {
          return
        }
        applySession(detail)
        if (detail.active_run_id) {
          activateRun({ runId: detail.active_run_id, sessionId: detail.id })
        }
      } catch (cause) {
        if (requestId === selectionRequestRef.current) {
          setError(cause instanceof Error ? cause.message : '无法加载会话')
        }
        throw cause
      } finally {
        if (requestId === selectionRequestRef.current) {
          setLoadingSession(false)
        }
      }
    },
    [activateRun, applySession],
  )

  const refreshSession = useCallback(
    async (id: string) => {
      const requestId = ++selectionRequestRef.current
      try {
        const detail = await api.session(id)
        if (requestId === selectionRequestRef.current && selectedSessionIdRef.current === id) {
          applySession(detail)
        }
      } catch (cause) {
        if (requestId === selectionRequestRef.current) {
          setError(cause instanceof Error ? cause.message : '无法刷新会话')
        }
        throw cause
      }
    },
    [applySession],
  )

  const refreshCompletedSession = useCallback(
    async (id: string) => {
      if (selectedSessionIdRef.current !== id) {
        return
      }
      const selectionRequestId = selectionRequestRef.current
      try {
        const detail = await api.session(id)
        if (
          selectionRequestId === selectionRequestRef.current &&
          selectedSessionIdRef.current === id
        ) {
          applySession(detail)
        }
      } catch (cause) {
        if (
          selectionRequestId === selectionRequestRef.current &&
          selectedSessionIdRef.current === id
        ) {
          setError(cause instanceof Error ? cause.message : '无法刷新会话')
        }
        throw cause
      }
    },
    [applySession],
  )

  useEffect(() => {
    void refreshSessions().catch(() => undefined)
  }, [refreshSessions])

  useEffect(() => {
    if (!activeRun || activeRun.runId !== run.runId) {
      return
    }
    const { runId, sessionId } = activeRun
    subscribedRunIdRef.current = runId
    const close = subscribeToRun(
      runId,
      (event) => {
        if (activeRunRef.current?.runId === runId && event.run_id === runId) {
          dispatch(event)
        }
      },
      () => {
        if (activeRunRef.current?.runId === runId) {
          dispatch({
            id: -1,
            type: 'error',
            run_id: runId,
            session_id: sessionId,
            ts: Date.now() / 1000,
            agent: 'main',
            data: { error: '事件流连接已关闭' },
          })
        }
      },
    )
    return () => {
      if (subscribedRunIdRef.current === runId) {
        subscribedRunIdRef.current = null
      }
      close()
    }
  }, [activeRun, connectionRevision, run.runId])

  useEffect(() => {
    const wasActive = previousStatusRef.current !== 'idle'
    if (wasActive && run.status === 'idle') {
      const completedRun = activeRunRef.current
      activeRunRef.current = null
      setActiveRun(null)
      void refreshSessions().catch(() => undefined)
      if (completedRun) {
        void refreshCompletedSession(completedRun.sessionId).catch(() => undefined)
      }
    }
    previousStatusRef.current = run.status
  }, [refreshCompletedSession, refreshSessions, run.status])

  const createSession = useCallback(
    async (title = '') => {
      setError(null)
      const selectionAtStart = selectedSessionIdRef.current
      const selectionRequestAtStart = selectionRequestRef.current
      try {
        const created = await api.createSession(title)
        const shouldSelectCreated =
          selectionRequestRef.current === selectionRequestAtStart &&
          selectedSessionIdRef.current === selectionAtStart
        if (shouldSelectCreated) {
          selectedSessionIdRef.current = created.id
        }
        const requestId = shouldSelectCreated ? ++selectionRequestRef.current : selectionRequestRef.current
        const detail = await api.session(created.id)
        if (
          shouldSelectCreated &&
          requestId === selectionRequestRef.current &&
          selectedSessionIdRef.current === created.id
        ) {
          applySession(detail)
        }
        void refreshSessions().catch(() => undefined)
        return detail
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '无法创建会话')
        throw cause
      }
    },
    [applySession, refreshSessions],
  )

  const start = useCallback(
    async (question: string) => {
      setError(null)
      try {
        const target = sessionRef.current ?? (await createSession())
        const created = await api.startRun(target.id, question)
        activateRun({ runId: created.run_id, sessionId: created.session_id }, question)
        void refreshSessions().catch(() => undefined)
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '无法启动运行')
        throw cause
      }
    },
    [activateRun, createSession, refreshSessions],
  )

  const decide = useCallback(
    async (action: ApprovalAction, feedback = '') => {
      const approval = run.approval
      if (!approval) {
        return
      }
      setError(null)
      try {
        await api.approve(approval.approval_id, action, feedback)
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '无法提交审批')
        throw cause
      }
    },
    [run.approval],
  )

  const cancel = useCallback(async () => {
    const runId = activeRunRef.current?.runId
    if (!runId) {
      return
    }
    setError(null)
    dispatch({
      id: -1,
      type: 'cancel_requested',
      run_id: runId,
      session_id: activeRunRef.current?.sessionId ?? '',
      ts: Date.now() / 1000,
      agent: 'main',
      data: {},
    })
    try {
      await api.cancel(runId)
    } catch (cause) {
      dispatch({
        id: -1,
        type: 'error',
        run_id: runId,
        session_id: activeRunRef.current?.sessionId ?? '',
        ts: Date.now() / 1000,
        agent: 'main',
        data: { error: cause instanceof Error ? cause.message : '无法取消运行' },
      })
      throw cause
    }
  }, [])

  const reconnect = useCallback(() => {
    if (activeRunRef.current) {
      setConnectionRevision((revision) => revision + 1)
      return
    }
    const selectedId = selectedSessionIdRef.current ?? sessionRef.current?.id
    if (selectedId) {
      void refreshSession(selectedId).catch(() => undefined)
      return
    }
    void refreshSessions().catch(() => undefined)
  }, [refreshSession, refreshSessions])

  const dismissError = useCallback(() => {
    setError(null)
    dispatch({
      id: -1,
      type: 'error_dismissed',
      run_id: activeRunRef.current?.runId ?? '',
      session_id: activeRunRef.current?.sessionId ?? '',
      ts: Date.now() / 1000,
      agent: 'main',
      data: {},
    })
  }, [])

  return {
    sessions,
    session,
    run,
    error,
    loadingSessions,
    loadingSession,
    refreshSessions,
    refreshSession,
    selectSession,
    createSession,
    start,
    decide,
    cancel,
    reconnect,
    dismissError,
    api,
  }
}
