import { describe, expect, it } from 'vitest'
import type { SSEEnvelope } from '../api/types'
import { initialRunState, runReducer } from './runReducer'

const event = (type: string, data: Record<string, unknown>): SSEEnvelope => ({
  id: 1,
  type,
  run_id: 'r1',
  session_id: 's1',
  ts: 1,
  agent: 'main',
  data,
})

describe('runReducer', () => {
  it('merges streaming deltas into one assistant message', () => {
    let state = runReducer(initialRunState, event('run_start', { input: 'question' }))
    state = runReducer(state, event('llm_delta', { text: 'hello ' }))
    state = runReducer(state, event('llm_delta', { text: 'world' }))
    expect(state.streamingText).toBe('hello world')
  })

  it('opens and resolves approval', () => {
    let state = runReducer(
      initialRunState,
      event('approval_required', { approval_id: 'a1', kind: 'plan', title: 'Plan', payload: {} }),
    )
    expect(state.approval?.approval_id).toBe('a1')
    state = runReducer(state, event('approval_resolved', { approval_id: 'a1', action: 'approve' }))
    expect(state.approval).toBeNull()
  })

  it('updates plan, tools, sources and completion status', () => {
    let state = runReducer(initialRunState, event('plan_updated', { plan: '[>] Search' }))
    state = runReducer(state, event('tool_start', { id: 't1', tool: 'web_search', arguments: {} }))
    state = runReducer(state, event('tool_end', { id: 't1', tool: 'web_search', ok: true }))
    state = runReducer(state, event('run_end', { stop_reason: 'completed' }))
    expect(state.plan).toContain('Search')
    expect(state.tools[0].ok).toBe(true)
    expect(state.status).toBe('idle')
  })

  it('correlates concurrent same-name tools by id', () => {
    let state = runReducer(initialRunState, event('run_start', { input: 'question' }))
    state = runReducer(state, event('tool_start', { id: 'a', tool: 'web_search', arguments: {} }))
    state = runReducer(state, event('tool_start', { id: 'b', tool: 'web_search', arguments: {} }))
    state = runReducer(state, event('tool_end', { id: 'a', tool: 'web_search', ok: true }))
    expect(state.tools[0].ok).toBe(true)
    expect(state.tools[0].status).toBe('finished')
    expect(state.tools[1].status).toBe('running')
    expect(state.tools[1].ok).toBeUndefined()
  })

  it('resolves two concurrent same-name tools by id-specific ends', () => {
    let state = runReducer(initialRunState, event('run_start', { input: 'question' }))
    state = runReducer(state, event('tool_start', { id: 'a', tool: 'web_search', arguments: {} }))
    state = runReducer(state, event('tool_start', { id: 'b', tool: 'web_search', arguments: {} }))
    state = runReducer(state, event('tool_end', { id: 'a', tool: 'web_search', ok: true }))
    state = runReducer(state, event('tool_end', { id: 'b', tool: 'web_search', ok: false }))
    expect(state.tools[0].ok).toBe(true)
    expect(state.tools[0].status).toBe('finished')
    expect(state.tools[1].ok).toBe(false)
    expect(state.tools[1].status).toBe('finished')
  })

  it('completes only the first running same-name tool when id is absent', () => {
    let state = runReducer(initialRunState, event('run_start', { input: 'question' }))
    state = runReducer(state, event('tool_start', { id: 'a', tool: 'web_search', arguments: {} }))
    state = runReducer(state, event('tool_start', { id: 'b', tool: 'web_search', arguments: {} }))
    state = runReducer(state, event('tool_end', { tool: 'web_search', ok: true }))
    expect(state.tools[0].ok).toBe(true)
    expect(state.tools[0].status).toBe('finished')
    expect(state.tools[1].status).toBe('running')
    expect(state.tools[1].ok).toBeUndefined()
  })

  it('retains streaming text after run_end until session refresh', () => {
    let state = runReducer(initialRunState, event('run_start', { input: 'question' }))
    state = runReducer(state, event('llm_delta', { text: 'final answer' }))
    state = runReducer(state, event('run_end', { stop_reason: 'completed' }))
    expect(state.streamingText).toBe('final answer')
    expect(state.status).toBe('idle')
  })

  it('does not revert cancelling status on approval_resolved', () => {
    const cancelling = { ...initialRunState, status: 'cancelling' as const, runId: 'r1' }
    const next = runReducer(
      cancelling,
      event('approval_resolved', { approval_id: 'a1', action: 'cancel' }),
    )
    expect(next.status).toBe('cancelling')
  })

  it('ignores error events when already idle', () => {
    const idle = { ...initialRunState, status: 'idle' as const }
    const next = runReducer(idle, event('error', { error: 'late failure' }))
    expect(next).toBe(idle)
  })
})
