/** One toaster for the app: action feedback lands here ("taught Sara a
 * rule"), errors in red, optional undo on the message. */
import { useCallback, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { ToastCtx } from './toastContext'
import type { ToastApi } from './toastContext'

interface ToastMsg {
  id: number
  text: string
  detail?: string
  kind: 'ok' | 'err'
  undo?: () => void
}

export function ToastProvider(props: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastMsg[]>([])
  const nextId = useRef(1)

  const show = useCallback<ToastApi['show']>((text, opts) => {
    const id = nextId.current++
    setToasts((ts) => [...ts, { id, text, detail: opts?.detail, kind: opts?.kind ?? 'ok', undo: opts?.undo }])
    window.setTimeout(() => {
      setToasts((ts) => ts.filter((t) => t.id !== id))
    }, opts?.undo ? 8000 : 4600)
  }, [])

  const api = useMemo<ToastApi>(() => ({ show }), [show])
  return (
    <ToastCtx.Provider value={api}>
      {props.children}
      <div className="toaster" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast${t.kind === 'err' ? ' err' : ''}`}>
            <span>{t.text}</span>
            {t.detail && <span className="tsec">{t.detail}</span>}
            {t.undo && (
              <button
                onClick={() => {
                  t.undo?.()
                  setToasts((ts) => ts.filter((x) => x.id !== t.id))
                }}
              >
                Undo
              </button>
            )}
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  )
}
