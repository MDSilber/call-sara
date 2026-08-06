/** Theme: auto → light → dark cycle, mirroring Sara Home's toggle.
 * The choice is stamped on <html data-theme> (CSS wins from there) and
 * persisted; charts re-read CSS variables whenever the epoch bumps. */
import { useSyncExternalStore } from 'react'

export type ThemeMode = 'auto' | 'light' | 'dark'
const KEY = 'sara-theme'
const ORDER: ThemeMode[] = ['auto', 'light', 'dark']

let epoch = 0
const subs = new Set<() => void>()

function stored(): ThemeMode {
  const v = localStorage.getItem(KEY)
  return v === 'light' || v === 'dark' ? v : 'auto'
}

export function applyTheme(mode: ThemeMode): void {
  if (mode === 'auto') {
    delete document.documentElement.dataset.theme
    localStorage.removeItem(KEY)
  } else {
    document.documentElement.dataset.theme = mode
    localStorage.setItem(KEY, mode)
  }
  epoch += 1
  subs.forEach((fn) => fn())
}

export function initTheme(): void {
  applyTheme(stored())
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
    if (stored() === 'auto') {
      epoch += 1
      subs.forEach((fn) => fn())
    }
  })
}

export function cycleTheme(): ThemeMode {
  const next = ORDER[(ORDER.indexOf(stored()) + 1) % ORDER.length] ?? 'auto'
  applyTheme(next)
  return next
}

export function themeLabel(): string {
  const mode = stored()
  return mode === 'auto' ? 'Auto theme' : mode === 'light' ? 'Light' : 'Dark'
}

/** Re-render subscribers when the effective theme changes. */
export function useThemeEpoch(): number {
  return useSyncExternalStore(
    (fn) => {
      subs.add(fn)
      return () => subs.delete(fn)
    },
    () => epoch,
  )
}

/** Read a CSS custom property at call time (charts pull live tokens). */
export function cssv(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}
