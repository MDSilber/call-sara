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
domains as modules. Finance is the first domain (13 `finance_*` tools
plus the `finance://` resources); add your own next to it (see "Adding
a domain").

```
Claude (iOS / claude.ai / Claude Code)
   │  streamable HTTP · OAuth 2.1 (or a static bearer)
   ▼
Cloudflare Worker  /mcp        ← this directory
   │  GitHub Contents API (raw), fine-grained read-only PAT, ~60s ETag cache
   ▼
github.com/you/your-vault-repo (PRIVATE)   reports/summary.json · facts/ · reports/
```

## Tools (finance domain, all read-only)

The design rule: **computed answers are tools, owner documents are
resources, method rides in the domain's ask tool.**

Fourteen tools. Two front doors — `finance_overview` (the whole picture
in one call, for vague/basic questions) and `finance_ask_sara(question)`
(advice mode: returns an advisory *briefing* — voice rules, the written
thesis, and the numbers relevant to the question — so the calling
assistant answers as the household's advisor without inventing figures).
Then the specifics: `finance_networth` · `finance_balances` ·
`finance_positions` · `finance_spend(period)` · `finance_cashflow` ·
`finance_findings` · `finance_forecast` · `finance_autopilot` ·
`finance_goals_529` · `finance_calendar` · `finance_freshness` —
plus `finance_calc`, pure Decimal arithmetic that touches no vault data
(the twin of the skill's `tools/calc.py`, same grammar).

Every answer leads with a human-readable block (window labels and the
snapshot stamp always included, a loud warning when the snapshot is over
7 days old) and ends with compact JSON.

## Resources (the owner's documents, verbatim)

- `finance://thesis` — THESIS.md, the written investment policy
- `finance://reports/findings` — the full findings report
- `finance://reports/summary` — the raw summary.json the tools compute from
- `finance://facts/{+path}` — resource template over the vault's `facts/`
  tree (e.g. `finance://facts/household/profile.md`), allowlisted to
  simple `facts/` paths — it cannot read the ledger or anything else.
  `resources/list` enumerates a curated set (thesis, findings, summary,
  household profile, household calendar); any allowlisted facts path
  reads through the template.

Documents are served as `text/markdown` (summary as `application/json`),
size-capped, fetched from GitHub exactly like the tools.

## Sara Lite (the method, on chat surfaces)

`skills/sara-lite/` in this repo is a claude.ai-uploadable custom skill
that pairs with this connector: Sara's voice, the numbers-only-from-tools
rule, and the tool/resource routing above. Upload it at claude.ai →
Settings → Capabilities → Skills and phone chats answer money questions
in Sara's voice with every figure pulled from the connector.

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

**4. Create the OAuth-state KV namespace (one time).**

```bash
npx wrangler kv namespace create OAUTH_KV
```

Paste the printed `id` into `wrangler.toml` under `[[kv_namespaces]]`.
(It stores OAuth grants — tokens and codes hashed, session props
encrypted. Any placeholder id works for local `wrangler dev`.)

**5. Set the two secrets.**

```bash
npx wrangler secret put GITHUB_TOKEN     # paste the PAT from step 1
openssl rand -base64 32                  # generate the owner token…
npx wrangler secret put SARA_MCP_TOKEN   # …and paste it here (save it for step 7)
```

**6. Deploy.**

```bash
npm run deploy           # prints https://<name>.<subdomain>.workers.dev
```

**7. Connect Claude.**
- **Claude iOS app / claude.ai:** Settings → Connectors → Add custom
  connector. URL: `https://<your-worker>.workers.dev/mcp`. Leave the
  OAuth fields **empty** — the server supports discovery and dynamic
  client registration, so Claude wires itself up. On connect, a browser
  page opens ("… wants read-only access"): paste your `SARA_MCP_TOKEN`
  once and hit Approve. Tokens refresh on their own after that.
- **Claude Code / scripts / agents** (static bearer, no OAuth dance):

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

# no token → 401 (the WWW-Authenticate header carries the OAuth discovery pointer)
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

## How auth works (and hardening)

- **OAuth 2.1, single-owner consent.** Cloudflare's
  `workers-oauth-provider` owns discovery (`/.well-known/*`), open
  dynamic client registration + client-ID metadata documents, PKCE, the
  token endpoint, and bearer validation on `/mcp`. Registration is open
  by design (that's what lets claude.ai's dialog work with empty
  fields) but grants NOTHING: the only identity step is the
  `/authorize` consent screen, which approves a client when you paste
  `SARA_MCP_TOKEN` — one paste per client, constant-time checked,
  refresh tokens after that. There are no accounts and no cookies;
  possession of the owner secret IS the approval.
- **Static bearer, still first-class.** `Authorization: Bearer
  <SARA_MCP_TOKEN>` at `/mcp` keeps working for CLI/agents/curl (wired
  through the provider's `resolveExternalToken` hook, tried only after
  its own tokens fail). Same secret, two doors.
- Rotate the owner token with `wrangler secret put SARA_MCP_TOKEN` —
  existing OAuth grants keep working (revoke by deleting the KV
  namespace's grants or the namespace itself); the static path and new
  consents use the new token immediately.
- Belt and suspenders: put **Cloudflare Access** in front of the Worker
  (Zero Trust → Access → Applications → your workers.dev route) — an
  identity check runs before a request ever reaches the code.
- Blast radius if the owner token leaks anyway: read-only numbers from
  `summary.json` plus `facts/`/`reports/` text. The PAT is the tighter
  secret — it lives only in Worker secrets, is scoped to one repo,
  read-only, and expires.

## Adding a domain

Three steps, no framework:

1. Write `src/domains/<name>.ts` exporting
   `register<Name>Domain(server: McpServer, env: Env)` — register tools
   named `<name>_*` AND resources under the `<name>://` URI scheme (see
   `src/domains/finance.ts` for both shapes: `server.registerTool`,
   `server.registerResource` with a fixed URI or a `ResourceTemplate`,
   plus a curated `list` callback so clients can browse).
2. Import it in `src/server.ts`.
3. Add it to the `DOMAINS` array there.

A domain contributes tools + resources under its prefix, split by one
rule: **computed answers are tools, owner documents are resources,
method rides in the domain's ask tool.**

That's the whole pattern. Smart-home next? `home_lights_status` awaits.

## Files

- `src/index.ts` — entry: the OAuthProvider wrapping the
  streamable-HTTP MCP handler (`createMcpHandler` from Cloudflare's
  agents SDK) at `/mcp`
- `src/auth.ts` — the owner-token check + static-bearer hook (the auth seam)
- `src/consent.ts` — the `/authorize` paste-to-approve consent screen
- `src/github.ts` — GitHub Contents fetcher, ETag + 60s TTL cache,
  dev-fixture fallback
- `src/server.ts` — the domain registry
- `src/domains/finance.ts` — the 14 finance tools + the `finance://` resources
- `src/types.ts` — `Env` + the `summary.json` schema
- `dev/fixture-summary.json` — the demo household's snapshot (synthetic)
- `dev/fixture-vault.json` — demo thesis/facts/report documents for the
  resources in fixture mode (synthetic)
