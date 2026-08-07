/** The Activity room v2: server-side search over the whole ledger, filter
 * chips, keyset infinite scroll, provenance chips, and BULK TEACH — select
 * same-merchant rows, teach one rule, watch every room refresh itself.
 * Money strings arrive formatted; this file never does money math. */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useToast } from '../components/toastContext'
import { Card, CardSkeleton, LoadError } from '../components/ui'
import { watchRegeneration } from '../refresh'
import type { ActivityFilters, ActivityPage, ActivityRow, Category, OwnerDef } from '../types'
import { invalidate } from '../useFetch'

const AMOUNT_DEBOUNCE = 350
const SEARCH_DEBOUNCE = 220

interface FeedState {
  rows: ActivityRow[]
  cursor: string | null
  matched: number
  totals: { spent: string; received: string } | null
  categories: Category[]
  owners: OwnerDef[]
  uncat: { count: number; amount: string } | null
  loading: boolean
  more: boolean
  error: string | null
}

const EMPTY_FEED: FeedState = {
  rows: [], cursor: null, matched: 0, totals: null, categories: [],
  owners: [], uncat: null, loading: true, more: false, error: null,
}

export function ActivityRoom(props: { initialQ?: string }) {
  // whose rows: room-local, starts at all owners every visit
  const [owner, setOwner] = useState('all')
  const [q, setQ] = useState(props.initialQ ?? '')
  const [debouncedQ, setDebouncedQ] = useState(q)
  const [uncatOnly, setUncatOnly] = useState(false)
  const [category, setCategory] = useState('')
  const [amountMin, setAmountMin] = useState('')
  const [amountMax, setAmountMax] = useState('')
  const [debouncedAmounts, setDebouncedAmounts] = useState<[string, string]>(['', ''])
  const [feed, setFeed] = useState<FeedState>(EMPTY_FEED)
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set())
  const [overrides, setOverrides] = useState<ReadonlyMap<number, string>>(new Map())
  const seq = useRef(0)

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQ(q), SEARCH_DEBOUNCE)
    return () => window.clearTimeout(t)
  }, [q])
  useEffect(() => {
    const t = window.setTimeout(
      () => setDebouncedAmounts([amountMin, amountMax]), AMOUNT_DEBOUNCE)
    return () => window.clearTimeout(t)
  }, [amountMin, amountMax])

  const filters = useMemo<ActivityFilters>(() => ({
    q: debouncedQ.trim() || undefined,
    uncategorized_only: uncatOnly || undefined,
    category: category || undefined,
    amount_min: debouncedAmounts[0] ? Number(debouncedAmounts[0]) : undefined,
    amount_max: debouncedAmounts[1] ? Number(debouncedAmounts[1]) : undefined,
  }), [debouncedQ, uncatOnly, category, debouncedAmounts])

  const load = useCallback((cursor: string | null) => {
    const my = ++seq.current
    setFeed((f) => cursor
      ? { ...f, loading: true }
      : { ...EMPTY_FEED, categories: f.categories, owners: f.owners })
    api.activity(filters, owner, cursor)
      .then((page: ActivityPage) => {
        if (my !== seq.current) return
        setFeed((f) => ({
          rows: cursor ? [...f.rows, ...page.rows] : page.rows,
          cursor: page.cursor,
          matched: page.matched ?? f.matched,
          totals: page.totals ?? f.totals,
          categories: page.categories ?? f.categories,
          owners: page.owners ?? f.owners,
          uncat: page.uncategorized ?? f.uncat,
          loading: false,
          more: page.cursor !== null,
          error: null,
        }))
      })
      .catch((e: unknown) => {
        if (my !== seq.current) return
        setFeed((f) => ({ ...f, loading: false,
          error: e instanceof Error ? e.message : String(e) }))
      })
  }, [filters, owner])

  useEffect(() => {
    setSelected(new Set())
    load(null)
  }, [load])

  // infinite scroll: a sentinel below the feed pulls the next keyset page
  const sentinel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = sentinel.current
    if (!el || !feed.more || feed.loading) return
    const io = new IntersectionObserver((entries) => {
      if (entries.some((en) => en.isIntersecting)) load(feed.cursor)
    }, { rootMargin: '600px' })
    io.observe(el)
    return () => io.disconnect()
  }, [feed.more, feed.loading, feed.cursor, load])

  // rules taught this visit: matched at render time so freshly loaded pages
  // inherit the optimistic category too (the DB catches up in the background)
  const [taughtRules, setTaughtRules] = useState<{ rx: RegExp | null; label: string }[]>([])
  const applyTaught = useCallback((pattern: string, label: string) => {
    let rx: RegExp | null = null
    try {
      rx = new RegExp(pattern, 'i')
    } catch {
      rx = null
    }
    setTaughtRules((rules) => [...rules, { rx, label }])
  }, [])

  const toggleRow = useCallback((id: number) => {
    setSelected((s) => {
      const next = new Set(s)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])

  const filtersOn = Boolean(filters.q || filters.uncategorized_only
    || filters.category || filters.amount_min !== undefined
    || filters.amount_max !== undefined) || owner !== 'all'

  if (feed.error && feed.rows.length === 0) {
    return <LoadError error={feed.error} retry={() => load(null)} />
  }
  if (feed.loading && feed.rows.length === 0 && !filtersOn) {
    return <div className="grid g-solo"><CardSkeleton lines={9} /></div>
  }

  const days = groupByDay(feed.rows)
  const uncatHere = feed.uncat?.count ?? 0

  return (
    <div className="grid g-solo">
      <Card
        k="Activity"
        sub={uncatHere > 0
          ? `${uncatHere} row${uncatHere === 1 ? '' : 's'} still need a category (${feed.uncat?.amount ?? ''}) — click a chip, or select a merchant's rows and teach one rule.`
          : 'Every booked transaction, searchable to the first import. Chips are categories; tiny dots say who categorized.'}
      >
        <div className="searchbar">
          <label className="searchbox">
            <SearchIcon />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Search payees and notes…"
              aria-label="search transactions"
              spellCheck={false}
            />
          </label>
          <button
            className="fchip"
            aria-pressed={uncatOnly}
            onClick={() => setUncatOnly((v) => !v)}
          >
            Uncategorized{uncatHere > 0 ? ` · ${uncatHere}` : ''}
          </button>
          <label className="fchip" aria-label="category filter">
            <select value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">Any category</option>
              {feed.categories.map((c) => (
                <option key={c.account} value={c.account}>{c.label}</option>
              ))}
            </select>
          </label>
          {feed.owners.length > 0 && (
            <label className="fchip" aria-label="owner filter">
              <select value={owner} onChange={(e) => setOwner(e.target.value)}>
                <option value="all">Everyone</option>
                {feed.owners.map((o) => (
                  <option key={o.owner} value={o.owner}>{o.label}</option>
                ))}
              </select>
            </label>
          )}
          <label className="fchip" aria-label="amount range">
            $<input type="number" min="0" placeholder="min" value={amountMin}
              onChange={(e) => setAmountMin(e.target.value)} aria-label="minimum amount" />
            –<input type="number" min="0" placeholder="max" value={amountMax}
              onChange={(e) => setAmountMax(e.target.value)} aria-label="maximum amount" />
          </label>
          <span className="feedcount num" aria-live="polite">
            {feed.totals
              ? <><b>{feed.matched.toLocaleString()}</b> rows · <b>{feed.totals.spent}</b> out · <b>{feed.totals.received}</b> in</>
              : '…'}
          </span>
        </div>

        {days.map((day) => (
          <div key={day.date}>
            <div className="dayhead">{day.label}</div>
            <ul className={`feed${selected.size > 0 ? ' selecting' : ''}`}>
              {day.rows.map((row) => (
                <FeedRow
                  key={row.id}
                  row={row}
                  override={overrides.get(row.id)
                    ?? (row.kind === 'uncategorized'
                      ? taughtRules.find((r) => r.rx?.test(row.payee))?.label
                      : undefined)}
                  categories={feed.categories}
                  selected={selected.has(row.id)}
                  onToggle={toggleRow}
                  anySelected={selected.size > 0}
                  onTaught={applyTaught}
                />
              ))}
            </ul>
          </div>
        ))}
        {feed.rows.length === 0 && !feed.loading && (
          <div className="emptyhero">
            <p className="eh1">{filtersOn ? 'Nothing matches those filters.' : 'No transactions yet.'}</p>
            <p className="eh2">
              {filtersOn
                ? 'Loosen the search or clear a chip — the whole ledger is searchable.'
                : 'Import a statement (Connections room) and the feed starts here.'}
            </p>
          </div>
        )}
        {feed.more && (
          <div ref={sentinel}>
            <button className="btn loadmore" onClick={() => load(feed.cursor)}
              disabled={feed.loading}>
              {feed.loading ? 'Loading…' : 'Load more'}
            </button>
          </div>
        )}
        {!feed.more && feed.rows.length > 0 && (
          <p className="feedend">
            {feed.rows.length === feed.matched
              ? 'That’s every matching row.'
              : `Showing ${feed.rows.length.toLocaleString()} of ${feed.matched.toLocaleString()}.`}
          </p>
        )}
        {selected.size > 0 && (
          <BulkTeach
            rows={feed.rows.filter((r) => selected.has(r.id))}
            categories={feed.categories}
            onDone={(ids, label) => {
              setOverrides((o) => {
                const next = new Map(o)
                ids.forEach((id) => next.set(id, label))
                return next
              })
              setSelected(new Set())
            }}
            onClear={() => setSelected(new Set())}
          />
        )}
      </Card>
    </div>
  )
}

function groupByDay(rows: ActivityRow[]): { date: string; label: string; rows: ActivityRow[] }[] {
  const out: { date: string; label: string; rows: ActivityRow[] }[] = []
  for (const row of rows) {
    const last = out[out.length - 1]
    if (last && last.date === row.date) last.rows.push(row)
    else out.push({ date: row.date, label: row.day, rows: [row] })
  }
  return out
}

function provenance(classifier: string): { cls: string; label: string } | null {
  if (!classifier) return null
  if (classifier === 'rule') return { cls: 'rule', label: 'rule' }
  if (classifier.startsWith('plaid')) return { cls: 'plaid', label: 'plaid' }
  return { cls: 'ai', label: 'sara' }
}

function FeedRow(props: {
  row: ActivityRow
  override: string | undefined
  categories: Category[]
  selected: boolean
  anySelected: boolean
  onToggle: (id: number) => void
  onTaught: (pattern: string, label: string) => void
}) {
  const { row, override, selected } = props
  const [open, setOpen] = useState(false)
  const prov = provenance(row.classifier)
  const taught = override !== undefined
  const category = override ?? row.category
  const uncat = row.kind === 'uncategorized' && !taught
  return (
    <>
      <li className={selected ? 'selected' : undefined}>
        <input
          type="checkbox"
          className="fcheck"
          checked={selected}
          onChange={() => props.onToggle(row.id)}
          aria-label={`select ${row.payee}`}
        />
        <div className="fp">
          <div className="payee">{row.payee}</div>
          {row.narration && <div className="fnote">{row.narration}</div>}
        </div>
        {row.owner && <span className="ownerchip">{row.owner}</span>}
        {prov && !uncat && (
          <span className={`provchip ${prov.cls}`}
            title={`categorized by ${row.classifier}`}>
            {prov.label}
          </span>
        )}
        {uncat ? (
          <button className="catchip uncat" onClick={() => setOpen((o) => !o)}
            aria-expanded={open}>
            uncategorized — teach Sara
          </button>
        ) : (
          <span className="catchip">{category}</span>
        )}
        <span className={`famt num${row.kind === 'income' ? ' in' : ''}`}>{row.amt}</span>
      </li>
      {open && (
        <TeachRule
          rows={[row]}
          categories={props.categories}
          done={() => setOpen(false)}
          onTaught={props.onTaught}
        />
      )}
    </>
  )
}

/** The sticky bar under a selection: same-merchant rows become ONE rule. */
function BulkTeach(props: {
  rows: ActivityRow[]
  categories: Category[]
  onDone: (ids: number[], label: string) => void
  onClear: () => void
}) {
  const toast = useToast()
  const [account, setAccount] = useState('')
  const [busy, setBusy] = useState(false)
  const patterns = useMemo(
    () => new Set(props.rows.map((r) => defaultPattern(r.payee))),
    [props.rows],
  )
  const oneMerchant = patterns.size === 1
  const pattern = oneMerchant ? [...patterns][0] ?? '' : ''

  const teach = () => {
    if (!account || !pattern || busy) return
    setBusy(true)
    const label = props.categories.find((c) => c.account === account)?.label ?? account
    api.categorize(pattern, account, true)
      .then((res) => {
        toast.show(`Taught Sara a rule for ${props.rows.length} row${props.rows.length === 1 ? '' : 's'}`, {
          detail: `${res.rule.match} → ${res.rule.account}` +
            (res.changed ? ` · ${res.changed} past transaction${res.changed === 1 ? '' : 's'} recategorized` : ''),
        })
        props.onDone(props.rows.map((r) => r.id), label)
        invalidate('glance', 'spend', 'autopilot')
        watchRegeneration()
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
    <div className="bulkbar" role="region" aria-label="bulk teach">
      <span className="bcount num">{props.rows.length} selected</span>
      {oneMerchant ? (
        <>
          <select value={account} onChange={(e) => setAccount(e.target.value)}
            aria-label="bulk category">
            <option value="">Every “{trim(pattern)}” belongs in…</option>
            {props.categories.map((c) => (
              <option key={c.account} value={c.account}>{c.label}</option>
            ))}
          </select>
          <button className="btn primary" disabled={!account || busy} onClick={teach}>
            {busy ? 'Teaching…' : 'Teach one rule'}
          </button>
        </>
      ) : (
        <span className="bcount" style={{ fontWeight: 400, opacity: 0.85 }}>
          pick rows from one merchant — a rule matches one payee pattern
        </span>
      )}
      <button className="quietlink" onClick={props.onClear}>clear</button>
    </div>
  )
}

function TeachRule(props: {
  rows: ActivityRow[]
  categories: Category[]
  done: () => void
  onTaught: (pattern: string, label: string) => void
}) {
  const toast = useToast()
  const first = props.rows[0]
  const [pattern, setPattern] = useState(first ? defaultPattern(first.payee) : '')
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
        const label = props.categories.find((c) => c.account === account)?.label ?? account
        props.onTaught(pattern, label)
        invalidate('glance', 'spend', 'autopilot')
        watchRegeneration()
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

function trim(pattern: string): string {
  return pattern.replace(/\\(.)/g, '$1')
}

function SearchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.2" strokeLinecap="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.8-3.8" />
    </svg>
  )
}
