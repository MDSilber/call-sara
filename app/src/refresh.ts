/** Close the loop after a ledger-changing action: the server regenerates
 * summary.json + analytics.duckdb in the background; this poller watches
 * /api/freshness until the DB stamp moves (or the regen flag drops), then
 * invalidates every cached room so fresh numbers land on their own. */
import { api } from './api'
import { invalidate } from './useFetch'

const POLL_MS = 2500
const MAX_MS = 120_000

let watching = false
const subs = new Set<() => void>()

export function isRefreshing(): boolean {
  return watching
}

export function onRefreshChange(fn: () => void): () => void {
  subs.add(fn)
  return () => subs.delete(fn)
}

function flag(value: boolean): void {
  watching = value
  subs.forEach((fn) => fn())
}

export function watchRegeneration(): void {
  if (watching) return
  flag(true)
  const started = Date.now()
  let baseline: string | null = null
  const tick = () => {
    api.freshness()
      .then((f) => {
        if (baseline === null) baseline = f.db_built_at
        const moved = f.db_built_at !== baseline
        const settled = moved || (!f.regen.running && f.regen.last_ok !== null)
        if (settled && !f.regen.running) {
          flag(false)
          invalidate()
          return
        }
        if (Date.now() - started > MAX_MS) {
          flag(false)
          invalidate()
          return
        }
        window.setTimeout(tick, POLL_MS)
      })
      .catch(() => {
        if (Date.now() - started > MAX_MS) {
          flag(false)
          return
        }
        window.setTimeout(tick, POLL_MS)
      })
  }
  window.setTimeout(tick, POLL_MS)
}
