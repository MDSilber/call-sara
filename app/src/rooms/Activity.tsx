/** The Activity room: the transaction feed grouped by day, category chips,
 * and the teach-a-rule picker on uncategorized rows — choose a category,
 * Sara learns a rules.toml rule and (optionally) recategorizes history
 * through the same gated tool a session would run. */
import { useMemo, useState } from 'react'
import { api } from '../api'
import { useToast } from '../components/toastContext'
import { Card, CardSkeleton, Empty, LoadError } from '../components/ui'
import type { Activity, ActivityRow } from '../types'
import { invalidate, useFetch } from '../useFetch'

export function ActivityRoom() {
  const [month, setMonth] = useState<string | null>(null)
  const key = `activity:${month ?? 'latest'}`
  const { data, error, loading, reload } = useFetch(key, () => api.activity(month ?? undefined))
  if (loading) return <div className="grid g-solo"><CardSkeleton lines={9} /></div>
  if (error) return <LoadError error={error} retry={reload} />
  if (!data) return null
  if (!data.month) {
    return (
      <div className="grid g-solo">
        <Card k="Activity">
          <Empty><b>No transactions yet.</b> Import a statement and the feed starts here.</Empty>
        </Card>
      </div>
    )
  }
  return <Feed data={data} month={data.month} onMonth={setMonth} />
}

function Feed(props: { data: Activity; month: string; onMonth: (m: string) => void }) {
  const { data } = props
  const uncatHere = data.uncategorized_month ?? 0
  return (
    <div className="grid g-solo">
      <Card
        k="Activity"
        sub={uncatHere > 0
          ? `${uncatHere} row${uncatHere === 1 ? '' : 's'} here still need a category — click their chip and teach Sara the rule.`
          : 'Every booked transaction, newest first. Chips are their categories.'}
      >
        <div className="feedbar">
          <select
            className="monthsel"
            value={props.month}
            onChange={(e) => props.onMonth(e.target.value)}
            aria-label="month"
          >
            {data.months.map((m) => (
              <option key={m.ym} value={m.ym}>{m.label}</option>
            ))}
          </select>
          {data.totals && (
            <span className="feedtotals">
              <b>{data.totals.spent}</b> out · <b>{data.totals.received}</b> in · {data.totals.window}
            </span>
          )}
        </div>
        {data.days.map((day) => (
          <div key={day.date}>
            <div className="dayhead">{day.label}</div>
            <ul className="feed">
              {day.rows.map((row, i) => (
                <FeedRow key={`${day.date}:${i}`} row={row} categories={data.categories} />
              ))}
            </ul>
          </div>
        ))}
        {data.days.length === 0 && <Empty>Nothing booked this month.</Empty>}
      </Card>
    </div>
  )
}

function FeedRow(props: { row: ActivityRow; categories: Activity['categories'] }) {
  const { row } = props
  const [open, setOpen] = useState(false)
  return (
    <>
      <li>
        <div className="fp">
          <div className="payee">{row.payee}</div>
          {row.narration && <div className="fnote">{row.narration}</div>}
        </div>
        {row.kind === 'uncategorized' ? (
          <button className="catchip uncat" onClick={() => setOpen((o) => !o)}
            aria-expanded={open}>
            uncategorized — teach Sara
          </button>
        ) : (
          <span className="catchip">{row.category}</span>
        )}
        <span className={`famt num${row.kind === 'income' ? ' in' : ''}`}>{row.amt}</span>
      </li>
      {open && <TeachRule row={row} categories={props.categories} done={() => setOpen(false)} />}
    </>
  )
}

function TeachRule(props: {
  row: ActivityRow
  categories: Activity['categories']
  done: () => void
}) {
  const toast = useToast()
  const [pattern, setPattern] = useState(defaultPattern(props.row.payee))
  const [account, setAccount] = useState('')
  const [applyHistory, setApplyHistory] = useState(true)
  const [busy, setBusy] = useState(false)
  const expenses = useMemo(
    () => props.categories.filter((c) => c.account.startsWith('Expenses:')),
    [props.categories],
  )
  const income = useMemo(
    () => props.categories.filter((c) => c.account.startsWith('Income:')),
    [props.categories],
  )

  const teach = () => {
    if (!account || busy) return
    setBusy(true)
    api.categorize(pattern, account, applyHistory)
      .then((res) => {
        toast.show('Taught Sara a rule', {
          detail: `${res.rule.match} → ${res.rule.account}` +
            (res.applied ? ` · ${res.changed} past transaction${res.changed === 1 ? '' : 's'} recategorized` : ''),
        })
        invalidate() // every room's numbers may have moved
        props.done()
      })
      .catch((e: unknown) => {
        toast.show('Rule refused', {
          kind: 'err',
          detail: e instanceof Error ? e.message : String(e),
        })
        setBusy(false)
      })
  }

  return (
    <li>
      <div className="teach" style={{ width: '100%' }}>
        <div className="tlabel">Teach Sara: transactions matching…</div>
        <div className="teachrow">
          <input
            type="text"
            value={pattern}
            onChange={(e) => setPattern(e.target.value)}
            aria-label="payee pattern"
            spellCheck={false}
          />
          <select value={account} onChange={(e) => setAccount(e.target.value)}
            aria-label="category">
            <option value="">…belong in</option>
            {expenses.map((c) => (
              <option key={c.account} value={c.account}>{c.label}</option>
            ))}
            {income.length > 0 && (
              <optgroup label="Income">
                {income.map((c) => (
                  <option key={c.account} value={c.account}>{c.label}</option>
                ))}
              </optgroup>
            )}
          </select>
          <label className="apply">
            <input
              type="checkbox"
              checked={applyHistory}
              onChange={(e) => setApplyHistory(e.target.checked)}
            />
            fix history too
          </label>
          <button className="btn primary" disabled={!account || busy} onClick={teach}>
            {busy ? 'Teaching…' : 'Teach the rule'}
          </button>
          <button className="btn quiet" onClick={props.done}>Cancel</button>
        </div>
      </div>
    </li>
  )
}

/** Statement payees carry store numbers; the suggested pattern drops the
 * trailing digits so one rule covers the chain, not one register. */
function defaultPattern(payee: string): string {
  const trimmed = payee.replace(/[#*]?\s*\d{2,}\s*$/, '').trim()
  const base = trimmed.length >= 4 ? trimmed : payee.trim()
  return base.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}
