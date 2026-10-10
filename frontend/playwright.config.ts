import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { defineConfig, devices, type PlaywrightTestConfig } from '@playwright/test'

/**
 * End-to-end tests against the real server (`omnisync serve`) serving the production build.
 * Every browser project gets its own server and throw-away data directory, so each one starts
 * from a genuine first run. CI runs Chromium, Firefox and WebKit (09-TESTING-STRATEGY 2);
 * locally only Chromium runs by default because Windows application control on the owner's PC
 * blocks the Playwright Firefox build and WebKit lacks Windows host dependencies (ADR-0014).
 */
const ALL_BROWSERS = Boolean(process.env.CI) || process.env.E2E_ALL_BROWSERS === '1'
const STATIC_DIR = path.resolve(import.meta.dirname, 'dist')

const BROWSERS = [
  { name: 'chromium', device: devices['Desktop Chrome'], port: 8766 },
  { name: 'firefox', device: devices['Desktop Firefox'], port: 8767 },
  { name: 'webkit', device: devices['Desktop Safari'], port: 8768 },
].slice(0, ALL_BROWSERS ? 3 : 1)

// The config is evaluated in the runner and in each worker; the data directories are created
// once in the runner and handed to the workers through the environment.
function dataDir(name: string): string {
  const key = `OMNISYNC_E2E_DATA_DIR_${name.toUpperCase()}`
  process.env[key] ??= mkdtempSync(path.join(tmpdir(), `omnisync-e2e-${name}-`))
  return process.env[key]
}

const webServer: PlaywrightTestConfig['webServer'] = BROWSERS.map((b) => ({
  command: 'uv run --project ../backend python -m omnisync serve',
  url: `http://127.0.0.1:${b.port}/api/v1/health`,
  reuseExistingServer: false,
  timeout: 120_000,
  env: {
    OMNISYNC_DATA_DIR: dataDir(b.name),
    OMNISYNC_PORT: String(b.port),
    OMNISYNC_STATIC_DIR: STATIC_DIR,
    OMNISYNC_INSTANCE_NAME: 'e2e-pc',
  },
}))

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: { trace: 'retain-on-failure' },
  projects: BROWSERS.map((b) => ({
    name: b.name,
    use: { ...b.device, baseURL: `http://127.0.0.1:${b.port}` },
    metadata: { dataDir: dataDir(b.name) },
  })),
  webServer,
})
