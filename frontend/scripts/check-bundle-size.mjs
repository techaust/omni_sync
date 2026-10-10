// NFR-PERF-02: initial JavaScript must stay at or below 300 KB gzip.
// Measures every script referenced by dist/index.html (the initial load).
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { gzipSync } from 'node:zlib'

const BUDGET_KB = 300
const dist = path.resolve(import.meta.dirname, '..', 'dist')
const html = readFileSync(path.join(dist, 'index.html'), 'utf8')
const scripts = [...html.matchAll(/<script[^>]+src="([^"]+)"/g)].map((m) => m[1])
if (scripts.length === 0) throw new Error('no scripts found in dist/index.html; run the build first')

let totalKb = 0
for (const src of scripts) {
  const kb = gzipSync(readFileSync(path.join(dist, src))).length / 1024
  totalKb += kb
  console.log(`${src}: ${kb.toFixed(1)} KB gzip`)
}
console.log(`initial JS total: ${totalKb.toFixed(1)} KB gzip (budget ${BUDGET_KB} KB)`)
if (totalKb > BUDGET_KB) {
  console.error('bundle budget exceeded (NFR-PERF-02)')
  process.exit(1)
}
