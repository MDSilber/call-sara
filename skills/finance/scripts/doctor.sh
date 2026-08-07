#!/usr/bin/env bash
# doctor.sh — diagnose a finance-skill install when things misbehave.
# Usage: doctor.sh [--vault <dir>]   (default vault: $FINANCE_VAULT or ~/Finance)
# Prints PASS/WARN/FAIL per check with a one-line fix; exits non-zero on any FAIL.
set -uo pipefail   # no -e: a failing check must report, not abort the doctor

HERE="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$(cd "$HERE/.." && pwd)"
VAULT="${FINANCE_VAULT:-}"
if [ -z "$VAULT" ] && [ -f "$HOME/.finance-vault" ]; then
  P="$(head -1 "$HOME/.finance-vault")"
  # Trust the pointer only if its target is a vault this user owns and
  # nobody else can rewrite (guards against a planted/hijacked pointer).
  if [ -d "$P" ] && [ -O "$P" ] && [ -f "$P/ledger/main.beancount" ] \
     && [ -z "$(find "$P" -maxdepth 0 -perm -0002 2>/dev/null)" ]; then
    VAULT="$P"
  else
    echo "ignoring ~/.finance-vault (failed validation)" >&2
  fi
fi
VAULT="${VAULT:-$HOME/Finance}"
if [ "${1:-}" = "--vault" ]; then
  [ -n "${2:-}" ] || { echo "usage: doctor.sh [--vault <dir>]" >&2; exit 2; }
  VAULT="$2"
elif [ -n "${1:-}" ]; then
  echo "usage: doctor.sh [--vault <dir>]" >&2; exit 2
fi

FAILS=0
CORE_OK=0
pass() { printf 'PASS  %s\n' "$1"; }
warn() { printf 'WARN  %s\n      %s\n' "$1" "$2"; }
fail() { printf 'FAIL  %s\n      fix: %s\n' "$1" "$2"; FAILS=$((FAILS + 1)); }

echo "finance doctor — skill at $SKILL_DIR"
echo "                 vault at $VAULT"
echo

# --- host prerequisites --------------------------------------------------
if command -v git >/dev/null 2>&1; then
  pass "git installed ($(git --version | awk '{print $3}'))"
else
  fail "git not found" "install Xcode command-line tools or  brew install git"
fi

if command -v python3 >/dev/null 2>&1; then
  PYV="$(python3 -V 2>&1 | awk '{print $2}')"
  if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    pass "python3 $PYV (>= 3.11)"
  else
    fail "python3 is $PYV — the tools need 3.11+ (tomllib)" "brew install python@3.12"
  fi
else
  fail "python3 not found" "brew install python@3.12"
fi

if command -v gitleaks >/dev/null 2>&1; then
  pass "gitleaks installed (the vault's pre-commit secret scanner)"
else
  fail "gitleaks not found — vault commits are blocked without it" "brew install gitleaks"
fi

if command -v pdftotext >/dev/null 2>&1; then
  pass "pdftotext installed (PDF statement ingestion)"
else
  warn "pdftotext not found — only needed for PDF ingestion" "brew install poppler"
fi

# --- skill wiring --------------------------------------------------------
SKILLS_HOME="$HOME/.claude/skills"
LINKED=""
if [ -d "$SKILLS_HOME" ]; then
  for entry in "$SKILLS_HOME"/*; do
    [ -e "$entry" ] || continue
    target="$(cd "$entry" 2>/dev/null && pwd -P || true)"
    if [ "$target" = "$SKILL_DIR" ]; then LINKED="$entry"; break; fi
  done
fi
if [ -n "$LINKED" ]; then
  pass "skill linked: $LINKED -> this repo"
else
  fail "no entry in $SKILLS_HOME resolves to this repo's skill" \
       "ln -s $SKILL_DIR $SKILLS_HOME/finance"
fi

# --- the vault -----------------------------------------------------------
if [ -f "$VAULT/ledger/main.beancount" ] && [ -f "$VAULT/CLAUDE.md" ] && [ -f "$VAULT/THESIS.md" ]; then
  pass "vault found (CLAUDE.md, THESIS.md, ledger/main.beancount present)"

  VENV_PY="$VAULT/.venv/bin/python"
  if [ -x "$VENV_PY" ] && "$VENV_PY" -c 'import beancount, beanquery' >/dev/null 2>&1; then
    pass "vault .venv has beancount + beanquery"
    if "$VENV_PY" -c 'import beanprice' >/dev/null 2>&1; then
      pass "vault .venv has beanprice (update_prices.sh)"
    else
      warn "beanprice not importable — update_prices.sh cannot fetch quotes" \
           "$VAULT/.venv/bin/pip install beanprice"
    fi
    if "$VENV_PY" -c 'import fava' >/dev/null 2>&1; then
      pass "vault .venv has fava (dashboard.sh)"
      if "$VENV_PY" -c 'import fava_dashboards, fava_investor' >/dev/null 2>&1; then
        pass "vault .venv has fava_dashboards + fava_investor (dashboard panels)"
      else
        warn "fava_dashboards / fava_investor not importable — the Dashboards and Investor pages in fava will error" \
             "$VAULT/.venv/bin/pip install fava-dashboards fava-investor"
      fi
      if [ ! -f "$VAULT/dashboards.yaml" ]; then
        warn "no dashboards.yaml in the vault — fava's Dashboards page has nothing to render" \
             "cp $SKILL_DIR/vault-template/dashboards.yaml $VAULT/dashboards.yaml"
      fi
    else
      warn "fava not importable — dashboard.sh cannot launch" \
           "$VAULT/.venv/bin/pip install fava"
    fi
    if OUT="$(cd "$VAULT" && ./.venv/bin/bean-check ledger/main.beancount 2>&1)"; then
      pass "bean-check: ledger validates"
      CORE_OK=1
    else
      fail "bean-check reports errors in the ledger" \
           "run  $VAULT/.venv/bin/bean-check $VAULT/ledger/main.beancount  and fix the lines it names"
      printf '%s\n' "$OUT" | head -5 | sed 's/^/      | /'
    fi
  else
    fail "vault .venv missing or beancount/beanquery not importable" \
         "python3 -m venv $VAULT/.venv && $VAULT/.venv/bin/pip install beancount beanquery"
  fi

  if "$VENV_PY" -c 'import sara, plaid' >/dev/null 2>&1; then
    pass "vault .venv has the sara package + plaid-python (importers, ingest daemon)"
  else
    warn "sara package not importable from the vault venv — importers/ingest will ask for this one-time install" \
         "$VAULT/.venv/bin/pip install -e $SKILL_DIR/sara"
  fi

  # --- Plaid feed freshness (mirrors checks.py plaid_freshness) ----------
  if grep -q '^\[sources\.plaid\.items\.' "$VAULT/rules.toml" 2>/dev/null; then
    if [ -d "$VAULT/.secrets" ] && [ -z "$(find "$VAULT/.secrets" -maxdepth 0 -perm -0077 2>/dev/null)" ]; then
      pass "vault .secrets/ exists and is owner-only (Plaid keys + cursors)"
    else
      warn "vault .secrets/ missing or too permissive" \
           "mkdir -p $VAULT/.secrets && chmod 700 $VAULT/.secrets && chmod 600 $VAULT/.secrets/* 2>/dev/null"
    fi
    if git -C "$VAULT" check-ignore -q .secrets 2>/dev/null; then
      pass "vault .gitignore covers .secrets/ (credentials can never commit)"
    else
      fail "vault .gitignore does NOT cover .secrets/ — Plaid tokens could reach git" \
           "printf '.secrets/\\n' >> $VAULT/.gitignore"
    fi
    CURSORS="$VAULT/.secrets/plaid-cursors.json"
    if [ -f "$CURSORS" ]; then
      STALE_D=$(python3 - "$CURSORS" <<'PYFRESH'
import json, sys
from datetime import datetime
try:
    items = json.load(open(sys.argv[1])).get("items", {})
    ages = [(datetime.now() - datetime.fromisoformat(v["last_synced"])).days
            for v in items.values() if v.get("last_synced")]
    print(max(ages) if ages else 9999)
except Exception:
    print(9999)
PYFRESH
)
      if [ "${STALE_D:-9999}" -le 3 ]; then
        pass "plaid sync fresh (oldest item synced ${STALE_D}d ago)"
      else
        warn "plaid sync stale — oldest configured item last synced ${STALE_D}d ago (watch >3d, alert >7d)" \
             "tools/run ingest.py  (then --write); a broken link repairs FREE: python -m sara.link --repair <alias>"
      fi
    else
      warn "plaid items configured but never synced (no cursor file yet)" \
           "finish the trust ramp: tools/run ingest.py, read the report, then --write"
    fi
  fi

  if [ -d "$VAULT/inbox" ]; then
    STALE=$(find "$VAULT/inbox" -maxdepth 1 -type f -mtime +7 ! -name '.*' 2>/dev/null | wc -l | tr -d ' ')
    if [ "${STALE:-0}" -gt 0 ]; then
      warn "$STALE document(s) sitting in inbox/ for over 7 days" \
           "tools/run inbox.py identifies and files them — nothing should rot in the inbox"
    else
      pass "inbox/ draining (nothing older than 7 days)"
    fi
  fi

  if [ "$(git -C "$VAULT" config core.hooksPath 2>/dev/null)" = ".githooks" ]; then
    pass "vault git hooks armed (core.hooksPath = .githooks)"
  else
    fail "vault git hooks NOT armed — commits bypass the secret scanner" \
         "git -C $VAULT config core.hooksPath .githooks"
  fi
else
  fail "no vault at $VAULT (need CLAUDE.md, THESIS.md, ledger/main.beancount)" \
       "run  $HERE/init_vault.sh $VAULT  — or point me at it:  doctor.sh --vault <dir>"
  echo "      (skipped: venv, bean-check, git hooks — no vault to check)"
fi

# --- layers — how far this install goes ----------------------------------
# One compact map of the optional layers (onboarding.md § Optional layers),
# so anyone can see exactly where they stopped. Informational, never a FAIL.
if [ -f "$VAULT/ledger/main.beancount" ]; then
  echo
  echo "layers — how far this install goes:"
  lay() { printf '  %-7s %s\n' "$1" "$2"; }

  if [ "$CORE_OK" = 1 ]; then
    lay core "✓ vault + ledger validate"
  else
    lay core "incomplete — fixes above"
  fi

  N_ITEMS="$(grep -c '^\[sources\.plaid\.items\.' "$VAULT/rules.toml" 2>/dev/null || true)"
  if [ "${N_ITEMS:-0}" -gt 0 ] 2>/dev/null; then
    S=s; [ "$N_ITEMS" = 1 ] && S=""
    if [ -n "${STALE_D:-}" ] && [ "$STALE_D" -lt 9999 ] 2>/dev/null; then
      lay plaid "$N_ITEMS item$S linked · last sync ${STALE_D}d ago"
    else
      lay plaid "$N_ITEMS item$S linked · never synced — tools/run ingest.py, then --write"
    fi
  else
    lay plaid "not set up — references/fetching.md § The Plaid lane"
  fi

  if [ -x "${VENV_PY:-}" ] && "$VENV_PY" -c 'import fastapi, uvicorn, sara.server' >/dev/null 2>&1; then
    lay app "installed — scripts/dashboard.sh --app"
  else
    lay app "not installed — first scripts/dashboard.sh --app installs it"
  fi

  if [ -f "$VAULT/reports/digest.html" ]; then
    lay digest "generating (last: $(date -r "$VAULT/reports/digest.html" +%Y-%m-%d 2>/dev/null || echo '?'))"
  else
    lay digest "never generated — scripts/dashboard.sh --digest"
  fi

  if [ -d "$VAULT/inbox" ]; then
    W="$(find "$VAULT/inbox" -maxdepth 1 -type f ! -name '.*' 2>/dev/null | wc -l | tr -d ' ')"
    lay inbox "present · ${W:-0} waiting"
  else
    lay inbox "missing — mkdir $VAULT/inbox"
  fi

  if [ -f "$VAULT/reports/summary.json" ]; then
    lay worker "n/a locally (phone layer) — summary.json present to serve"
  else
    lay worker "n/a locally (phone layer) — tools/run reports.py writes summary.json"
  fi

  if [ -f "$VAULT/ONBOARDING.md" ]; then
    echo "  (ONBOARDING.md present — onboarding or a layer is mid-flight;"
    echo "   Sara resumes at its first unchecked box)"
  fi
fi

echo
if [ "$FAILS" -gt 0 ]; then
  echo "❌ $FAILS check(s) failed — fixes above, top to bottom."
  exit 1
fi
echo "✓ all checks passed"
