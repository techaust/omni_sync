import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { defineConfig, devices } from '@playwright/test'

/**
 * End-to-end tests against the real server (`omnisync serve`) serving the production build,
 * on a throw-away data directory. CI runs Chromium, Firefox and WebKit (09-TESTING-STRATEGY 2);
 * locally only Chromium runs by default because Windows application control on the owner's PC
 * blocks the Playwright Firefox build and WebKit lacks Windows host dependencies.
 */
const PORT = 8766
const DATA_DIR = process.env.OMNISYNC_E2E_DATA_DIR ?? mkdtempSync(path.join(tmpdir(), 'omnisync-e2e-'))
process.env.OMNISYNC_E2E_DATA_DIR = DATA_DIR
const ALL_BROWSERS = Boolean(process.env.CI) || process.env.E2E_ALL_BROWSERS === '1'

const browsers = [
  { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  { name: 'firefox', use: { ...devices['Desktop Firefox'] } },
  { name: 'webkit', use: { ...devices['Desktop Safari'] } },
]

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
  },
  projects: ALL_BROWSERS ? browsers : browsers.slice(0, 1),
  webServer: {
    command: 'uv run --project ../backend python -m omnisync serve',
    url: `http://127.0.0.1:${PORT}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      OMNISYNC_DATA_DIR: DATA_DIR,
      OMNISYNC_PORT: String(PORT),
      OMNISYNC_STATIC_DIR: path.resolve(import.meta.dirname, 'dist'),
      OMNISYNC_INSTANCE_NAME: 'e2e-pc',
    },
  },
})
