export type ApprovalAction = 'approve' | 'revise' | 'reject' | 'allow_session' | 'cancel'

export interface SSEEnvelope {
  id: number
  type: string
  run_id: string
  session_id: string
  ts: number
  agent: string
  data: Record<string, unknown>
}

export interface CreateSessionRequest {
  title?: string
}

export interface CreateRunRequest {
  question: string
}

export interface CreateRunResponse {
  run_id: string
  session_id: string
}

export interface ApprovalSubmitRequest {
  action: ApprovalAction
  feedback?: string
}

export interface CancelRunResponse {
  run_id: string
  status: 'cancelling' | 'already_finished'
}

export interface HealthResponse {
  status: 'ok'
  active_run_id: string | null
}

export interface SessionSummary {
  id: string
  title: string
  message_count: number
  created_at: number
  updated_at: number
}

export interface SessionDetail {
  id: string
  title: string
  messages: Array<{ role: string; content: string }>
  plan: string
  plan_steps: string[]
  plan_current: number
  sources: Array<{ label: string; title: string; url: string; fetched_at: number }>
  usage: Record<string, number>
  active_run_id: string | null
  run_status: string
}
