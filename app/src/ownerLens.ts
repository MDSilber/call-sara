/** The owner lens: one household-wide toggle (All / per person / Joint),
 * persisted in localStorage, read anywhere via useSyncExternalStore. The
 * lens value rides API calls as `owner`; "all" sends nothing. */
import { useSyncExternalStore } from 'react'

const KEY = 'sara-owner'
const subs = new Set<() => void>()
let current: string = readStored()

function readStored(): string {
  try {
    return localStorage.getItem(KEY) ?? 'all'
  } catch {
    return 'all'
  }
}

export function setOwner(owner: string): void {
  current = owner || 'all'
  try {
    if (current === 'all') localStorage.removeItem(KEY)
    else localStorage.setItem(KEY, current)
  } catch {
    /* storage may be unavailable; the in-memory lens still works */
  }
  subs.forEach((fn) => fn())
}

export function useOwner(): string {
  return useSyncExternalStore(
    (fn) => {
      subs.add(fn)
      return () => subs.delete(fn)
    },
    () => current,
  )
}

export function ownerLabel(owner: string): string {
  return owner === 'all' ? 'All' : owner.charAt(0).toUpperCase() + owner.slice(1)
}
