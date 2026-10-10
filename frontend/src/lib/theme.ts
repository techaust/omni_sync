/**
 * Colour mode and density (FR-UX-04, docs/08-UX-DESIGN.md section 5.2).
 *
 * First paint: the server writes `class="light"` / `class="dark"` on <html> from the user's
 * stored preference; `system` renders no class and CSS follows the OS. These helpers keep the
 * class in sync when the preference changes in the browser, without a reload.
 */
import { useSyncExternalStore } from 'react'

export type ColorMode = 'system' | 'light' | 'dark'
export type Density = 'compact' | 'comfortable'

export function applyColorMode(mode: ColorMode, root: HTMLElement = document.documentElement): void {
  root.classList.remove('light', 'dark')
  if (mode !== 'system') root.classList.add(mode)
}

export function applyDensity(density: Density, root: HTMLElement = document.documentElement): void {
  root.dataset.density = density
}

const DARK_QUERY = '(prefers-color-scheme: dark)'

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia(DARK_QUERY)
  media.addEventListener('change', onChange)
  return () => media.removeEventListener('change', onChange)
}

/** The theme actually shown: resolves `system` through the OS setting. */
export function useResolvedTheme(mode: ColorMode): 'light' | 'dark' {
  const systemDark = useSyncExternalStore(
    subscribe,
    () => window.matchMedia(DARK_QUERY).matches,
    () => false,
  )
  if (mode === 'system') return systemDark ? 'dark' : 'light'
  return mode
}
