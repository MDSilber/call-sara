/** The ⌘K palette: rooms, accounts (→ register), live transaction search
 * (→ activity, pre-filtered). One input, arrow keys, Enter, Esc. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { AccountRow, ActivityRow } from '../types'

export interface PaletteRoom {
  id: string
  label: string
}

interface Hit {
  key: string
  group: 'Rooms' | 'Accounts' | 'Transactions'
  title: string
  sub?: string
  value?: string
  go: () => void
}

const DEBOUNCE_MS = 160

export function Palette(props: {
  rooms: PaletteRoom[]
  onClose: () => void
  onGo: (hash: string) => void
}) {
  const { rooms, onClose, onGo } = props
  const [q, setQ] = useState('')
  const [accounts, setAccounts] = useState<AccountRow[]>([])
  const [txns, setTxns] = useState<ActivityRow[]>([])
  const [cursor, setCursor] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const timer = useRef(0)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    window.clearTimeout(timer.current)
    const needle = q.trim()
    if (!needle) {
      setAccounts([])
      setTxns([])
      return
    }
    timer.current = window.setTimeout(() => {
      api.search(needle)
        .then((r) => {
          setAccounts(r.accounts)
          setTxns(r.txns)
        })
        .catch(() => {
          setAccounts([])
          setTxns([])
        })
    }, DEBOUNCE_MS)
    return () => window.clearTimeout(timer.current)
  }, [q])

  const hits = useMemo<Hit[]>(() => {
    const needle = q.trim().toLowerCase()
    const roomHits: Hit[] = rooms
      .filter((r) => !needle || r.label.toLowerCase().includes(needle))
      .map((r) => ({
        key: `room:${r.id}`,
        group: 'Rooms' as const,
        title: r.label,
        go: () => onGo(`#${r.id}`),
      }))
    const acctHits: Hit[] = accounts.map((a) => ({
      key: `acct:${a.account}`,
      group: 'Accounts' as const,
      title: a.account.split(':').slice(2).join(' · ') || a.account,
      sub: `${a.account}${a.owner ? ` · ${a.owner}` : ''}${a.is_open ? '' : ' · closed'}`,
      value: a.balance,
      go: () => onGo(`#register?account=${encodeURIComponent(a.account)}`),
    }))
    const txnHits: Hit[] = txns.map((t) => ({
      key: `txn:${t.id}`,
      group: 'Transactions' as const,
      title: t.payee,
      sub: `${t.day} · ${t.category}${t.narration ? ` · ${t.narration}` : ''}`,
      value: t.amt,
      go: () => onGo(`#activity?q=${encodeURIComponent(t.payee)}`),
    }))
    return [...roomHits, ...acctHits, ...txnHits]
  }, [q, rooms, accounts, txns, onGo])

  useEffect(() => {
    setCursor(0)
  }, [q, hits.length])

  const onKey = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        setCursor((c) => Math.min(c + 1, hits.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setCursor((c) => Math.max(c - 1, 0))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        hits[cursor]?.go()
      }
    },
    [hits, cursor, onClose],
  )

  let lastGroup = ''
  return (
    <>
      <div className="palette-scrim" onClick={onClose} />
      <div className="palette" role="dialog" aria-modal="true" aria-label="command palette"
        onKeyDown={onKey}>
        <div className="pinput">
          <SearchIcon />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Jump to a room, an account, or a transaction…"
            aria-label="palette search"
            spellCheck={false}
          />
          <kbd>esc</kbd>
        </div>
        <div className="plist" role="listbox">
          {hits.map((h, i) => {
            const head = h.group !== lastGroup ? <div className="pgroup ck">{h.group}</div> : null
            lastGroup = h.group
            return (
              <div key={h.key}>
                {head}
                <button
                  className="prow"
                  role="option"
                  aria-selected={i === cursor}
                  onMouseEnter={() => setCursor(i)}
                  onClick={h.go}
                >
                  <span className="pmain">
                    <span className="pt">{h.title}</span>
                    {h.sub && <span className="ps">{h.sub}</span>}
                  </span>
                  {h.value && <span className="pv num">{h.value}</span>}
                </button>
              </div>
            )
          })}
          {hits.length === 0 && (
            <p className="pempty">Nothing matches — try a payee, an account name, or a room.</p>
          )}
        </div>
        <div className="pfoot">
          <span><kbd>↑</kbd> <kbd>↓</kbd> move</span>
          <span><kbd>↵</kbd> open</span>
          <span><kbd>esc</kbd> close</span>
        </div>
      </div>
    </>
  )
}

function SearchIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.8-3.8" />
    </svg>
  )
}
