/**
 * The per-request CSP nonce rendered by the server into <meta property="csp-nonce"> (C-22).
 * Components that render their own <style> elements (Radix Select viewport, scroll-lock via
 * get-nonce) must pass it, or the browser blocks their styles. Undefined in dev/tests.
 */
let cached: string | null | undefined

export function getCspNonce(): string | undefined {
  if (cached === undefined) {
    const value = document.querySelector('meta[property="csp-nonce"]')?.getAttribute('content') ?? null
    cached = value && !value.startsWith('__') ? value : null
  }
  return cached ?? undefined
}
