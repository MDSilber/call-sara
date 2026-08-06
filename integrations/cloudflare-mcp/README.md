# Personal MCP — Sara, anywhere

A tiny remote MCP server on Cloudflare Workers that lets Claude on your
phone (or anywhere else) read your finance vault — net worth, spending
pace, findings, forecast, the works — **without your laptop being on**.

It is strictly read-only, and it deliberately has **one source of truth**:
the vault's own private GitHub repo. Every time reports regenerate, the
vault commits `reports/summary.json` (a machine-readable twin of the
reports, produced by the same verified math). This Worker fetches that
file — plus raw `facts/` and `reports/` files on request — straight from
the GitHub Contents API with a small ETag cache. No database, no sync
pipeline, nothing to drift.

It's also a **template**: the server is a generic "personal MCP" with
domains as modules. Finance is the first domain (14 `finance_*` tools);
add your own next to it (see "Adding a domain").

```
Claude (iOS / claude.ai / Claude Code)
   │  streamable HTTP + bearer token
   ▼
Cloudflare Worker  /mcp        ← this directory
   │  GitHub Contents API (raw), fine-grained read-only PAT, ~60s ETag cache
   ▼
github.com/you/your-vault-repo (PRIVATE)   reports/summary.json · facts/ · reports/
```

## Tools (finance domain, all read-only)

`finance_networth` · `finance_balances` · `finance_positions` ·
`finance_spend(period)` · `finance_cashflow` · `finance_findings` ·
`finance_forecast` · `finance_autopilot` · `finance_goals_529` ·
`finance_calendar` · `finance_thesis_rules` · `finance_home_summary` ·
`finance_freshness` · `finance_read_fact(path)`

Every answer leads with a human-readable block (window labels and the
snapshot stamp always included, a loud warning when the snapshot is over
7 days old) and ends with compact JSON. `finance_read_fact` is
allowlisted to `facts/` and `reports/` paths — it cannot read the ledger
or anything else.

## Deploy it

Prerequisites: Node 20+, a Cloudflare account (free tier is fine), and a
vault whose reports include `reports/summary.json` (any vault generated
by this skill does — run `tools/run reports.py` once and commit/push).

**1. Create the fine-grained GitHub PAT (read-only, one repo).**
GitHub → Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token:
- Resource owner: **your personal account** (the vault lives there)
- Repository access: **Only select repositories** → your private vault repo
- Repository permissions: **Contents: Read-only**. Nothing else. Leave
  every other permission on "No access".
- Pick an expiry you'll actually renew (e.g. 90 days) and calendar it.

**2. Point the Worker at your vault.** Edit `wrangler.toml` `[vars]`:
`GITHUB_OWNER`, `GITHUB_REPO`, `GITHUB_BRANCH` (and `SERVER_NAME` if you
like). These are not secrets; the tokens never go here.

**3. Log in to Cloudflare — your PERSONAL account.**

```bash
cd integrations/cloudflare-mcp
npm install
npx wrangler login       # pick your PERSONAL account, not any work account
npx wrangler whoami      # verify the account shown is yours before deploying
```

**4. Set the two secrets.**

```bash
npx wrangler secret put GITHUB_TOKEN     # paste the PAT from step 1
openssl rand -base64 32                  # generate a strong bearer token…
npx wrangler secret put SARA_MCP_TOKEN   # …and paste it here (save it for step 6)
```

**5. Deploy.**

```bash
npm run deploy           # prints https://<name>.<subdomain>.workers.dev
```

**6. Connect Claude.**
- **Claude iOS app / claude.ai:** Settings → Connectors → Add custom
  connector. URL: `https://<your-worker>.workers.dev/mcp`. In the
  dialog's advanced/request-headers section add header `Authorization`
  with value `Bearer <your SARA_MCP_TOKEN>` (word "Bearer", space,
  token). If your app build only offers OAuth fields there, connect via
  Claude Code below, or put Cloudflare Access in front (see "Harden it").
- **Claude Code:**

```bash
claude mcp add --transport http sara https://<your-worker>.workers.dev/mcp \
  --header "Authorization: Bearer <your SARA_MCP_TOKEN>"
```

Then ask: "what's our net worth?" — Claude calls `finance_networth` and
answers from the vault's own numbers, with the snapshot date attached.

## Smoke test with curl

```bash
URL=https://<your-worker>.workers.dev/mcp
TOKEN=<your SARA_MCP_TOKEN>

# no token → 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$URL" \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# with token → the 14 tools
curl -s -X POST "$URL" \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# call one
curl -s -X POST "$URL" \
  -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"finance_networth","arguments":{}}}'
```

## Local dev (no credentials needed)

```bash
cp .dev.vars.example .dev.vars   # SARA_MCP_TOKEN=dev-token; GITHUB_TOKEN unset
npm run dev                      # http://localhost:8787/mcp
```

With `GITHUB_TOKEN` unset the Worker serves a bundled **demo fixture**
(the fictional Demo household's `summary.json`) and says so in every
response — the full MCP surface works end-to-end with zero secrets.
`npm run typecheck` for strict tsc; `wrangler tail` streams the
structured logs in production.

## Freshness (the one honest caveat)

The Worker serves the last summary the vault **pushed**. Import
statements → reports regenerate → vault commits and pushes: that's the
refresh. `finance_freshness` tells you exactly how old the snapshot is,
and every tool shouts when it's more than a week stale. The Worker also
re-checks GitHub with an ETag after ~60s, so a push shows up within a
minute.

## Harden it

- The shipped gate is a single static bearer token, checked
  constant-time before any MCP traffic (`src/auth.ts`). Rotate it by
  re-running `wrangler secret put SARA_MCP_TOKEN`.
- Belt and suspenders: put **Cloudflare Access** in front of the Worker
  (Zero Trust → Access → Applications → your workers.dev route) — a
  service token or identity check runs before a request ever reaches the
  code, and revocation is instant.
- The MCP spec's full authorization story for remote servers is OAuth
  2.1; `src/auth.ts` is the one seam to swap for Cloudflare's
  `workers-oauth-provider` if this ever serves more than you.
- Blast radius if the bearer leaks anyway: read-only numbers from
  `summary.json` plus `facts/`/`reports/` text. The PAT is the tighter
  secret — it lives only in Worker secrets, is scoped to one repo,
  read-only, and expires.

## Adding a domain

Three steps, no framework:

1. Write `src/domains/<name>.ts` exporting
   `register<Name>Domain(server: McpServer, env: Env)` — register tools
   named `<name>_*` (see `src/domains/finance.ts` for the shape).
2. Import it in `src/server.ts`.
3. Add it to the `DOMAINS` array there.

That's the whole pattern. Smart-home next? `home_lights_status` awaits.

## Files

- `src/index.ts` — entry: bearer gate, then the streamable-HTTP MCP
  handler (`createMcpHandler` from Cloudflare's agents SDK) at `/mcp`
- `src/auth.ts` — the bearer middleware (the auth seam)
- `src/github.ts` — GitHub Contents fetcher, ETag + 60s TTL cache,
  dev-fixture fallback
- `src/server.ts` — the domain registry
- `src/domains/finance.ts` — the 14 finance tools
- `src/types.ts` — `Env` + the `summary.json` schema
- `dev/fixture-summary.json` — the demo household's snapshot (synthetic)
