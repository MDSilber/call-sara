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
    else
      warn "fava not importable — dashboard.sh cannot launch" \
           "$VAULT/.venv/bin/pip install fava"
    fi
    if OUT="$(cd "$VAULT" && ./.venv/bin/bean-check ledger/main.beancount 2>&1)"; then
      pass "bean-check: ledger validates"
    else
      fail "bean-check reports errors in the ledger" \
           "run  $VAULT/.venv/bin/bean-check $VAULT/ledger/main.beancount  and fix the lines it names"
      printf '%s\n' "$OUT" | head -5 | sed 's/^/      | /'
    fi
  else
    fail "vault .venv missing or beancount/beanquery not importable" \
         "python3 -m venv $VAULT/.venv && $VAULT/.venv/bin/pip install beancount beanquery"
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

echo
if [ "$FAILS" -gt 0 ]; then
  echo "❌ $FAILS check(s) failed — fixes above, top to bottom."
  exit 1
fi
echo "✓ all checks passed"
