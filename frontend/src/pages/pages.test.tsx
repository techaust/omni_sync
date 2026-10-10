import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { safeNext } from '@/pages/login'
import { json, makeMe, mockApi, problem, renderApp } from '@/test/render'

const HEALTH = {
  version: '2.0.0.dev0',
  instance: { id: 'c2f0-test', name: 'owner-pc' },
  checks: [
    { name: 'database', ok: true, detail: 'SQLite journal mode wal' },
    { name: 'schema', ok: false, detail: 'migration 0001 (latest 0002)' },
  ],
}

describe('routing and guards', () => {
  it('sends a fresh install to setup', async () => {
    mockApi({ 'GET /setup/status': () => json({ setup_required: true }) })
    const { router } = renderApp('/')
    await waitFor(() => expect(router.state.location.pathname).toBe('/setup'))
    expect(await screen.findByRole('heading', { level: 1, name: 'Set up OmniSync' })).toBeInTheDocument()
  })

  it('sends signed-out users to login and keeps the requested path', async () => {
    mockApi({
      'GET /setup/status': () => json({ setup_required: false }),
      'GET /auth/me': () => problem('auth.required', 401),
    })
    const { router } = renderApp('/account')
    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(router.state.location.search).toBe('?next=%2Faccount')
  })

  it('rejects open redirects in ?next=', () => {
    expect(safeNext('/account')).toBe('/account')
    expect(safeNext('//evil.example')).toBe('/')
    expect(safeNext('https://evil.example')).toBe('/')
    expect(safeNext(null)).toBe('/')
  })
})

describe('login', () => {
  it('explains wrong credentials and lockout', async () => {
    mockApi({
      'GET /setup/status': () => json({ setup_required: false }),
      'GET /auth/me': () => problem('auth.required', 401),
      'POST /auth/login': () => problem('auth.invalid_credentials', 401),
    })
    renderApp('/login')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Username'), 'owner')
    await user.type(screen.getByLabelText('Password'), 'wrong-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/locked for 15 minutes/)
  })

  it('signs in and opens the dashboard', async () => {
    mockApi({
      'GET /setup/status': () => json({ setup_required: false }),
      'GET /auth/me': () => problem('auth.required', 401),
      'POST /auth/login': () => json(makeMe()),
      'GET /health/details': () => json(HEALTH),
    })
    const { router } = renderApp('/login')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Username'), 'owner')
    await user.type(screen.getByLabelText('Password'), 'correct horse battery staple')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
  })
})

describe('setup', () => {
  it('validates the password locally before calling the API', async () => {
    const fetch = mockApi({ 'GET /setup/status': () => json({ setup_required: true }) })
    renderApp('/setup')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Setup token'), 'AAAAA')
    await user.type(screen.getByLabelText('Password'), 'long-enough-password')
    await user.type(screen.getByLabelText('Repeat password'), 'different-password-x')
    await user.click(screen.getByRole('button', { name: 'Create administrator' }))
    expect(await screen.findByText('The passwords do not match.')).toBeInTheDocument()
    expect(screen.getByLabelText('Repeat password')).toHaveAttribute('aria-invalid', 'true')
    expect(fetch.mock.calls.every(([, init]) => (init?.method ?? 'GET') === 'GET')).toBe(true)
  })

  it('shows a clear message for an invalid token', async () => {
    mockApi({
      'GET /setup/status': () => json({ setup_required: true }),
      'POST /setup': () =>
        json(
          {
            code: 'setup.invalid_token',
            title: 'Invalid setup token',
            status: 422,
            errors: [{ field: 'setup_token', message: 'invalid' }],
          },
          422,
        ),
    })
    renderApp('/setup')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Setup token'), 'WRONG')
    await user.type(screen.getByLabelText('Username'), 'owner')
    await user.type(screen.getByLabelText('Display name'), 'Owner')
    await user.type(screen.getByLabelText('Password'), 'correct horse battery staple')
    await user.type(screen.getByLabelText('Repeat password'), 'correct horse battery staple')
    await user.click(screen.getByRole('button', { name: 'Create administrator' }))
    expect(await screen.findByText(/This token is not valid/)).toBeInTheDocument()
  })
})

describe('shell and dashboard', () => {
  it('has a skip link, landmarks and health with text status labels', async () => {
    mockApi({
      'GET /setup/status': () => json({ setup_required: false }),
      'GET /auth/me': () => json(makeMe()),
      'GET /health/details': () => json(HEALTH),
    })
    renderApp('/')
    expect(await screen.findByRole('link', { name: 'Skip to content' })).toHaveAttribute('href', '#main')
    expect(screen.getByRole('main')).toHaveAttribute('id', 'main')
    const health = await screen.findByRole('heading', { name: 'Health' })
    const card = health.closest('[data-slot="card"]') as HTMLElement
    expect(await within(card).findByText('OK')).toBeInTheDocument()
    expect(within(card).getByText('Problem')).toBeInTheDocument()
  })

  it('hides admin-only navigation from operators and blocks the page', async () => {
    mockApi({
      'GET /setup/status': () => json({ setup_required: false }),
      'GET /auth/me': () => json(makeMe({ role: 'operator', permissions: ['view', 'account.self', 'runs.write'] })),
    })
    renderApp('/settings/users')
    expect(await screen.findByRole('heading', { name: 'Not available' })).toBeInTheDocument()
    const nav = screen.getAllByRole('navigation', { name: 'Main' })[0]!
    expect(within(nav).queryByRole('link', { name: 'Users' })).not.toBeInTheDocument()
    expect(within(nav).getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
  })

  it('applies the colour mode from the user preference', async () => {
    mockApi({
      'GET /setup/status': () => json({ setup_required: false }),
      'GET /auth/me': () =>
        json(makeMe({ preferences: { color_mode: 'dark', density: 'comfortable', browser_notifications: false } })),
      'GET /health/details': () => json(HEALTH),
    })
    renderApp('/')
    await waitFor(() => expect(document.documentElement).toHaveClass('dark'))
    expect(document.documentElement.dataset.density).toBe('comfortable')
  })
})

describe('users', () => {
  it('lists users with role and status', async () => {
    mockApi({
      'GET /setup/status': () => json({ setup_required: false }),
      'GET /auth/me': () => json(makeMe()),
      'GET /users': () =>
        json({
          items: [
            {
              id: 1,
              username: 'owner',
              display_name: 'Owner',
              role: 'admin',
              disabled: false,
              locked: false,
              created_at: '2026-10-11T00:00:00Z',
              updated_at: '2026-10-11T00:00:00Z',
            },
            {
              id: 2,
              username: 'colleague',
              display_name: 'Colleague',
              role: 'operator',
              disabled: true,
              locked: false,
              created_at: '2026-10-11T00:00:00Z',
              updated_at: '2026-10-11T00:00:00Z',
            },
          ],
        }),
    })
    renderApp('/settings/users')
    const table = await screen.findByRole('table')
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(3)
    expect(within(rows[1]!).getByText('(you)')).toBeInTheDocument()
    expect(within(rows[2]!).getByText('Disabled')).toBeInTheDocument()
    expect(within(rows[2]!).getByRole('button', { name: 'Edit colleague' })).toBeInTheDocument()
  })
})
