# Sara App — the frontend

The local web app behind `dashboard.sh --app`. React + TypeScript + Vite +
ECharts, wearing the same aurora design system as Sara Home (the token
sheet in `src/styles/tokens.css` is ported from `tools/home.py` — one
system, two surfaces).

**Users never build this.** `npm run build` writes straight into
`skills/finance/sara/sara/server/static/`, and those built files are
committed, so `pip install`ing the sara package ships the finished page.
You only need node if you're changing the frontend itself.

## The contract

Money is ALWAYS a preformatted display string from the server (true minus,
whole dollars, ≈ on estimates, every figure window-labelled). The frontend
renders strings and never does money math. Plain numbers cross the wire
only as chart geometry — and the y-axis labels for those charts are
precomputed server-side too. If you find yourself calling `toFixed` on a
dollar, stop; the fix belongs in `sara/server/assemble.py`.

## Developing

```bash
# terminal 1 — the backend on a demo vault, with the dev origin allowed
skills/finance/scripts/init_vault.sh --demo /tmp/demo-vault   # once
SARA_DEV_ORIGIN=http://localhost:5173 FINANCE_VAULT=/tmp/demo-vault \
  /tmp/demo-vault/.venv/bin/python -m sara.server

# terminal 2 — vite with /api proxied to it
cd app && npm install && npm run dev
```

`SARA_DEV_ORIGIN` lets the vite origin through the write-token check —
dev only, never set it otherwise.

## Shipping a change

```bash
npm run build        # tsc -b && vite build -> ../skills/.../server/static/
npm run lint         # eslint, zero warnings expected
```

Commit the regenerated `static/` output together with the source change.

## E2E

```bash
SARA_E2E_VAULT=/tmp/demo-vault npm run e2e
```

`e2e/serve.sh` copies the vault, plants one uncategorized transaction, and
serves on 8793; the suite loads every room, teaches a rule end-to-end
(rules.toml + recategorize + bean-check, for real), dismisses a finding,
and checks the phone viewport. Playwright drives the SYSTEM Chrome
(`channel: 'chrome'`) deliberately — no browser downloads; if you touch
playwright deps, install with `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1`.
`e2e/look.spec.ts` is the screenshot pass (`SARA_E2E_URL=... SARA_SHOTS=...`)
for eyeballing every room in light + dark.
