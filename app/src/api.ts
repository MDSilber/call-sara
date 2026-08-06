/** Fetch wrapper: same-origin reads, token-carrying writes.
 *
 * The server injects a per-launch token into index.html as
 * <meta name="sara-token">; every POST carries it back in X-Sara-Token.
 * A cross-site page can neither read the token nor set the header, so the
 * write surface stays local even while the server runs.
 */
import type {
  Activity, Autopilot, CategorizeResult, DismissResult, Freshness, Glance,
  Goals, Investments, Networth, SetGoalResult, Spend,
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

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init)
  if (!res.ok) {
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
  return res.json() as Promise<T>
}

const get = <T,>(path: string) => request<T>(path)

const post = <T,>(path: string, body: unknown) =>
  request<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Sara-Token': token() },
    body: JSON.stringify(body),
  })

export const api = {
  glance: () => get<Glance>('/api/glance'),
  activity: (month?: string) =>
    get<Activity>(`/api/activity${month ? `?month=${encodeURIComponent(month)}` : ''}`),
  spend: () => get<Spend>('/api/spend'),
  networth: () => get<Networth>('/api/networth'),
  investments: () => get<Investments>('/api/investments'),
  goals: () => get<Goals>('/api/goals'),
  autopilot: () => get<Autopilot>('/api/autopilot'),
  freshness: () => get<Freshness>('/api/freshness'),
  categorize: (payee_pattern: string, account: string, apply_history: boolean) =>
    post<CategorizeResult>('/api/actions/categorize',
      { payee_pattern, account, apply_history }),
  setGoal: (key: string, value: number | boolean) =>
    post<SetGoalResult>('/api/actions/set-goal', { key, value }),
  dismiss: (finding_id: string, until: string | null, title = '') =>
    post<DismissResult>('/api/actions/dismiss', { finding_id, until, title }),
}
