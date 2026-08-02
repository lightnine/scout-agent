import { describe, expect, it, vi } from 'vitest'
import { subscribeToRun } from './sse'

type Listener = (event: MessageEvent) => void

function sourceFixture() {
  const listeners: Record<string, Listener> = {}
  return {
    onerror: null as ((event: Event) => void) | null,
    readyState: 0,
    addEventListener: vi.fn((name: string, handler: Listener) => {
      listeners[name] = handler
    }),
    close: vi.fn(),
    listeners,
  }
}

describe('subscribeToRun', () => {
  it('parses a named SSE envelope and closes cleanly', () => {
    const received = vi.fn()
    const source = sourceFixture()
    const close = subscribeToRun('r1', received, vi.fn(), () => source as never)

    source.listeners.run_start({ data: JSON.stringify({ id: 1, type: 'run_start' }) } as MessageEvent)

    expect(received).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }))
    close()
    expect(source.close).toHaveBeenCalledTimes(1)
  })

  it('does not report a fatal error while EventSource reconnects', () => {
    const source = sourceFixture()
    const error = vi.fn()
    subscribeToRun('r1', vi.fn(), error, () => source as never)

    source.onerror?.(new Event('error'))

    expect(error).not.toHaveBeenCalled()
  })

  it('reports terminal EventSource errors', () => {
    const source = sourceFixture()
    source.readyState = 2
    const error = vi.fn()
    subscribeToRun('r1', vi.fn(), error, () => source as never)

    source.onerror?.(new Event('error'))

    expect(error).toHaveBeenCalledOnce()
  })

  it('reports malformed event payloads', () => {
    const source = sourceFixture()
    const error = vi.fn()
    subscribeToRun('r1', vi.fn(), error, () => source as never)

    source.listeners.run_start({ data: '{invalid' } as MessageEvent)

    expect(error).toHaveBeenCalledWith(expect.objectContaining({ type: 'parse-error' }))
  })
})
