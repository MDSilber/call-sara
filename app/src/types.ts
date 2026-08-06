/** The API contract, mirrored.
 *
 * Money is ALWAYS a preformatted display string from the server (true
 * minus, whole dollars, ≈ on estimates). Plain numbers appear only as
 * chart geometry. The frontend renders strings and never does money math.
 */

// ---- shared chart payloads (built by tools/home.py chart builders) ----
export interface YAxis {
  min: number
  max: number
  step: number
  labels: Record<string, string>
}

export interface Tip {
  t: string
  rows: [string, string][]
}

export interface PaceChart {
  days: string[]
  actual: (number | null)[]
  ideal: number[]
  tips: Tip[]
  y: YAxis
  xint: number
  now: { xy: [number, number]; label: string; side: 'left' | 'right' } | null
}

export interface NwChart {
  labels: string[]
  market: (number | null)[]
  atcost: (number | null)[]
  tips: Tip[]
  y: YAxis
  xint: number
  seam: number | null
  seamLabel: string
  end: { xy: [number, number]; label: string }
}

export interface Spark {
  points: string
  area: string
  win: string
}

// ------------------------------------------------------------- glance
export interface SpendTile {
  verdict: string
  cls: '' | 'good' | 'bad' | 'warn'
  fig: string
  sub: string
  streak: string
  glow: boolean
}

export interface Glance {
  generated_at: string
  greet: string
  sara: string
  ledger_stamp: string
  checks_stamp: string
  tiles: {
    spend: SpendTile
    networth: {
      value: string
      delta: { cls: string; text: string } | null
      spark: Spark | null
      sub: string
      glow: boolean
    }
    autopilot: {
      verdict: string
      cls: string
      sub: string
      dots: string[]
      aria: string
    }
    education: { label: string; verdict: string; cls: string; fig: string; sub: string }
  }
  next: { label: string; quiet: boolean; text: string; meta: string }
}

// ------------------------------------------------------------ activity
export interface ActivityRow {
  payee: string
  narration: string
  account: string
  category: string
  amt: string
  kind: 'expense' | 'income' | 'uncategorized'
}

export interface Activity {
  months: { ym: string; label: string }[]
  month: string | null
  label?: string
  totals?: { spent: string; received: string; window: string }
  days: { date: string; label: string; rows: ActivityRow[] }[]
  categories: { account: string; label: string }[]
  uncategorized_month?: number
  uncategorized_total: number
}

// --------------------------------------------------------------- spend
export interface MerchantPeriod {
  amt: string
  pct: string
  w: number
  merch: [string, string][]
  more: number
}

export interface SpendCat {
  name: string
  series: number[]
  tips: Tip[]
  y: YAxis
  per: Partial<Record<'cur' | 'prev' | 'six', MerchantPeriod>>
}

export interface SpendRooms {
  periods: { key: 'cur' | 'prev' | 'six'; label: string; win: string; total: string }[]
  cats: SpendCat[]
  order: Record<'cur' | 'prev' | 'six', number[]>
  months: string[]
  trendWin: string
  partialIdx: number
  visible: number
}

export interface Spend {
  pace: {
    empty: boolean
    window: string
    sub: string
    hero?: string
    hero_cls?: string
    herolab?: string
    lag_note?: string
  }
  pace_chart: PaceChart | null
  tile: SpendTile
  rooms: SpendRooms | null
  cheshbon: {
    title: string
    window: string
    inc: string
    exp: string
    net: string
    net_cls: string
    payday_note: string
    closed: { month: string; inc: string; exp: string; net: string } | null
    wink: string
  }
  wins: {
    total: string
    year: string
    count_lbl: string
    rows: { label: string; amt: string }[]
  } | null
}

// ------------------------------------------------------------ networth
export interface MapNode {
  name: string
  value: number
  amt: string
  pct: string
  /** Owner chip (household lens) — present when the node's accounts share one owner. */
  own?: string
  cvar?: string
  children?: MapNode[]
}

export interface ThesisRow {
  label: string
  state: '' | 'over' | 'under'
  now: string
  value: string
  target: string
  band: string
  fill: string
  tick: string
  delta: string
}

export interface Networth {
  headline: {
    sub: string
    window: string
    liquid: string
    asof: string
    delta: { cls: string; body: string } | null
    has_chart: boolean
    table_rows: [string, string, string][]
  }
  chart: NwChart | null
  spark: Spark | null
  attribution: {
    rows: {
      window: string
      suppressed: string | null
      delta?: string
      cls?: string
      body?: string
      note?: string
      segs?: { cls: string; width: string }[] | null
      aria?: string
    }[]
  } | null
  map: {
    tree: MapNode[]
    caption: string
    totalN: number
    window: string
    has_owners: boolean
    /** [account, balance] — plus an owner label once the ledger declares owners. */
    table_rows: [string, string][] | [string, string, string][]
  } | null
  thesis: {
    rows: ThesisRow[]
    nudge: string | null
    sub?: string
    window?: string
    notes?: string
    conc?: string
    any_out?: boolean
  }
  paper: string | null
  unpriced: string[]
  milestones: { pct: number; next: string | null; crossed: number; label: string } | null
  window: string
}

// --------------------------------------------------------- investments
export interface Position {
  symbol: string
  units: string
  value: string | null
  valueN: number
  price: string | null
  price_date: string | null
  share: string
}

export interface Investments {
  window: string
  positions: Position[]
  paper_note: string | null
  invested_total: string
  allocation: {
    invested: string
    rows: { label: string; value: number; amt: string; pct: string; target: string; out: boolean }[]
    cash_above_reserve: string | null
  } | null
  dividends: { ytd: string; count: number; window: string; note: string }
  contributions: {
    bought: string
    window: string
    note: string
    lanes: {
      name: string
      cadence: string
      status: string
      last: string | null
      last_amount: string | null
    }[]
  }
}

// --------------------------------------------------------------- goals
export interface EduGrid {
  steps: string[]
  arrive: string[]
  cover: string[]
  def: number
  paceS: string | null
}

export interface Goals {
  education: {
    title: string
    sub: string
    empty: string
    value?: string
    val_lbl?: string
    fill?: string | null
    pct?: string
    of?: string
    nudge?: string | null
    foot?: string | null
    perkid?: { name: string; width: string; amt: string }[]
    grid: EduGrid | null
    tile: { label: string; verdict: string; cls: string; fig: string; sub: string }
  }
  envelopes: { tag: string; over: boolean; width: string; amt: string }[]
  milestones: { pct: number; next: string | null; crossed: number; label: string } | null
  settings: { key: string; value: number | string | null }[]
  window: string
}

// ----------------------------------------------------------- autopilot
export interface Lane {
  name: string
  detail: string
  dot: 'ok' | 'watch' | 'bad' | 'mut'
  tag: string
}

export interface QueueItem {
  id: string
  title: string
  severity: 'alert' | 'watch' | 'info'
  check: string
  detail: string
  fix: string
}

export interface Move {
  date: string
  days: number
  when: string
  day_lbl: string
  near: boolean
  amt: string
  text: string
  plumbing: boolean
}

export interface Autopilot {
  machine: { rows: Lane[]; sub: string; window: string; summary: string }
  needs: {
    state: 'none' | 'ok' | 'cards'
    cards: { kind: string; verb: string; why: string; meta: string; days: number | null }[]
    more: number
  }
  queue: QueueItem[]
  dismissed: { id: string; until: string | null; title: string; active: boolean }[]
  moves: Move[]
  plumbing: Move[]
  review: { count: number; amount: string; note: string }
  counts: string
  errors: string[]
  checks_from: string | null
  findings_ran: boolean
}

// ----------------------------------------------------------- freshness
export interface Freshness {
  generated_at: string
  vault: string
  ledger_through: string | null
  checks_from: string | null
  prices_through: string | null
  accounts: { account: string; last_posting: string; days_quiet: number }[]
}

// ------------------------------------------------------------- actions
export interface CategorizeResult {
  rule: { match: string; account: string }
  applied: boolean
  changed: number
  report: string
}

export interface SetGoalResult {
  key: string
  value: number | string
  previous: number | string | null
}

export interface DismissResult {
  id: string
  until: string | null
  removed: boolean
}
