import type { OAuthHelpers } from "@cloudflare/workers-oauth-provider";

/** Worker bindings. Secrets come from `wrangler secret put`; vars from wrangler.toml. */
export interface Env {
  /** Identity shown to MCP clients (wrangler.toml var). */
  SERVER_NAME?: string;
  /**
   * The owner token. Secret; unset = server refuses all access. Presented as a
   * static bearer by CLI/agent clients, or pasted ONCE into the OAuth consent
   * screen by browser-driven clients (claude.ai / the iOS app).
   */
  SARA_MCP_TOKEN?: string;
  /** Fine-grained PAT, read-only Contents on the vault repo. Secret; unset = dev fixture mode. */
  GITHUB_TOKEN?: string;
  GITHUB_OWNER?: string;
  GITHUB_REPO?: string;
  GITHUB_BRANCH?: string;
  /** OAuth state storage (workers-oauth-provider): tokens/codes hashed, props encrypted. */
  OAUTH_KV: KVNamespace;
  /** Injected by OAuthProvider into handlers it invokes. */
  OAUTH_PROVIDER: OAuthHelpers;
}

/**
 * reports/summary.json — the machine-readable twin the vault's reports.py
 * emits (see skills/finance/tools/summary.py). Sections are typed only as
 * deep as the tools reach; unknown extras pass through untouched.
 */
export interface Summary {
  schema: number;
  generated_at: string;
  ledger_through: string | null;
  networth: {
    window: string;
    liquid: number;
    assets: number;
    liabilities: number;
    paper: number;
    paper_currency: string | null;
    combined: number;
    unpriced_accounts: { account: string; held: string }[];
    monthly_series: {
      window: string;
      points: { date: string; value: number; at_cost: boolean }[];
    };
  };
  balances: {
    window: string;
    accounts: { account: string; usd: number }[];
  };
  positions: {
    window: string;
    holdings: { symbol: string; units: number; usd: number | null }[];
  };
  spend: {
    current_month: {
      month: string;
      window: string;
      fallback_latest_imported: boolean;
      spent: number;
      typical: number | null;
      typical_window: string | null;
      typical_by_now: number | null;
      pace_delta: number | null;
      left_of_typical: number | null;
      verdict: string;
    };
    last_closed_month: { month: string; total: number } | null;
    monthly_by_category: {
      window: string;
      months: string[];
      categories: Record<string, number[]>;
      totals: number[];
    };
  };
  cashflow: {
    window: string;
    months: { month: string; income: number; expenses: number; net: number }[];
  };
  findings: {
    window: string;
    generated: string | null;
    counts: string;
    items: { severity: string; title: string; check: string; detail: string }[];
    errors: string[];
  };
  forecast: {
    window: string;
    household: {
      start: number;
      income: number;
      expenses: number;
      transfer_net: number;
      oneoff_total: number;
      surplus: number;
      oneoffs: { date: string; amount: number; text: string; source: string }[];
      warns: {
        account: string;
        kind: string;
        min: number;
        date: string;
        floor: number | null;
        drivers: string;
      }[];
    };
    accounts: {
      account: string;
      start: number;
      asof: string | null;
      min: number;
      min_date: string;
      end_balance: number;
      floor: number | null;
      projected_flows: number;
    }[];
  };
  autopilot: {
    window: string;
    summary: string;
    lanes: {
      name: string;
      kind: string;
      account: string;
      cadence: string;
      status: string;
      last: string | null;
      last_amount: number | null;
      expected: string | null;
      balance: number | null;
      floor: number | null;
      note: string;
      detail: string;
    }[];
  };
  education_529: {
    window: string;
    accounts: { account: string; kid: string; value: number; at_cost: boolean }[];
    total: number;
    target: number | null;
    contribution_pace_monthly: number | null;
    verdict: string | null;
  };
  goals: {
    config: Record<string, unknown>;
    calendar: {
      window: string;
      upcoming: { date: string; days_until: number; text: string; source: string }[];
      recently_passed: { date: string; text: string; source: string }[];
    };
  };
  thesis_rules: {
    source: string | null;
    sections: { heading: string; rules: string[] }[];
  };
  glance: {
    sara: string;
    spend: { verdict: string; fig: string; sub: string; streak: string };
    networth: { value: number; delta: string | null; window: string };
    autopilot: { verdict: string; sub: string; dots: string[] };
    education: { label: string; verdict: string; fig: string; sub: string };
    next: { label: string; text: string; meta: string };
  };
}
