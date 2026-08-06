/** The toast context + hook, split from the provider so fast refresh
 * keeps working (component files export only components). */
import { createContext, useContext } from 'react'

export interface ToastApi {
  show: (text: string, opts?: { detail?: string; kind?: 'ok' | 'err'; undo?: () => void }) => void
}

export const ToastCtx = createContext<ToastApi>({ show: () => undefined })

export function useToast(): ToastApi {
  return useContext(ToastCtx)
}
