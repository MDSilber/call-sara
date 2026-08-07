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

// ------------------------------------------------------------ activity v2
export interface ActivityRow {
  id: number
  date: string
  day: string
  payee: string
  narration: string
  account: string
  source_account: string
  owner: string | null
  category: string
  amt: string
  kind: 'expense' | 'income' | 'uncategorized'
  classifier: string
}

export interface Category {
  account: string
  label: string
}

export interface ActivityPage {
  rows: ActivityRow[]
  cursor: string | null
  /** First page only. */
  matched?: number
  totals?: { spent: string; received: string }
  categories?: Category[]
  uncategorized?: { count: number; amount: string }
  owners?: OwnerDef[]
}

export interface ActivityFilters {
  q?: string
  amount_min?: number
  amount_max?: number
  category?: string
  account?: string
  date_from?: string
  date_to?: string
  uncategorized_only?: boolean
}

// --------------------------------------------------------------- owners
export interface OwnerDef {
  owner: string
  label: string
}

export interface Owners {
  owners: OwnerDef[]
  slices: { owner: string; liquid: string; accounts: number }[]
}

// -------------------------------------------------------------- register
export interface RegisterRow {
  id: number
  date: string
  day: string
  payee: string
  narration: string
  other: string
  amt: string
  neg: boolean
  balance: string
}

export interface Register {
  account: string
  found: boolean
  foreign_owner?: string | null
  owner?: string | null
  institution?: string | null
  is_open?: boolean
  opened?: string | null
  postings?: number
  balance?: string
  balances?: { currency: string; units: string; value: string | null }[]
  rows: RegisterRow[]
  cursor: string | null
}

export interface AccountRow {
  account: string
  owner: string | null
  institution: string | null
  is_open: boolean
  balance: string
}

// -------------------------------------------------------------- insights
export interface InsightCat {
  name: string
  series: number[]
  cur: string
  avg: string
  delta: string
  delta_cls: 'good' | 'bad'
}

export interface Insights {
  cats: InsightCat[]
  months: string[]
  window: string
}

export interface SpendDrill {
  category: string
  month: string
  total: string
  merchants: { name: string; amt: string; n: number }[]
  more: number
}

// ----------------------------------------------------------- connections
export interface ConnectionItem {
  alias: string
  products: string[]
  token_var: string
  token_present: boolean
  accounts: { tail: string; ledger_account: string }[]
  status: 'fresh' | 'stale' | 'dead' | 'never' | 'no-token'
  last_synced: string | null
  last_synced_lbl: string | null
  silent_days: number | null
}

export interface Connections {
  configured: boolean
  items: ConnectionItem[]
  slots: { used: number; total: number; line: string }
  keys_present: boolean
  fixture: boolean
}

export interface LinkUpdate {
  alias: string
  link_token: string
  mode: string
}

// --------------------------------------------------------------- uploads
export interface UploadPlan {
  upload_id: string
  kind: string
  display: string
  label: string
  note: string
  recognized: boolean
  files_to: string
  import_cmd: string | null
  report: string
  unknown_note: string
}

// ---------------------------------------------------------------- search
export interface SearchResults {
  accounts: AccountRow[]
  txns: ActivityRow[]
}

// ------------------------------------------------------------- freshness
export interface FreshnessV2 {
  summary_generated_at: string | null
  ledger_through: string | null
  db_built_at: string
  db_txns: number
  regen: {
    running: boolean
    last_started: string | null
    last_finished: string | null
    last_ok: boolean | null
    last_error: string
  }
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
  /** Present when the payload was computed under a person/joint lens. */
  owner?: string
  pace: {
    empty: boolean
    /** Owner-lens payloads title the card ("Danny’s spending"). */
    title?: string
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

export interface Lot {
  account: string
  symbol: string
  units: string
  acquired: string | null
  acquired_lbl: string
  term: 'LT' | 'ST'
  basis: string
  value: string | null
  valueN: number
  gain: string | null
  gain_cls: 'good' | 'bad' | ''
  gain_pct: string | null
}

export interface DividendsTimeline {
  months: { month: string; label: string; value: number; amt: string; n: number }[]
  ytd: string
  ytd_count: number
}

export interface ContributionPace {
  year: number
  rows: {
    key: string
    label: string
    contributed: string
    contributedN: number
    limit?: string
    limit_year?: string
    pct?: number
    room?: string
  }[]
  source: string | null
  note: string
}

export interface Investments {
  window: string
  owner?: string
  lots: Lot[]
  dividends_timeline: DividendsTimeline
  contribution_pace: ContributionPace
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

// ---------------------------------------------------------- owner map
export interface OwnerMapNode {
  name: string
  account?: string
  value: number
  amt: string
  pct: string
  children?: OwnerMapNode[]
}

export interface OwnerMap {
  owner: string
  tree: OwnerMapNode[]
  assets: string
  owed: string | null
  net: string
  caption: string
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
