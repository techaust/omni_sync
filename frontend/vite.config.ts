/// <reference types="vitest/config" />
import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/**
 * Sonner injects its CSS through an inline <style> element without a CSP nonce, which our
 * policy (C-22) blocks. We load sonner/dist/styles.css as a normal stylesheet instead (see
 * src/index.css) and turn the runtime injection into a no-op. If a Sonner upgrade changes the
 * injector, the build fails here and e2e/milestone3.spec.ts reports the CSP violation.
 */
function sonnerWithoutInlineStyles(): Plugin {
  const INJECTOR = 'function __insertCSS(code) {'
  return {
    name: 'omnisync:sonner-without-inline-styles',
    enforce: 'pre',
    transform(code, id) {
      if (!/[\\/]sonner[\\/]dist[\\/]index\.m?js$/.test(id)) return null
      if (!code.includes(INJECTOR)) {
        this.error('sonner no longer defines __insertCSS; review the CSP workaround in vite.config.ts')
      }
      return { code: code.replace(INJECTOR, `${INJECTOR} return;`), map: null }
    },
  }
}

// The backend only accepts Host/Origin 127.0.0.1:<port> or localhost:<port> (C-04, C-03).
// The dev proxy therefore rewrites both, so `npm run dev` works against `omnisync serve`.
const BACKEND = process.env.OMNISYNC_BACKEND ?? 'http://127.0.0.1:8765'

export default defineConfig({
  plugins: [sonnerWithoutInlineStyles(), react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, 'src') },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
        headers: { origin: BACKEND },
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    // Never inline assets as data: URIs; fonts must be same-origin files for font-src 'self' (C-22).
    assetsInlineLimit: 0,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: false,
    coverage: {
      provider: 'v8',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.test.{ts,tsx}', 'src/test/**', 'src/api/schema.d.ts', 'src/components/ui/**'],
      thresholds: { lines: 70 },
    },
  },
})
