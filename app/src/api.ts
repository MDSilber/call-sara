/** Fetch wrapper: same-origin reads, token-carrying writes.
 *
 * The server injects a per-launch token into index.html as
 * <meta name="sara-token">; every POST carries it back in X-Sara-Token.
 * A cross-site page can neither read the token nor set the header, so the
 * write surface stays local even while the server runs.
 *
 * v2: exploratory reads take query params (search filters, keyset cursors,
 * the in-room owner filters); two actions stream text (sync, upload confirm).
 */
import type {
  AccountRow, ActivityFilters, ActivityPage, Autopilot, CategorizeResult,
  Connections, DismissResult, FreshnessV2, Glance, Goals, Insights,
  Investments, LinkUpdate, Networth, Owners, Register,
  SearchResults, SetGoalResult, Spend, SpendDrill, UploadPlan,
} from './types'

function token(): string {
  const meta = document.querySelector('meta[name="sara-token"]')
  return meta?.getAttribute('content') ?? ''
}

export class ApiError extends Error {
  status: number
  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
  }
}

async function bail(res: Response): Promise<never> {
  let detail = `${res.status} ${res.statusText}`
  try {
    const body: unknown = await res.json()
    if (body && typeof body === 'object' && 'detail' in body) {
      detail = String((body as { detail: unknown }).detail)
    }
  } catch {
    /* non-JSON error body — keep the status line */
  }
  throw new ApiError(res.status, detail)
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) await bail(res)
  return res.json() as Promise<T>
}

/** Build "?a=1&b=2" from defined params only. */
function qs(params: Record<string, string | number | boolean | undefined | null>): string {
  const pairs = Object.entries(params).filter(
    ([, v]) => v !== undefined && v !== null && v !== '' && v !== false,
  )
  if (pairs.length === 0) return ''
  const search = new URLSearchParams()
  for (const [k, v] of pairs) search.set(k, String(v))
  return `?${search.toString()}`
}

const get = <T,>(path: string) => request<T>(path)

const post = <T,>(path: string, body: unknown) =>
  request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Sara-Token': token() },
    body: JSON.stringify(body),
  })

/** POST that streams text/plain line chunks into `onChunk`. */
async function postStream(
  path: string,
  body: unknown,
  onChunk: (text: string) => void,
): Promise<void> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Sara-Token': token() },
    body: JSON.stringify(body),
  })
  if (!res.ok) await bail(res)
  if (!res.body) {
    onChunk(await res.text())
    return
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    onChunk(decoder.decode(value, { stream: true }))
  }
  const tail = decoder.decode()
  if (tail) onChunk(tail)
}

/** The owner-filter rider: "all" (or unset) sends nothing. */
const who = (owner?: string) => (owner && owner !== 'all' ? owner : undefined)

export const api = {
  glance: () => get<Glance>('/api/glance'),
  spend: (owner?: string) => get<Spend>(`/api/spend${qs({ owner: who(owner) })}`),
  networth: () => get<Networth>('/api/networth'),
  investments: (owner?: string) =>
    get<Investments>(`/api/investments${qs({ owner: who(owner) })}`),
  goals: () => get<Goals>('/api/goals'),
  autopilot: () => get<Autopilot>('/api/autopilot'),
  freshness: () => get<FreshnessV2>('/api/freshness'),

  activity: (filters: ActivityFilters, owner?: string, cursor?: string | null) =>
    get<ActivityPage>(`/api/activity${qs({
      ...filters, owner: who(owner), cursor: cursor ?? undefined,
    })}`),
  register: (account: string, cursor?: string | null) =>
    get<Register>(`/api/register${qs({ account, cursor: cursor ?? undefined })}`),
  owners: () => get<Owners>('/api/owners'),
  accounts: () => get<{ accounts: AccountRow[] }>('/api/accounts'),
  search: (q: string) => get<SearchResults>(`/api/search${qs({ q })}`),
  insights: (owner?: string) => get<Insights>(`/api/insights${qs({ owner: who(owner) })}`),
  spendDrill: (category: string, month: string, owner?: string) =>
    get<SpendDrill>(`/api/spend/drill${qs({ category, month, owner: who(owner) })}`),
  connections: () => get<Connections>('/api/connections'),

  categorize: (payee_pattern: string, account: string, apply_history: boolean) =>
    post<CategorizeResult & { regenerating?: boolean }>('/api/actions/categorize',
      { payee_pattern, account, apply_history }),
  setGoal: (key: string, value: number | boolean) =>
    post<SetGoalResult>('/api/actions/set-goal', { key, value }),
  dismiss: (finding_id: string, until: string | null, title = '') =>
    post<DismissResult>('/api/actions/dismiss', { finding_id, until, title }),

  plaidSync: (item: string, onChunk: (t: string) => void) =>
    postStream('/api/actions/plaid-sync', { item }, onChunk),
  linkUpdate: (item: string) => post<LinkUpdate>('/api/actions/link-update', { item }),
  plaidDisable: (item: string) =>
    post<{ alias: string; disabled: boolean; note: string }>(
      '/api/actions/plaid-disable', { item }),

  upload: async (file: File): Promise<UploadPlan> => {
    const form = new FormData()
    form.append('file', file, file.name)
    const res = await fetch('/api/actions/upload', {
      method: 'POST',
      headers: { 'X-Sara-Token': token() },
      body: form,
    })
    if (!res.ok) await bail(res)
    return res.json() as Promise<UploadPlan>
  },
  uploadConfirm: (upload_id: string, onChunk: (t: string) => void) =>
    postStream('/api/actions/upload-confirm', { upload_id }, onChunk),
}
