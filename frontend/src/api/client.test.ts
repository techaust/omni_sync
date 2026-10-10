import { afterEach, describe, expect, it, vi } from 'vitest'

import { ApiError, api, request, setCsrfToken } from '@/api/client'

function mockFetch(response: Response) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(response)
}

const json = (body: unknown, status = 200, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } })

afterEach(() => setCsrfToken(null))

describe('request', () => {
  it('sends the CSRF token on state-changing requests only', async () => {
    setCsrfToken('tok-123')
    const fetch = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => json({ ok: true }))
    await request('POST', '/x', { a: 1 })
    await request('GET', '/y')
    const [postInit, getInit] = fetch.mock.calls.map((call) => call[1] ?? {})
    expect((postInit!.headers as Record<string, string>)['X-CSRF-Token']).toBe('tok-123')
    expect((getInit!.headers as Record<string, string>)['X-CSRF-Token']).toBeUndefined()
    expect(postInit!.credentials).toBe('same-origin')
    expect(fetch.mock.calls[0]?.[0]).toBe('/api/v1/x')
  })

  it('turns Problem Details into ApiError with field errors', async () => {
    mockFetch(
      json(
        {
          code: 'password.weak',
          title: 'Password does not meet the policy',
          detail: 'Password must be at least 12 characters.',
          errors: [{ field: 'password', message: 'must be at least 12 characters' }],
          request_id: 'req-1',
        },
        422,
      ),
    )
    const error = await api.login({ username: 'a', password: 'b' }).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    const apiError = error as ApiError
    expect(apiError.status).toBe(422)
    expect(apiError.code).toBe('password.weak')
    expect(apiError.fieldError('password')).toBe('must be at least 12 characters')
    expect(apiError.requestId).toBe('req-1')
  })

  it('handles non-JSON error bodies', async () => {
    mockFetch(new Response('gateway down', { status: 502, statusText: 'Bad Gateway' }))
    const error = (await request('GET', '/x').catch((e: unknown) => e)) as ApiError
    expect(error.code).toBe('internal')
    expect(error.status).toBe(502)
  })

  it('reports an unreachable server as a network error', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new TypeError('Failed to fetch'))
    const error = (await request('GET', '/x').catch((e: unknown) => e)) as ApiError
    expect(error.code).toBe('network')
    expect(error.status).toBe(0)
  })

  it('returns undefined for 204 responses', async () => {
    mockFetch(new Response(null, { status: 204 }))
    await expect(api.logout()).resolves.toBeUndefined()
  })
})
