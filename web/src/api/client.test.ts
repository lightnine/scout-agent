import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './client'

describe('api client', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('uses the API prefix and JSON request body', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ run_id: 'run-1', session_id: 'session-1' }), { status: 202 }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await expect(api.startRun('session-1', 'What changed?')).resolves.toEqual({
      run_id: 'run-1',
      session_id: 'session-1',
    })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/sessions/session-1/runs',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ question: 'What changed?' }),
        headers: expect.objectContaining({ 'content-type': 'application/json' }),
      }),
    )
  })

  it('turns FastAPI validation detail into a readable error', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: [{ loc: ['body', 'question'], msg: 'Field required', type: 'missing' }],
          }),
          { status: 422, headers: { 'content-type': 'application/json' } },
        ),
      ),
    )

    await expect(api.startRun('session-1', '')).rejects.toThrow('body.question: Field required')
  })

  it('uses a string FastAPI detail when available', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          new Response(JSON.stringify({ detail: '找不到会话 session-1' }), {
            status: 404,
            headers: { 'content-type': 'application/json' },
          }),
        ),
    )

    await expect(api.session('session-1')).rejects.toThrow('找不到会话 session-1')
  })
})
