/**
 * Recomputes every WCAG contrast ratio in docs/08-UX-DESIGN.md section 5.1 from tokens.css
 * (ADR-0013 follow-up). A token change that breaks AA fails the build.
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'

const css = readFileSync(path.resolve(import.meta.dirname, 'tokens.css'), 'utf8')

function block(start: RegExp, end?: string): Record<string, string> {
  const startIndex = css.search(start)
  if (startIndex < 0) throw new Error(`block ${start} not found`)
  const stopIndex = end ? css.indexOf(end, startIndex) : css.indexOf('}', startIndex)
  const body = css.slice(startIndex, stopIndex)
  const vars: Record<string, string> = {}
  for (const match of body.matchAll(/--([a-z-]+):\s*(#[0-9a-f]{6})\s*;/gi)) {
    vars[match[1]!] = match[2]!.toLowerCase()
  }
  return vars
}

const light = block(/:root\s*\{/)
const dark = block(/\/\* DARK-START \*\//, '/* DARK-END */')
const systemDark = block(/\/\* SYSTEM-DARK-START \*\//, '/* SYSTEM-DARK-END */')

function luminance(hex: string): number {
  const channel = (i: number) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x) as [number, number]
  return (hi + 0.05) / (lo + 0.05)
}

const TEXT = 4.5
const UI = 3

type Pair = [foreground: string, surface: string, minimum: number]

// Foreground tokens on every surface they are used on (08-UX-DESIGN 5.1).
const PAIRS: Pair[] = [
  ...['foreground', 'muted-foreground', 'link', 'success', 'warning', 'danger', 'info'].flatMap((fg): Pair[] => [
    [fg, 'background', TEXT],
    [fg, 'card', TEXT],
    [fg, 'muted', TEXT],
  ]),
  ['ring', 'background', UI],
  ['ring', 'card', UI],
  ['ring', 'muted', UI],
  ['input', 'background', UI],
  ['input', 'card', UI],
  ['input', 'muted', UI],
  ['primary-foreground', 'primary', TEXT],
  ['destructive-foreground', 'destructive', TEXT],
  ['secondary-foreground', 'secondary', TEXT],
  ['accent-foreground', 'accent', TEXT],
  ['card-foreground', 'card', TEXT],
  ['popover-foreground', 'popover', TEXT],
  ['sidebar-foreground', 'sidebar', TEXT],
  ['sidebar-primary-foreground', 'sidebar-primary', TEXT],
  ['sidebar-accent-foreground', 'sidebar-accent', TEXT],
]

describe('colour tokens', () => {
  it('define the same variables in light and both dark blocks', () => {
    expect(Object.keys(dark).sort()).toEqual(Object.keys(light).sort())
    expect(systemDark).toEqual(dark)
  })

  for (const [mode, tokens] of [
    ['light', light],
    ['dark', dark],
  ] as const) {
    it.each(PAIRS)(`${mode}: %s on %s >= %s:1`, (fg, surface, minimum) => {
      const a = tokens[fg]
      const b = tokens[surface]
      expect(a, `missing --${fg}`).toBeDefined()
      expect(b, `missing --${surface}`).toBeDefined()
      expect(contrast(a!, b!)).toBeGreaterThanOrEqual(minimum)
    })
  }

  it('matches the documented ratios for the accent colour', () => {
    expect(contrast(light['primary']!, light['primary-foreground']!)).toBeCloseTo(6.29, 2)
    expect(contrast(dark['primary']!, dark['primary-foreground']!)).toBeCloseTo(6.67, 2)
    expect(light['primary']).toBe('#4f46e5')
  })
})
