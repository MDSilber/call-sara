#!/usr/bin/env bash
# E2E harness: copy a built vault, plant uncategorized transactions for the
# teach flows (one lone merchant for the chip teach, one merchant twice for
# bulk teach), point the Plaid lane at the offline fixture seam, and serve
# Sara App on a scratch port. Never touches the source vault; the copy
# leans on its .venv (bean-query/bean-check).
#   SARA_E2E_VAULT=/path/to/vault (init_vault.sh --demo makes a rich one)
set -euo pipefail
SRC="${SARA_E2E_VAULT:?set SARA_E2E_VAULT to a built vault (init_vault.sh --demo makes one)}"
PORT="${SARA_E2E_PORT:-8793}"
WORK="${TMPDIR:-/tmp}/sara-e2e-vault"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
rm -rf "$WORK"
mkdir -p "$WORK"
( cd "$SRC" && tar cf - --exclude .venv --exclude .git . ) | ( cd "$WORK" && tar xf - )
ln -s "$SRC/.venv" "$WORK/.venv"

CARD="$(grep -rhoE '^[0-9]{4}-[0-9]{2}-[0-9]{2} open (Liabilities:[A-Za-z0-9:]+)[[:space:]]+USD' "$WORK/ledger" | head -1 | awk '{print $3}')"
[ -n "$CARD" ] || { echo "no USD liability account found to plant against" >&2; exit 1; }
LEDGER_FILE="$(ls "$WORK"/ledger/2*.beancount 2>/dev/null | tail -1 || true)"
[ -n "$LEDGER_FILE" ] || LEDGER_FILE="$WORK/ledger/main.beancount"
TODAY="$(date +%F)"
cat >> "$LEDGER_FILE" <<PLANT

$TODAY * "PLANTED COFFEE 042" ""
  $CARD   -6.75 USD
  Expenses:Uncategorized

$TODAY * "PLANTED BAGEL 101" ""
  $CARD   -4.25 USD
  Expenses:Uncategorized

$TODAY * "PLANTED BAGEL 205" ""
  $CARD   -5.10 USD
  Expenses:Uncategorized

$TODAY * "PLANTED JUICE 007" ""
  $CARD   -7.25 USD
  Expenses:Uncategorized

$TODAY * "PLANTED SODA 011" ""
  $CARD   -2.50 USD
  Expenses:Uncategorized

$TODAY * "PLANTED SODA 022" ""
  $CARD   -3.10 USD
  Expenses:Uncategorized

$TODAY * "Zelle to Alicia Weiss" ""
  $CARD   -150.00 USD
  Expenses:Uncategorized
PLANT

# The Connections room needs one configured item: the demo alias, routed at
# the template's Chase accounts, token present (fixture seam, no network).
if ! grep -q "sources.plaid.items.demo" "$WORK/rules.toml"; then
cat >> "$WORK/rules.toml" <<'PLAID'

[sources.plaid.items.demo]
access_token_env = "PLAID_DEMO_ACCESS_TOKEN"
products = ["transactions"]
[sources.plaid.items.demo.accounts]
checking = "Assets:US:Chase:Checking4321"
card = "Liabilities:US:Chase:Card5678"
PLAID
fi
mkdir -p "$WORK/.secrets"
if [ ! -f "$WORK/.secrets/plaid.env" ]; then
cat > "$WORK/.secrets/plaid.env" <<'ENVEOF'
PLAID_CLIENT_ID=demo-client
PLAID_SECRET=demo-secret
PLAID_DEMO_ACCESS_TOKEN=access-demo-offline
ENVEOF
fi

# The v2 server reads summary.json + analytics.duckdb — materialize the
# planted rows the same way the write side would.
export FINANCE_VAULT="$WORK"
"$SRC/.venv/bin/python" -m sara.analytics >/dev/null
"$SRC/.venv/bin/python" -m sara.advisor.summary >/dev/null

# Plaid "sync now" rides the offline fixture seam (alias `demo` maps to
# tests/fixtures/demo.sync.json) — a real pipeline run, no network.
export SARA_PLAID_FIXTURE="$REPO/app/e2e/fixtures"

exec "$SRC/.venv/bin/python" -m sara.server --port "$PORT"
