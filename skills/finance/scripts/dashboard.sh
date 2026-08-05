#!/usr/bin/env bash
# Launch the vault's visual dashboard (fava) — net worth, spending drill-downs,
# a query console — as a LOCAL-ONLY web page. Nothing leaves the machine:
# fava binds to 127.0.0.1 and reads the ledger straight off disk.
#   scripts/dashboard.sh [--vault <dir>] [--port <n>]
set -euo pipefail

VAULT="${FINANCE_VAULT:-}"
PORT=5000
while [ $# -gt 0 ]; do
  case "$1" in
    --vault) VAULT="$2"; shift 2 ;;
    --port)  PORT="$2";  shift 2 ;;
    *) echo "usage: dashboard.sh [--vault <dir>] [--port <n>]" >&2; exit 1 ;;
  esac
done
if [ -z "$VAULT" ] && [ -f "$HOME/.finance-vault" ]; then
  VAULT="$(head -1 "$HOME/.finance-vault")"
fi
VAULT="${VAULT:-$HOME/Finance}"

FAVA="$VAULT/.venv/bin/fava"
LEDGER="$VAULT/ledger/main.beancount"
[ -f "$LEDGER" ] || { echo "❌ no ledger at $LEDGER — is the vault set up?" >&2; exit 1; }
if [ ! -x "$FAVA" ]; then
  echo "fava isn't installed in this vault yet — one-time install:" >&2
  "$VAULT/.venv/bin/pip" install -q fava || {
    echo "❌ install failed. If your pip index is broken, retry:" >&2
    echo "   PIP_INDEX_URL=https://pypi.org/simple $VAULT/.venv/bin/pip install fava" >&2
    exit 1
  }
fi

echo "✓ dashboard on http://127.0.0.1:$PORT  (local only — Ctrl-C to stop)"
exec "$FAVA" --host 127.0.0.1 --port "$PORT" "$LEDGER"
