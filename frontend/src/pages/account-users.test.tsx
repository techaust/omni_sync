import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { applyColorMode, applyDensity } from '@/lib/theme'
import { json, makeMe, mockApi, problem, renderApp } from '@/test/render'

const SIGNED_IN = {
  'GET /setup/status': () => json({ setup_required: false }),
  'GET /auth/me': () => json(makeMe()),
}

function body(init: RequestInit | undefined): unknown {
  return JSON.parse(String(init?.body))
}

describe('account', () => {
  it('changes the password with matching confirmation', async () => {
    let sent: unknown
    mockApi({
      ...SIGNED_IN,
      'POST /auth/password': (init) => {
        sent = body(init)
        return new Response(null, { status: 204 })
      },
    })
    renderApp('/account')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Current password'), 'old-password-value')
    await user.type(screen.getByLabelText('New password'), 'a-much-better-passphrase')
    await user.type(screen.getByLabelText('Repeat new password'), 'a-much-better-passphrase')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    await waitFor(() =>
      expect(sent).toEqual({ current_password: 'old-password-value', new_password: 'a-much-better-passphrase' }),
    )
    expect(await screen.findByText(/other sessions were signed out/)).toBeInTheDocument()
  })

  it('rejects a short new password without calling the API', async () => {
    const fetch = mockApi(SIGNED_IN)
    renderApp('/account')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('New password'), 'short')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('Use at least 12 characters.')).toBeInTheDocument()
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })

  it('shows the server error for a wrong current password', async () => {
    mockApi({
      ...SIGNED_IN,
      'POST /auth/password': () =>
        json(
          {
            code: 'validation.failed',
            title: 'Validation failed',
            status: 422,
            errors: [{ field: 'current_password', message: 'incorrect' }],
          },
          422,
        ),
    })
    renderApp('/account')
    const user = userEvent.setup()
    await user.type(await screen.findByLabelText('Current password'), 'wrong-password-x')
    await user.type(screen.getByLabelText('New password'), 'a-much-better-passphrase')
    await user.type(screen.getByLabelText('Repeat new password'), 'a-much-better-passphrase')
    await user.click(screen.getByRole('button', { name: 'Change password' }))
    expect(await screen.findByText('incorrect')).toBeInTheDocument()
  })

  it('saves a colour-mode preference and applies it', async () => {
    let sent: unknown
    mockApi({
      ...SIGNED_IN,
      'PATCH /auth/preferences': (init) => {
        sent = body(init)
        return json(makeMe({ preferences: { color_mode: 'dark', density: 'compact', browser_notifications: false } }))
      },
    })
    renderApp('/account')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('combobox', { name: 'Colour mode' }))
    await user.click(await screen.findByRole('option', { name: 'Dark' }))
    await waitFor(() => expect(sent).toEqual({ color_mode: 'dark' }))
    await waitFor(() => expect(document.documentElement).toHaveClass('dark'))
  })
})

describe('users: create', () => {
  it('creates a user and refreshes the list', async () => {
    let created: unknown
    let listCalls = 0
    mockApi({
      ...SIGNED_IN,
      'GET /users': () => {
        listCalls += 1
        return json({ items: [] })
      },
      'POST /users': (init) => {
        created = body(init)
        return json(
          {
            id: 2,
            username: 'colleague',
            display_name: 'Colleague',
            role: 'operator',
            disabled: false,
            locked: false,
            created_at: '2026-10-11T00:00:00Z',
            updated_at: '2026-10-11T00:00:00Z',
          },
          201,
        )
      },
    })
    renderApp('/settings/users')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Add user' }))
    const dialog = await screen.findByRole('dialog')
    await user.type(within(dialog).getByLabelText('Username'), 'colleague')
    await user.type(within(dialog).getByLabelText('Display name'), 'Colleague')
    await user.type(within(dialog).getByLabelText('Initial password'), 'violet-anchor-meadow-42')
    await user.click(within(dialog).getByRole('button', { name: 'Create user' }))
    await waitFor(() =>
      expect(created).toEqual({
        username: 'colleague',
        display_name: 'Colleague',
        role: 'operator',
        password: 'violet-anchor-meadow-42',
      }),
    )
    await waitFor(() => expect(listCalls).toBe(2))
  })

  it('surfaces the last-admin guard when disabling', async () => {
    mockApi({
      ...SIGNED_IN,
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
          ],
        }),
      'PATCH /users/1': () => problem('user.last_admin', 409, 'The last active admin cannot be removed'),
    })
    renderApp('/settings/users')
    const user = userEvent.setup()
    await user.click(await screen.findByRole('button', { name: 'Edit owner' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Disable account' }))
    expect(await within(dialog).findByText('The last active admin cannot be removed')).toBeInTheDocument()
  })
})

describe('theme helpers', () => {
  it('sets and clears the colour-mode class and density attribute', () => {
    const root = document.documentElement
    applyColorMode('dark', root)
    expect(root.className).toBe('dark')
    applyColorMode('light', root)
    expect(root.className).toBe('light')
    applyColorMode('system', root)
    expect(root.className).toBe('')
    applyDensity('comfortable', root)
    expect(root.dataset.density).toBe('comfortable')
  })
})
