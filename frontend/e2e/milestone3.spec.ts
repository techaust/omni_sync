/**
 * Milestone 3 exit criterion: setup, sign-in and user management work end to end in a real
 * browser against the real server, with zero serious/critical axe violations and zero CSP
 * violations. Tests share one server and data directory, so they run in order.
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { AxeBuilder } from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'

test.describe.configure({ mode: 'serial' })

const ADMIN = { username: 'owner', password: 'correct horse battery staple' }
const OPERATOR = { username: 'colleague', password: 'violet-anchor-meadow-42' }

function setupToken(): string {
  const file = path.join(process.env.OMNISYNC_E2E_DATA_DIR!, 'setup-token.txt')
  return readFileSync(file, 'utf8').trim()
}

/** Fails the test on any Content-Security-Policy violation or uncaught page error. */
function watchForViolations(page: Page): string[] {
  const problems: string[] = []
  page.on('console', (msg) => {
    if (msg.type() === 'error' && /Content[- ]Security[- ]Policy|CSP/i.test(msg.text())) problems.push(msg.text())
  })
  page.on('pageerror', (err) => problems.push(`pageerror: ${err.message}`))
  return problems
}

async function expectAccessible(page: Page, label: string) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze()
  const serious = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
  expect(
    serious,
    `${label}: ${JSON.stringify(
      serious.map((v) => [v.id, v.nodes.map((n) => n.target)]),
      null,
      1,
    )}`,
  ).toEqual([])
}

async function signIn(page: Page, who: { username: string; password: string }) {
  await page.goto('/login')
  await page.getByLabel('Username').fill(who.username)
  await page.getByLabel('Password').fill(who.password)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible()
}

async function signOut(page: Page) {
  await page.getByRole('button', { name: /Account menu/ }).click()
  await page.getByRole('menuitem', { name: 'Sign out' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Sign in' })).toBeVisible()
}

test('first run: setup creates the admin and opens the dashboard', async ({ page }) => {
  const violations = watchForViolations(page)
  await page.goto('/')
  await expect(page).toHaveURL(/\/setup$/)
  await expectAccessible(page, 'setup')

  await page.getByLabel('Setup token').fill(setupToken())
  await page.getByLabel('Username').fill(ADMIN.username)
  await page.getByLabel('Display name').fill('Owner')
  await page.getByLabel('Password', { exact: true }).fill(ADMIN.password)
  await page.getByLabel('Repeat password').fill(ADMIN.password)
  await page.getByRole('button', { name: 'Create administrator' }).click()

  await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible()
  await expect(page.getByText('e2e-pc').first()).toBeVisible()
  await expect(page.getByText('OK').first()).toBeVisible()
  await expectAccessible(page, 'dashboard')
  expect(violations).toEqual([])
})

test('sign-in rejects a wrong password and accepts the right one', async ({ page }) => {
  const violations = watchForViolations(page)
  await page.goto('/login')
  await expectAccessible(page, 'login')
  await page.getByLabel('Username').fill(ADMIN.username)
  await page.getByLabel('Password').fill('not-the-password')
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByRole('alert')).toContainText('Wrong username or password')
  await signIn(page, ADMIN)
  expect(violations).toEqual([])
})

test('keyboard: the first Tab reaches the skip link, which moves focus to the content', async ({ page }) => {
  await signIn(page, ADMIN)
  await page.keyboard.press('Tab')
  const skip = page.getByRole('link', { name: 'Skip to content' })
  await expect(skip).toBeFocused()
  await page.keyboard.press('Enter')
  await expect(page.locator('#main')).toBeFocused()
})

test('admin creates an operator; the operator cannot see user management', async ({ page }) => {
  const violations = watchForViolations(page)
  await signIn(page, ADMIN)
  await page.getByRole('link', { name: 'Users' }).first().click()
  await expect(page.getByRole('heading', { level: 1, name: 'Users' })).toBeVisible()
  await expectAccessible(page, 'users')

  await page.getByRole('button', { name: 'Add user' }).click()
  const dialog = page.getByRole('dialog')
  await expectAccessible(page, 'add-user dialog')
  await dialog.getByLabel('Username').fill(OPERATOR.username)
  await dialog.getByLabel('Display name').fill('Colleague')
  await dialog.getByLabel('Initial password').fill(OPERATOR.password)
  await dialog.getByRole('button', { name: 'Create user' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByRole('cell', { name: OPERATOR.username, exact: true })).toBeVisible()
  expect(violations, 'opening a Radix dialog must not trip the CSP (nonce wiring)').toEqual([])

  await signOut(page)
  await signIn(page, OPERATOR)
  await expect(page.getByRole('link', { name: 'Users' })).toHaveCount(0)
  await page.goto('/settings/users')
  await expect(page.getByRole('heading', { name: 'Not available' })).toBeVisible()
})

test('dark preference is rendered by the server before first paint', async ({ page, request }) => {
  const violations = watchForViolations(page)
  await signIn(page, ADMIN)
  await page.getByRole('button', { name: /Account menu/ }).click()
  await page.getByRole('menuitem', { name: 'Account' }).click()
  await page.getByRole('combobox', { name: 'Colour mode' }).click()
  await page.getByRole('option', { name: 'Dark' }).click()
  await expect(page.locator('html')).toHaveClass(/dark/)
  // A toast renders with Sonner's stylesheet and without inline <style> injection (CSP).
  await expect(page.getByText('Preferences saved')).toBeVisible()
  await expectAccessible(page, 'account (dark)')
  expect(violations).toEqual([])

  // The raw HTML (before any script runs) already carries the class: no flash of light theme.
  const cookies = await page.context().cookies()
  const html = await request.get('/', {
    headers: { Cookie: cookies.map((c) => `${c.name}=${c.value}`).join('; ') },
  })
  expect(await html.text()).toContain('<html lang="en" class="dark">')
  expect(html.headers()['content-security-policy']).toContain("'nonce-")
})
