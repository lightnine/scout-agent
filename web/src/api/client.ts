import type {
  ApprovalAction,
  CancelRunResponse,
  CreateRunResponse,
  HealthResponse,
  SessionDetail,
  SessionSummary,
} from './types'

function formatDetail(detail: unknown): string | null {
  if (typeof detail === 'string' && detail.trim()) {
    return detail
  }
  if (Array.isArray(detail)) {
    const messages = detail.flatMap((entry) => {
      if (!entry || typeof entry !== 'object') {
        return []
      }
      const { loc, msg } = entry as { loc?: unknown; msg?: unknown }
      if (typeof msg !== 'string') {
        return []
      }
      const location = Array.isArray(loc) ? loc.map(String).join('.') : ''
      return [location ? `${location}: ${msg}` : msg]
    })
    return messages.length > 0 ? messages.join('; ') : null
  }
  return null
}

async function responseError(response: Response): Promise<Error> {
  const fallback = `HTTP ${response.status}${response.statusText ? ` ${response.statusText}` : ''}`
  const contentType = response.headers.get('content-type') ?? ''
  try {
    if (contentType.includes('application/json')) {
      const body: unknown = await response.json()
      if (body && typeof body === 'object') {
        const detail = formatDetail((body as { detail?: unknown }).detail)
        if (detail) {
          return new Error(detail)
        }
      }
      return new Error(fallback)
    }
    const text = await response.text()
    return new Error(text.trim() || fallback)
  } catch {
    return new Error(fallback)
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    throw await responseError(response)
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<HealthResponse>('/health'),
  sessions: () => request<SessionSummary[]>('/sessions'),
  session: (id: string) => request<SessionDetail>(`/sessions/${encodeURIComponent(id)}`),
  createSession: (title = '') =>
    request<SessionSummary>('/sessions', { method: 'POST', body: JSON.stringify({ title }) }),
  startRun: (sessionId: string, question: string) =>
    request<CreateRunResponse>(`/sessions/${encodeURIComponent(sessionId)}/runs`, {
      method: 'POST',
      body: JSON.stringify({ question }),
    }),
  approve: (approvalId: string, action: ApprovalAction, feedback = '') =>
    request<{ approval_id: string; status: string }>(`/approvals/${encodeURIComponent(approvalId)}`, {
      method: 'POST',
      body: JSON.stringify({ action, feedback }),
    }),
  cancel: (runId: string) =>
    request<CancelRunResponse>(`/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST' }),
}
