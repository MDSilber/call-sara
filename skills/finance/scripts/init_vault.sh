#!/usr/bin/env bash
# init_vault.sh — scaffold a fresh household finance vault from vault-template/.
# Usage: init_vault.sh [path]            (default: $FINANCE_VAULT or ~/Finance)
#        init_vault.sh --demo <path>     (path REQUIRED — see below)
#   --demo   seed the vault with the fictional Demo household from
#            vault-template-demo/ — ~6 months of synthetic history, a filled
#            thesis, and realistic rules. Safe to delete; never real data.
#            Requires an explicit path so demo data can never land in the
#            default real-vault location.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$(cd "$HERE/../vault-template" && pwd)"
DEMO=0
if [ "${1:-}" = "--demo" ]; then
  DEMO=1
  shift
  if [ -z "${1:-}" ]; then
    echo "❌ --demo needs an explicit directory, e.g.:  init_vault.sh --demo ~/finance-demo" >&2
    echo "   (refusing to seed synthetic data into the default real-vault location)" >&2
    exit 1
  fi
fi
VAULT="${1:-${FINANCE_VAULT:-$HOME/Finance}}"

# --- preflight: fail BEFORE writing anything ---------------------------
if [ -e "$VAULT/CLAUDE.md" ] || [ -e "$VAULT/ledger/main.beancount" ]; then
  echo "❌ A vault already exists at $VAULT. Refusing to overwrite." >&2
  exit 1
fi
missing=()
command -v git      >/dev/null 2>&1 || missing+=("git")
command -v python3  >/dev/null 2>&1 || missing+=("python3 3.11+  (brew install python@3.12)")
command -v gitleaks >/dev/null 2>&1 || missing+=("gitleaks   (brew install gitleaks)")
if [ ${#missing[@]} -gt 0 ]; then
  echo "❌ install these first, then re-run:" >&2
  printf '   - %s\n' "${missing[@]}" >&2
  echo "   gitleaks is required: the vault's pre-commit hook refuses to run without it." >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
  echo "❌ python3 is $(python3 -V 2>&1 | awk '{print $2}') — the tools need 3.11+ (tomllib)." >&2
  echo "   brew install python@3.12, then re-run." >&2
  exit 1
}
command -v pdftotext >/dev/null 2>&1 || \
  echo "⚠️  pdftotext not found (brew install poppler) — only needed for PDF ingestion; continuing."
git config user.email >/dev/null 2>&1 || {
  echo "❌ git has no user.email configured (needed for the first commit):" >&2
  echo '   git config --global user.name "Your Name" && git config --global user.email you@example.com' >&2
  exit 1
}

# --- scaffold ------------------------------------------------------------
mkdir -p "$VAULT"
cp -R "$TEMPLATE"/. "$VAULT"/
mkdir -p "$VAULT"/{documents,inbox,notes,reports}
find "$VAULT" -name .gitkeep -delete
chmod +x "$VAULT/.githooks/pre-commit"

# --- demo seed: overlay the fictional Demo household ---------------------
if [ "$DEMO" = 1 ]; then
  DEMO_TEMPLATE="$(cd "$HERE/../vault-template-demo" && pwd)"
  cp -R "$DEMO_TEMPLATE"/. "$VAULT"/
  echo 'include "2026.beancount"' >> "$VAULT/ledger/main.beancount"
  BANNER='> **⚠️ DEMO VAULT — 100% synthetic data.** The Demo household (Alex, Jordan,
> Riley) is fictional; every account, balance, and transaction here was
> generated as sample data. Explore freely, delete the whole directory when
> done. Create a real vault with `init_vault.sh` (no `--demo`).'
  printf '%s\n\n%s' "$BANNER" "$(cat "$VAULT/CLAUDE.md")" > "$VAULT/CLAUDE.md"
fi
cd "$VAULT"

# Python env with beancount + beanquery (the tools run under this venv).
# Uses whatever package index this machine is configured for; if that index
# can't serve the packages, fix or request them there — no side-channel install.
python3 -m venv .venv
./.venv/bin/pip install -q beancount beanquery || {
  echo "❌ beancount install failed via this machine's configured package index." >&2
  echo "   If that index is corporate or broken, retry against the public one:" >&2
  echo "   rm -rf $VAULT && PIP_INDEX_URL=https://pypi.org/simple $0 $VAULT" >&2
  echo "   (or install beancount + beanquery into $VAULT/.venv yourself, then re-run)." >&2
  exit 1
}
./.venv/bin/bean-check ledger/main.beancount
echo "✓ ledger validates"

# Demo vaults start fully furnished: generate reports/ so the first session
# reads precomputed answers instead of an empty directory.
if [ "$DEMO" = 1 ]; then
  FINANCE_VAULT="$VAULT" ./.venv/bin/python "$HERE/../tools/reports.py"
  FINANCE_VAULT="$VAULT" ./.venv/bin/python "$HERE/../tools/run_checks.py"
  echo "✓ demo reports generated"
fi

# --- remember a custom location (pointer file; env var still wins) -------
# Real vaults only: a demo vault must never capture the default lookup.
if [ "$DEMO" != 1 ] && [ "$VAULT" != "$HOME/Finance" ]; then
  printf '%s\n' "$VAULT" > "$HOME/.finance-vault"
  echo "✓ recorded vault location in ~/.finance-vault (tools find it without FINANCE_VAULT)"
fi

# --- git, with the secret scanner armed BEFORE any commit ---------------
git init -q
git config core.hooksPath .githooks
git add -A
if [ "$DEMO" = 1 ]; then
  git commit -qm "Scaffold DEMO finance vault (synthetic Demo household)"
  echo "✓ demo vault scaffolded at $VAULT — fictional data, safe to delete"
  echo
  TOOLS="$(cd "$HERE/../tools" && pwd)"
  echo "Try:  FINANCE_VAULT=$VAULT $TOOLS/run query.py networth"
  echo "      FINANCE_VAULT=$VAULT $TOOLS/run reports.py"
  echo "      or ask the finance skill to hunt for savings (there's bait in there)."
else
  git commit -qm "Scaffold household finance vault"
  echo "✓ vault scaffolded at $VAULT (secret scanner armed on every commit)"
  echo
  echo "Next: 1) create a PRIVATE remote:  gh repo create <name> --private --source=. --push"
  echo "      2) then say 'set up my finances' — the onboarding interview fills THESIS.md and facts/."
fi
