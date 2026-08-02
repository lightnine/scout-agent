import type { SSEEnvelope } from '../api/types'

export interface ApprovalView {
  approval_id: string
  kind: string
  title: string
  payload: Record<string, unknown>
}

export interface RunState {
  status: 'idle' | 'running' | 'awaiting_approval' | 'cancelling'
  runId: string | null
  streamingText: string
  plan: string
  tools: Array<Record<string, unknown>>
  approval: ApprovalView | null
  error: string | null
}

export const initialRunState: RunState = {
  status: 'idle',
  runId: null,
  streamingText: '',
  plan: '',
  tools: [],
  approval: null,
  error: null,
}

function applyToolEnd(
  tools: Array<Record<string, unknown>>,
  data: Record<string, unknown>,
): Array<Record<string, unknown>> {
  const endId = data.id
  if (endId != null) {
    return tools.map((tool) =>
      tool.status === 'running' && tool.id === endId
        ? { ...tool, ...data, status: 'finished' }
        : tool,
    )
  }

  let matched = false
  return tools.map((tool) => {
    if (!matched && tool.status === 'running' && tool.tool === data.tool) {
      matched = true
      return { ...tool, ...data, status: 'finished' }
    }
    return tool
  })
}

export function runReducer(state: RunState, event: SSEEnvelope): RunState {
  switch (event.type) {
    case 'run_start':
      return { ...initialRunState, status: 'running', runId: event.run_id }
    case 'llm_delta':
      if (state.status === 'idle') {
        return state
      }
      return { ...state, streamingText: state.streamingText + String(event.data.text ?? '') }
    case 'plan_updated':
      return { ...state, plan: String(event.data.plan ?? '') }
    case 'tool_start':
      return { ...state, tools: [...state.tools, { ...event.data, status: 'running' }] }
    case 'tool_end':
      return { ...state, tools: applyToolEnd(state.tools, event.data) }
    case 'approval_required':
      return {
        ...state,
        status: 'awaiting_approval',
        approval: event.data as unknown as ApprovalView,
      }
    case 'approval_resolved':
      if (state.status === 'cancelling') {
        return state.approval ? { ...state, approval: null } : state
      }
      return { ...state, status: 'running', approval: null }
    case 'cancel_requested':
      return state.runId ? { ...state, status: 'cancelling', approval: null } : state
    case 'error':
      if (state.status === 'idle') {
        return state
      }
      return { ...state, error: String(event.data.error ?? 'Unknown error') }
    case 'run_end':
      return { ...state, status: 'idle', runId: null, approval: null }
    default:
      return state
  }
}
