import type { SSEEnvelope } from './types'

const EVENT_TYPES = [
  'run_start',
  'run_end',
  'step_start',
  'llm_start',
  'llm_delta',
  'llm_end',
  'tool_start',
  'tool_end',
  'memory_recall',
  'compaction',
  'plan_updated',
  'subagent_start',
  'subagent_end',
  'approval_required',
  'approval_resolved',
  'error',
]

export function subscribeToRun(
  runId: string,
  onEvent: (event: SSEEnvelope) => void,
  onError: (error: Event) => void,
  factory: (url: string) => EventSource = (url) => new EventSource(url),
): () => void {
  const source = factory(`/api/runs/${encodeURIComponent(runId)}/events`)
  let closed = false

  const close = () => {
    if (!closed) {
      closed = true
      source.close()
    }
  }

  const handle = (raw: Event) => {
    try {
      const event = JSON.parse((raw as MessageEvent<string>).data) as SSEEnvelope
      onEvent(event)
      if (event.type === 'run_end') {
        close()
      }
    } catch {
      onError(new Event('parse-error'))
    }
  }

  for (const type of EVENT_TYPES) {
    source.addEventListener(type, handle)
  }
  source.onerror = (event) => {
    if (!closed && source.readyState === 2) {
      onError(event)
    }
  }

  return close
}
