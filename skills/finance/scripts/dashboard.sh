#!/usr/bin/env bash
# Open the vault's visual surfaces. LOCAL-ONLY, always: everything binds to
# 127.0.0.1 or is a plain file on disk; nothing leaves the machine.
#
#   scripts/dashboard.sh [--vault <dir>] [--port <n>] [mode]
#
# Modes:
#   (default)  SARA APP — the interactive local web app and daily driver
#              (FastAPI + prebuilt frontend, python -m sara.server). Fixed
#              default port 8787 (the server validates Host and gates every
#              write behind a per-launch token, so a guessable URL exposes
#              nothing); --port overrides.
#   --home     the one-viewport PRINT GLANCE (reports/home.html, via
#              sara.advisor.home) — a self-contained sheet you can print or mail.
#   --digest   Sara's weekly letter (reports/digest.html + digest.txt, via
#              tools/digest.py) — the email-shaped 20-second read.
#   --fava     the nerd drill-down (fava): raw ledger, query console.
#              Read-only by default — --writable re-enables fava's editor
#              for a session that needs it; port randomized (41000-49000)
#              unless --port pins it. While a server runs, ANY local page
#              could try to reach it — Ctrl-C when done.
#              (The old --pretty static brief retired 2026-08-07; the app
#              is the daily driver and --home the printable artifact.)
#
# fava-dashboards caveat: the Dashboards page renders every panel through a
# POST endpoint, which fava's read-only mode rejects (verified: the page
# shell loads, each panel errors 401). Read-only stays the UNCONDITIONAL
# default anyway — a writable fava exposes ledger-editing endpoints to any
# local page while it runs. When a dashboards config is present this script
# prints a hint; opt into the Dashboards tab per-session with --writable.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
VAULT="${FINANCE_VAULT:-}"
PORT=""
WRITABLE=0
HOME_PAGE=0
DIGEST=0
FAVA=0
APP=1
while [ $# -gt 0 ]; do
  case "$1" in
    --vault) VAULT="$2"; shift 2 ;;
    --port)  PORT="$2";  shift 2 ;;
    --writable) WRITABLE=1; shift ;;
    --home) HOME_PAGE=1; APP=0; shift ;;
    --digest) DIGEST=1; APP=0; shift ;;
    --fava) FAVA=1; APP=0; shift ;;
    --app) APP=1; shift ;;
    *) echo "usage: dashboard.sh [--vault <dir>] [--port <n>] [--app | --home | --digest | --fava [--writable]]" >&2; exit 1 ;;
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

# --app: Sara App — the interactive local web app. Reads assemble the same
# verified builders the static pages use; the three write actions (teach a
# categorization rule, set a goal, dismiss a finding) run through the same
# gated tools a session would use. 127.0.0.1 only.
if [ "$APP" = 1 ]; then
  PY="$VAULT/.venv/bin/python"
  [ -x "$PY" ] || { echo "❌ no vault venv at $PY — is the vault set up?" >&2; exit 1; }
  if ! "$PY" -c 'import fastapi, uvicorn' >/dev/null 2>&1; then
    echo "installing the app server into this vault's venv (one time)…"
    "$VAULT/.venv/bin/pip" install -q -e "$HERE/../sara[app]" || {
      echo "❌ install failed. If your pip index is broken, retry:" >&2
      echo "   PIP_INDEX_URL=https://pypi.org/simple $VAULT/.venv/bin/pip install -e '$HERE/../sara[app]'" >&2
      exit 1
    }
  fi
  [ -n "$PORT" ] || PORT=8787
  command -v open >/dev/null && ( sleep 1; open "http://127.0.0.1:$PORT/" ) &
  exec env FINANCE_VAULT="$VAULT" "$PY" -m sara.server --port "$PORT"
fi

# --home / --digest: the static views — generate the page and open it as a
# plain file. No server, no port, nothing to Ctrl-C.
if [ "$HOME_PAGE" = 1 ] || [ "$DIGEST" = 1 ]; then
  PY="$VAULT/.venv/bin/python"
  [ -x "$PY" ] || { echo "❌ no vault venv at $PY — is the vault set up?" >&2; exit 1; }
  if [ "$DIGEST" = 1 ]; then
    FINANCE_VAULT="$VAULT" "$PY" "$HERE/../tools/digest.py"
    PAGE="$VAULT/reports/digest.html"
    echo "✓ weekly letter at $PAGE  (text twin: $VAULT/reports/digest.txt — delivery is yours: email, text, print)"
  else
    FINANCE_VAULT="$VAULT" "$PY" -m sara.advisor.home
    PAGE="$VAULT/reports/home.html"
    echo "✓ print glance at $PAGE  (one self-contained sheet; the app is the live view, --fava the drill-down)"
  fi
  command -v open >/dev/null && open "$PAGE"
  exit 0
fi

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
# fava-dashboards renders panels via POST, which --read-only rejects (401 on
# every panel), so the Dashboards tab only works under --writable. Read-only
# remains the unconditional default — never auto-dropped — because a writable
# fava exposes ledger-editing endpoints to any local page while it runs.
if [ "$WRITABLE" != 1 ] && [ -f "$VAULT/dashboards.yaml" ] \
   && "$VAULT/.venv/bin/python" -c 'import fava_dashboards' >/dev/null 2>&1; then
  echo "ℹ️  dashboards.yaml detected — rerun with --writable to enable the Dashboards tab"
  echo "   (fava-dashboards renders panels via POST, which read-only mode blocks; all other pages work read-only)"
fi
if [ "$WRITABLE" = 1 ]; then
  MODE_ARGS=()
  MODE_NOTE="WRITABLE"
  echo "⚠️  --writable: the dashboard can EDIT the ledger while it runs — any local" >&2
  echo "   browser page could reach it. Prefer the default read-only mode; Ctrl-C as soon as you're done." >&2
fi
echo "✓ dashboard on http://127.0.0.1:$PORT  ($MODE_NOTE, local only — Ctrl-C to stop)"
echo "  Close the dashboard tab when you're done: the page never leaves this machine,"
echo "  but while the server runs, any page open in a local browser could reach it."
command -v open >/dev/null && ( sleep 1; open "http://127.0.0.1:$PORT/?time=year" ) &  # default window: this year — unfiltered all-time sums read as period figures
exec "$FAVA" "${MODE_ARGS[@]+"${MODE_ARGS[@]}"}" --host 127.0.0.1 --port "$PORT" "$LEDGER"
