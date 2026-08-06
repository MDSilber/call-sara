/**
 * Vault file access: the GitHub Contents API (raw media type) with a small
 * in-isolate cache — ETag revalidation past a 60s TTL, so a burst of tool
 * calls costs one GitHub round-trip and a quiet hour costs a few 304s.
 *
 * With no GITHUB_TOKEN configured (local `wrangler dev`), the bundled demo
 * fixture stands in for reports/summary.json so the whole MCP surface can be
 * exercised end-to-end without credentials.
 */
import fixtureSummary from "../dev/fixture-summary.json";
import type { Env } from "./types";

export const SUMMARY_PATH = "reports/summary.json";
const TTL_MS = 60_000;
const MAX_BYTES = 500_000; // fact files are prose; anything bigger is a wrong path
const API_VERSION = "2022-11-28";

/** Failure a tool can relay verbatim — message is operator-facing, never a stack. */
export class VaultFetchError extends Error {}

interface CacheEntry {
  body: string;
  etag: string | null;
  fetchedAt: number;
}

const cache = new Map<string, CacheEntry>();

function log(event: string, fields: Record<string, unknown>): void {
  console.log(JSON.stringify({ event, ...fields })); // wrangler tail-friendly
}

function repoCoords(env: Env): { owner: string; repo: string; branch: string } {
  const owner = env.GITHUB_OWNER?.trim();
  const repo = env.GITHUB_REPO?.trim();
  if (!owner || !repo) {
    throw new VaultFetchError(
      "Server misconfigured: GITHUB_OWNER / GITHUB_REPO are unset (wrangler.toml [vars])."
    );
  }
  return { owner, repo, branch: env.GITHUB_BRANCH?.trim() || "main" };
}

/** Fetch one vault file as text. `path` must already be allowlist-validated. */
export async function fetchVaultFile(env: Env, path: string): Promise<string> {
  if (!env.GITHUB_TOKEN) {
    if (path === SUMMARY_PATH) {
      log("fixture_serve", { path });
      return JSON.stringify(fixtureSummary);
    }
    throw new VaultFetchError(
      `Dev fixture mode (GITHUB_TOKEN unset) only serves ${SUMMARY_PATH}; ` +
        `set the secret to read ${path} from the vault repo.`
    );
  }

  const now = Date.now();
  const hit = cache.get(path);
  if (hit && now - hit.fetchedAt < TTL_MS) {
    return hit.body;
  }

  const { owner, repo, branch } = repoCoords(env);
  const encoded = path.split("/").map(encodeURIComponent).join("/");
  const url = `https://api.github.com/repos/${owner}/${repo}/contents/${encoded}?ref=${encodeURIComponent(branch)}`;
  const headers: Record<string, string> = {
    accept: "application/vnd.github.raw+json",
    authorization: `Bearer ${env.GITHUB_TOKEN}`,
    "user-agent": "personal-mcp",
    "x-github-api-version": API_VERSION,
  };
  if (hit?.etag) {
    headers["if-none-match"] = hit.etag;
  }

  const started = Date.now();
  const res = await fetch(url, { headers });
  log("github_fetch", { path, status: res.status, ms: Date.now() - started });

  if (res.status === 304 && hit) {
    cache.set(path, { ...hit, fetchedAt: now });
    return hit.body;
  }
  if (res.status === 404) {
    throw new VaultFetchError(`Not in the vault repo: ${path} (branch ${branch}).`);
  }
  if (res.status === 401 || res.status === 403) {
    throw new VaultFetchError(
      "GitHub rejected the token — check the PAT's expiry and that it grants " +
        "read-only Contents on the vault repo."
    );
  }
  if (!res.ok) {
    throw new VaultFetchError(`GitHub Contents API failed for ${path}: HTTP ${res.status}.`);
  }

  const body = await res.text();
  if (body.length > MAX_BYTES) {
    throw new VaultFetchError(`${path} is ${body.length} bytes — over the ${MAX_BYTES} limit.`);
  }
  cache.set(path, { body, etag: res.headers.get("etag"), fetchedAt: now });
  return body;
}

/** True when tools are answering from the bundled demo fixture. */
export function fixtureMode(env: Env): boolean {
  return !env.GITHUB_TOKEN;
}
