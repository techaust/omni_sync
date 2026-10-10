import { render } from '@testing-library/react'
import { RouterProvider, createMemoryRouter } from 'react-router'
import { vi } from 'vitest'

import type { Me } from '@/api/client'
import { AppProviders, makeQueryClient } from '@/app'
import { routes } from '@/router'

type Handler = (init: RequestInit | undefined) => Response | Promise<Response>

export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

export const problem = (code: string, status: number, title = code) => json({ code, title, status }, status)

export function makeMe(overrides: Partial<Me> = {}): Me {
  return {
    id: 1,
    username: 'owner',
    display_name: 'Owner',
    role: 'admin',
    permissions: ['view', 'account.self', 'ai.use', 'users.manage'],
    preferences: { color_mode: 'system', density: 'compact', browser_notifications: false },
    csrf_token: 'csrf-1',
    instance: { id: 'c2f0-test', name: 'owner-pc' },
    ...overrides,
  }
}

/** Route `METHOD /path` (without /api/v1) to a handler; unknown requests fail the test. */
export function mockApi(handlers: Record<string, Handler>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url
    const key = `${init?.method ?? 'GET'} ${url.replace('/api/v1', '')}`
    const handler = handlers[key]
    if (!handler) throw new Error(`unexpected request: ${key}`)
    return handler(init)
  })
}

export function renderApp(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] })
  const client = makeQueryClient()
  const view = render(
    <AppProviders client={client}>
      <RouterProvider router={router} />
    </AppProviders>,
  )
  return { ...view, router, client }
}
