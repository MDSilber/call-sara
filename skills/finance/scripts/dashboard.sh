#!/usr/bin/env bash
# Launch the vault's visual dashboard (fava) — net worth, spending drill-downs,
# a query console — as a LOCAL-ONLY web page. Nothing leaves the machine:
# fava binds to 127.0.0.1 and reads the ledger straight off disk.
#
# Read-only by default: the page can VIEW everything but cannot edit the
# ledger. --writable re-enables fava's editor for a session that needs it.
# The port is randomized (41000-49000) unless --port pins it, so the URL
# isn't guessable-by-default. While the server runs, ANY page open in a
# local browser could try to reach it — close the dashboard tab when done
# and Ctrl-C the server rather than leaving it up.
#   scripts/dashboard.sh [--vault <dir>] [--port <n>] [--writable]
set -euo pipefail

VAULT="${FINANCE_VAULT:-}"
PORT=""
WRITABLE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --vault) VAULT="$2"; shift 2 ;;
    --port)  PORT="$2";  shift 2 ;;
    --writable) WRITABLE=1; shift ;;
    *) echo "usage: dashboard.sh [--vault <dir>] [--port <n>] [--writable]" >&2; exit 1 ;;
  esac
done
if [ -z "$VAULT" ] && [ -f "$HOME/.finance-vault" ]; then
  P="$(head -1 "$HOME/.finance-vault")"
  # The pointer file is only trusted when its target looks like a vault the
  # current user owns and no one else can swap out from under them.
  if [ -d "$P" ] && [ -O "$P" ] && [ -f "$P/ledger/main.beancount" ] \
     && [ -z "$(find "$P" -maxdepth 0 -perm -0002 2>/dev/null)" ]; then
    VAULT="$P"
  else
    echo "ignoring ~/.finance-vault (failed validation)" >&2
  fi
fi
VAULT="${VAULT:-$HOME/Finance}"
# Random high port by default — a fixed well-known port would let any local
# page guess the dashboard's URL even between sessions.
[ -n "$PORT" ] || PORT=$((41000 + RANDOM % 8001))

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

MODE_ARGS=(--read-only)
MODE_NOTE="read-only"
if [ "$WRITABLE" = 1 ]; then
  MODE_ARGS=()
  MODE_NOTE="WRITABLE"
  echo "⚠️  --writable: the dashboard can EDIT the ledger while it runs — any local" >&2
  echo "   browser page could reach it. Prefer the default read-only mode; Ctrl-C as soon as you're done." >&2
fi
echo "✓ dashboard on http://127.0.0.1:$PORT  ($MODE_NOTE, local only — Ctrl-C to stop)"
echo "  Close the dashboard tab when you're done: the page never leaves this machine,"
echo "  but while the server runs, any page open in a local browser could reach it."
command -v open >/dev/null && ( sleep 1; open "http://127.0.0.1:$PORT" ) &
exec "$FAVA" "${MODE_ARGS[@]+"${MODE_ARGS[@]}"}" --host 127.0.0.1 --port "$PORT" "$LEDGER"
