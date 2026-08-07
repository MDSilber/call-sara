/** The Activity room v2: server-side search over the whole ledger, filter
 * chips, keyset infinite scroll, provenance chips, and BULK TEACH — select
 * same-merchant rows, teach one rule, watch every room refresh itself.
 * Money strings arrive formatted; this file never does money math. */
import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { useToast } from '../components/toastContext'
import { Card, LoadError, Skeleton } from '../components/ui'
import { watchRegeneration } from '../refresh'
import type { ActivityFilters, ActivityPage, ActivityRow, Category, FeedEntry, OwnerDef, Suggest, Suggestion, SweepGroup } from '../types'
import { invalidate } from '../useFetch'

const AMOUNT_DEBOUNCE = 350
const SEARCH_DEBOUNCE = 220

interface FeedState {
  rows: FeedEntry[]
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
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [selected, setSelected] = useState<ReadonlySet<number>>(new Set())
  const [overrides, setOverrides] = useState<ReadonlyMap<number, string>>(new Map())
  const seq = useRef(0)

  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQ(q), SEARCH_DEBOUNCE)
    return () => window.clearTimeout(t)
  }, [q])
  useEffect(() => {
    const t = window.setTimeout(
      () => setDebouncedAmounts((prev) =>
        prev[0] === amountMin && prev[1] === amountMax
          ? prev
          : [amountMin, amountMax]), AMOUNT_DEBOUNCE)
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
    setFeed((f) => cursor || f.rows.length
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

  // a just-opened category joins every picker at once; the regenerated DB
  // confirms it in the background
  const addCategory = useCallback((account: string) => {
    setFeed((f) => f.categories.some((c) => c.account === account) ? f : {
      ...f,
      categories: [...f.categories,
        { account, label: account.split(':').slice(1).join(' · ') }]
        .sort((a, b) => a.account.localeCompare(b.account)),
    })
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
  // first load renders skeleton lines INSIDE the same persistent Card below —
  // swapping whole cards would replay the room-entrance animation (a visible
  // double-jump the owner reported)
  const firstLoad = feed.loading && feed.rows.length === 0 && !filtersOn

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
          <button className="fchip fdisclose" aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen((v) => !v)}>
            Filters{filtersOn ? ' · on' : ''}
          </button>
          <div className={`fmore${filtersOpen ? ' open' : ''}`}>
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
          </div>
          <span className="feedcount num" aria-live="polite">
            {feed.totals
              ? <>{filtersOn ? 'matching · ' : 'all history · '}<b>{feed.matched.toLocaleString()}</b> rows · <b>{feed.totals.spent}</b> out · <b>{feed.totals.received}</b> in</>
              : '…'}
          </span>
        </div>

        {firstLoad ? (
          <div aria-hidden="true" className="feedin">
            {Array.from({ length: 9 }, (_, i) => (
              <Skeleton key={i} h={30} w={i % 3 ? '100%' : '72%'} />
            ))}
          </div>
        ) : null}
        {!firstLoad && days.map((day) => (
          <div key={day.date}>
            <div className="dayhead">{day.label}</div>
            <ul className={`feed${selected.size > 0 ? ' selecting' : ''}`}>
              {day.rows.map((entry) => isSweep(entry) ? (
                <SweepRow key={`sweep-${entry.id}`} group={entry} />
              ) : (
                <FeedRow
                  key={entry.id}
                  row={entry}
                  override={overrides.get(entry.id)
                    ?? (entry.kind === 'uncategorized'
                      ? taughtRules.find((r) => r.rx?.test(entry.payee))?.label
                      : undefined)}
                  categories={feed.categories}
                  selected={selected.has(entry.id)}
                  onToggle={toggleRow}
                  anySelected={selected.size > 0}
                  onTaught={applyTaught}
                  onNewCategory={addCategory}
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
            rows={feed.rows.filter((r): r is ActivityRow => !isSweep(r) && selected.has(r.id))}
            categories={feed.categories}
            onNewCategory={addCategory}
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

function groupByDay(rows: FeedEntry[]): { date: string; label: string; rows: FeedEntry[] }[] {
  const out: { date: string; label: string; rows: FeedEntry[] }[] = []
  for (const row of rows) {
    const last = out[out.length - 1]
    if (last && last.date === row.date) last.rows.push(row)
    else out.push({ date: row.date, label: row.day, rows: [row] })
  }
  return out
}

function isSweep(entry: FeedEntry): entry is SweepGroup {
  return 'kind' in entry && entry.kind === 'sweep'
}

/** A folded run of broker sweep noise: one quiet line, expandable. */
function SweepRow({ group }: { group: SweepGroup }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <li className="sweeprow">
        <button className="sweepbtn" onClick={() => setOpen((o) => !o)}
          aria-expanded={open}>
          <span className="sweeplbl">{group.label}</span>
          <span className="sweepn">{open ? 'fold' : 'show'}</span>
        </button>
        <span className="famt num in">{group.amt}</span>
      </li>
      {open && group.rows.map((row) => (
        <li key={row.id} className="sweepchild">
          <div className="fp">
            <div className="payee">{row.payee}</div>
            {row.narration && <div className="fnote">{row.narration}</div>}
          </div>
          <span className="catchip">{row.category}</span>
          <span className={`famt num${row.kind === 'income' ? ' in' : ''}`}>{row.amt}</span>
        </li>
      ))}
    </>
  )
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
  onNewCategory: (account: string) => void
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
          onNewCategory={props.onNewCategory}
        />
      )}
    </>
  )
}

/** The sticky bar under a selection: same-merchant rows become ONE rule.
 * One suggest call (the first row) preselects for the whole batch — same
 * guard: person/P2P batches show the line and wait for the owner's word. */
function BulkTeach(props: {
  rows: ActivityRow[]
  categories: Category[]
  onDone: (ids: number[], label: string) => void
  onClear: () => void
  onNewCategory: (account: string) => void
}) {
  const toast = useToast()
  const [account, setAccount] = useState('')
  const [newMode, setNewMode] = useState(false)
  const [newName, setNewName] = useState('')
  const [busy, setBusy] = useState(false)
  const [sug, setSug] = useState<Suggest | null>(null)
  const patterns = useMemo(
    () => new Set(props.rows.map((r) => defaultPattern(r.payee))),
    [props.rows],
  )
  const oneMerchant = patterns.size === 1
  const pattern = oneMerchant ? [...patterns][0] ?? '' : ''

  const first = oneMerchant ? props.rows[0] : undefined
  useEffect(() => {
    if (!first) {
      setSug(null)
      return
    }
    let alive = true
    suggestFor(first)
      .then((s) => {
        if (!alive) return
        setSug(s)
        if (s.suggestion?.preselect) {
          const suggested = s.suggestion.account
          setAccount((a) => (a === '' ? suggested : a))
        }
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [first])

  const newTarget = newMode ? newName.trim() : ''
  const ready = pattern !== '' && (newMode
    ? newTarget !== '' && accountHint(newTarget) === null
    : account !== '')

  const teach = () => {
    if (!ready || busy) return
    setBusy(true)
    api.categorize(pattern, newMode ? null : account, true,
      newMode ? newTarget : undefined)
      .then((res) => {
        toast.show(`Taught Sara a rule for ${props.rows.length} row${props.rows.length === 1 ? '' : 's'}`, {
          detail: `${res.rule.match} → ${res.rule.account}` +
            (res.changed ? ` · ${res.changed} past transaction${res.changed === 1 ? '' : 's'} recategorized` : ''),
        })
        suggestCache.clear()
        if (res.opened) props.onNewCategory(res.account)
        props.onDone(props.rows.map((r) => r.id), categoryLabel(res.account, props.categories))
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
          <CategoryPicker
            categories={props.categories}
            picked={account}
            newMode={newMode}
            newName={newName}
            placeholder={`Every “${trim(pattern)}” belongs in…`}
            ariaLabel="bulk category"
            onPick={setAccount}
            onNewMode={setNewMode}
            onNewName={setNewName}
          />
          {sug && <SaraLine s={sug} categories={props.categories} />}
          <button className="btn primary" disabled={!ready || busy} onClick={teach}>
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
  onNewCategory: (account: string) => void
}) {
  const toast = useToast()
  const first = props.rows[0]
  const [pattern, setPattern] = useState(first ? defaultPattern(first.payee) : '')
  const [account, setAccount] = useState('')
  const [newMode, setNewMode] = useState(false)
  const [newName, setNewName] = useState('')
  const [applyHistory, setApplyHistory] = useState(true)
  const [busy, setBusy] = useState(false)
  const [sug, setSug] = useState<Suggest | null>(null)

  // the popover opened instantly; the suggestion lands async and preselects
  // only while the picker is untouched (and never for person/P2P rows)
  useEffect(() => {
    if (!first) return
    let alive = true
    suggestFor(first)
      .then((s) => {
        if (!alive) return
        setSug(s)
        if (s.suggestion?.preselect) {
          const suggested = s.suggestion.account
          setAccount((a) => (a === '' ? suggested : a))
        }
      })
      .catch(() => undefined) // no suggestion is a fine suggestion
    return () => {
      alive = false
    }
  }, [first])

  const newTarget = newMode ? newName.trim() : ''
  const ready = newMode ? newTarget !== '' && accountHint(newTarget) === null
    : account !== ''

  const teach = () => {
    if (!ready || busy) return
    setBusy(true)
    api.categorize(pattern, newMode ? null : account, applyHistory,
      newMode ? newTarget : undefined)
      .then((res) => {
        toast.show(res.opened ? 'Opened a new category and taught Sara a rule'
          : 'Taught Sara a rule', {
          detail: `${res.rule.match} → ${res.rule.account}` +
            (res.applied ? ` · ${res.changed} past transaction${res.changed === 1 ? '' : 's'} recategorized` : ''),
        })
        suggestCache.clear() // the new rule outranks every cached verdict
        if (res.opened) props.onNewCategory(res.account)
        props.onTaught(pattern, categoryLabel(res.account, props.categories))
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
          <CategoryPicker
            categories={props.categories}
            picked={account}
            newMode={newMode}
            newName={newName}
            placeholder="…belong in"
            ariaLabel="category"
            onPick={setAccount}
            onNewMode={setNewMode}
            onNewName={setNewName}
          />
          <label className="apply">
            <input
              type="checkbox"
              checked={applyHistory}
              onChange={(e) => setApplyHistory(e.target.checked)}
            />
            fix history too
          </label>
          <button className="btn primary" disabled={!ready || busy} onClick={teach}>
            {busy ? 'Teaching…' : 'Teach the rule'}
          </button>
          <button className="btn quiet" onClick={props.done}>Cancel</button>
        </div>
        {sug && <SaraLine s={sug} categories={props.categories} />}
      </div>
    </li>
  )
}

// ---- live suggestions ------------------------------------------------------
// One suggest call per payee per visit: the popover opens instantly and the
// answer lands async; same-merchant rows share the verdict. Teaching a rule
// clears the cache (a new rule changes what every tier would say).
const suggestCache = new Map<string, Promise<Suggest>>()

function suggestFor(row: ActivityRow): Promise<Suggest> {
  const key = row.payee.trim().toUpperCase()
  const hit = suggestCache.get(key)
  if (hit) return hit
  const fresh = api.suggest(row.id)
  suggestCache.set(key, fresh)
  fresh.catch(() => suggestCache.delete(key)) // a miss may retry next open
  return fresh
}

function categoryLabel(account: string, categories: Category[]): string {
  return categories.find((c) => c.account === account)?.label
    ?? account.split(':').slice(1).join(' · ')
}

const SOURCE_DETAIL: Record<Suggestion['source'], string> = {
  rule: 'your rules',
  plaid: 'bank hint',
  apple: 'on-device',
}

/** "Sara thinks: Dining (on-device)" — and on person/P2P rows the reminder
 * that her guess stays a guess until the owner says so. */
function SaraLine({ s, categories }: { s: Suggest; categories: Category[] }) {
  if (!s.suggestion) return null
  return (
    <div className="saraline">
      Sara thinks: <b>{categoryLabel(s.suggestion.account, categories)}</b>
      {' '}({SOURCE_DETAIL[s.suggestion.source]})
      {s.guarded && <span className="guardnote"> — a person needs your word</span>}
    </div>
  )
}

// ---- the category picker ---------------------------------------------------
const NEW_CATEGORY = '__new__'
// Beancount's account grammar, narrowed to the teachable roots — the server
// re-validates; this only keeps the button honest while typing.
const ACCOUNT_RE = /^(?:Expenses|Income)(?::[A-Z0-9][A-Za-z0-9-]*)+$/

function accountHint(name: string): string | null {
  if (ACCOUNT_RE.test(name)) return null
  if (!/^(?:Expenses|Income)(?::|$)/.test(name)) {
    return 'start with Expenses: or Income:'
  }
  return 'segments are Capitalized (letters, digits, dashes), separated by ":"'
}

/** Completions for the inline input: every existing chart prefix that
 * extends what's typed, so new leaves land under known branches. */
function completions(input: string, categories: Category[]): string[] {
  const prefixes = new Set<string>(['Expenses:', 'Income:'])
  for (const c of categories) {
    const segs = c.account.split(':')
    for (let i = 2; i <= segs.length; i++) prefixes.add(segs.slice(0, i).join(':'))
  }
  const q = input.toLowerCase()
  return [...prefixes]
    .filter((p) => p.toLowerCase().startsWith(q) && p !== input)
    .sort()
    .slice(0, 8)
}

/** The teach flows' category control: the dropdown, plus a "New category…"
 * escape hatch that swaps in a validated inline input autocompleting
 * against the chart's existing segments. */
function CategoryPicker(props: {
  categories: Category[]
  picked: string
  newMode: boolean
  newName: string
  placeholder: string
  ariaLabel: string
  onPick: (account: string) => void
  onNewMode: (on: boolean) => void
  onNewName: (name: string) => void
}) {
  const listId = useId()
  const expenses = props.categories.filter((c) => c.account.startsWith('Expenses:'))
  const income = props.categories.filter((c) => c.account.startsWith('Income:'))
  if (props.newMode) {
    const hint = props.newName ? accountHint(props.newName) : null
    return (
      <span className="newcat">
        <input
          type="text"
          value={props.newName}
          list={listId}
          onChange={(e) => props.onNewName(e.target.value)}
          placeholder="Expenses:Health:Acupuncture"
          aria-label="new category name"
          aria-invalid={hint !== null || undefined}
          spellCheck={false}
          autoFocus
        />
        <datalist id={listId}>
          {completions(props.newName, props.categories).map((p) => (
            <option key={p} value={p} />
          ))}
        </datalist>
        <button type="button" className="quietlink"
          onClick={() => props.onNewMode(false)}>
          pick from list
        </button>
        {hint && <span className="newcathint">{hint}</span>}
      </span>
    )
  }
  return (
    <select
      value={props.picked}
      aria-label={props.ariaLabel}
      onChange={(e) => {
        if (e.target.value === NEW_CATEGORY) props.onNewMode(true)
        else props.onPick(e.target.value)
      }}
    >
      <option value="">{props.placeholder}</option>
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
      <option value={NEW_CATEGORY}>New category…</option>
    </select>
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
