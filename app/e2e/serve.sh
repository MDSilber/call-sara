#!/usr/bin/env bash
# E2E harness: copy a built vault, plant one uncategorized transaction for
# the categorize flow, and serve Sara App on a scratch port. Never touches
# the source vault; the copy leans on its .venv (bean-query/bean-check).
#   SARA_E2E_VAULT=/path/to/vault (init_vault.sh --demo makes a rich one)
set -euo pipefail
SRC="${SARA_E2E_VAULT:?set SARA_E2E_VAULT to a built vault (init_vault.sh --demo makes one)}"
PORT="${SARA_E2E_PORT:-8793}"
WORK="${TMPDIR:-/tmp}/sara-e2e-vault"
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
PLANT

exec env FINANCE_VAULT="$WORK" "$SRC/.venv/bin/python" -m sara.server --port "$PORT"
