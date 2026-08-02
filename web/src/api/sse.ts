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

interface SubscribeOptions {
  afterId?: number
  factory?: (url: string) => EventSource
}

export function subscribeToRun(
  runId: string,
  onEvent: (event: SSEEnvelope) => void,
  onError: (error: Event) => void,
  options: SubscribeOptions = {},
): () => void {
  const afterId = options.afterId ?? 0
  if (!Number.isSafeInteger(afterId) || afterId < 0) {
    throw new RangeError('afterId must be a safe non-negative integer')
  }
  const factory = options.factory ?? ((url: string) => new EventSource(url))
  const baseUrl = `/api/runs/${encodeURIComponent(runId)}/events`
  const source = factory(afterId > 0 ? `${baseUrl}?after_id=${afterId}` : baseUrl)
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
