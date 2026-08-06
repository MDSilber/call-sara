/** Tiny data hook: load once per key, expose reload for post-action
 * refreshes. Results are cached per key for the page's lifetime so tab
 * flips are instant; any write action calls invalidate() to re-pull. */
import { useCallback, useEffect, useRef, useState } from 'react'

const cache = new Map<string, unknown>()
const listeners = new Set<() => void>()

export function invalidate(...keys: string[]): void {
  if (keys.length === 0) cache.clear()
  else keys.forEach((k) => cache.delete(k))
  listeners.forEach((fn) => fn())
}

export interface Fetched<T> {
  data: T | null
  error: string | null
  loading: boolean
  reload: () => void
}

export function useFetch<T>(key: string, fn: () => Promise<T>): Fetched<T> {
  const [data, setData] = useState<T | null>((cache.get(key) as T) ?? null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(!cache.has(key))
  const fnRef = useRef(fn)
  fnRef.current = fn

  const load = useCallback(() => {
    let alive = true
    if (!cache.has(key)) setLoading(true)
    setError(null)
    fnRef.current()
      .then((d) => {
        if (!alive) return
        cache.set(key, d)
        setData(d)
        setLoading(false)
      })
      .catch((e: unknown) => {
        if (!alive) return
        setError(e instanceof Error ? e.message : String(e))
        setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [key])

  useEffect(() => {
    if (cache.has(key)) {
      setData(cache.get(key) as T)
      setLoading(false)
      return
    }
    return load()
  }, [key, load])

  useEffect(() => {
    const onInvalidate = () => {
      if (!cache.has(key)) load()
    }
    listeners.add(onInvalidate)
    return () => {
      listeners.delete(onInvalidate)
    }
  }, [key, load])

  return { data, error, loading, reload: load }
}
