/** The register: one account, statement-style — date, payee, counter
 * account, amount, and the RUNNING BALANCE the DB computed. Reached from
 * the money map, the accounts list, or the palette; never a nav tab. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Card, CardSkeleton, LoadError } from '../components/ui'
import type { Register } from '../types'

export function RegisterRoom(props: { account: string; onBack: () => void }) {
  const [data, setData] = useState<Register | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loadingMore, setLoadingMore] = useState(false)

  const load = useCallback(() => {
    setData(null)
    setError(null)
    api.register(props.account)
      .then(setData)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
  }, [props.account])

  useEffect(() => {
    load()
  }, [load])

  const more = () => {
    if (!data?.cursor || loadingMore) return
    setLoadingMore(true)
    api.register(props.account, data.cursor)
      .then((page) => {
        setData((d) => d ? { ...d, rows: [...d.rows, ...page.rows], cursor: page.cursor } : page)
        setLoadingMore(false)
      })
      .catch(() => setLoadingMore(false))
  }

  if (error) return <LoadError error={error} retry={load} />
  if (!data) return <div className="grid g-solo"><CardSkeleton lines={10} /></div>

  const leaf = props.account.split(':').slice(2).join(' · ') || props.account
  if (!data.found) {
    return (
      <div className="grid g-solo">
        <Card k="Register">
          <button className="regback" onClick={props.onBack}>← back</button>
          <p className="empty"><b>No account named <code>{props.account}</code>.</b> Pick one from the money map or the palette.</p>
        </Card>
      </div>
    )
  }
  return (
    <div className="grid g-solo">
      <Card>
        <div className="reghead">
          <button className="regback" onClick={props.onBack}>← back</button>
          <span className="regtitle">{leaf}</span>
          {data.owner && <span className="ownerchip">{data.owner}</span>}
          {data.is_open === false && <span className="chip">closed</span>}
          <span className="regbal num">{data.balance}</span>
        </div>
        <p className="regmeta">
          {props.account}
          {data.institution ? ` · ${data.institution}` : ''}
          {data.opened ? ` · opened ${data.opened}` : ''}
          {typeof data.postings === 'number' ? ` · ${data.postings.toLocaleString()} postings` : ''}
        </p>
        {data.balances && data.balances.length > 1 && (
          <div className="chiprow">
            {data.balances.map((b) => (
              <span key={b.currency} className="chip num">
                <b>{b.units}</b> {b.currency}{b.value ? ` · ${b.value}` : ''}
              </span>
            ))}
          </div>
        )}
        {data.rows.length === 0 ? (
          <p className="empty"><b>No postings yet.</b> The statement fills in with the first import.</p>
        ) : (
          <table className="regtable">
            <thead>
              <tr>
                <th>Date</th><th>Payee</th><th className="rother">Counter account</th>
                <th className="r">Amount</th><th className="r">Balance</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.id}>
                  <td className="rdate">{r.day}</td>
                  <td>
                    <span className="rp">{r.payee}</span>
                    {r.narration && <div className="rn">{r.narration}</div>}
                  </td>
                  <td className="rother">{counterLabel(r.other)}</td>
                  <td className={`r num ramt${r.neg ? ' neg' : ''}`}>{r.amt}</td>
                  <td className="r num rbal">{r.balance}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {data.cursor && (
          <button className="btn loadmore" onClick={more} disabled={loadingMore}>
            {loadingMore ? 'Loading…' : 'Earlier postings'}
          </button>
        )}
      </Card>
    </div>
  )
}

/** 'Income:US:Interest' -> 'Interest' — the 2-letter country segment is
 * plumbing, same rule the category chips apply. */
function counterLabel(account: string): string {
  const segs = account.split(':').slice(1)
  const trimmed = segs[0] && /^[A-Z]{2}$/.test(segs[0]) ? segs.slice(1) : segs
  return trimmed.join(' · ') || account
}
