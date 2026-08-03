#!/usr/bin/env bash
# init_vault.sh — scaffold a fresh household finance vault from vault-template/.
# Usage: init_vault.sh [path]   (default: $FINANCE_VAULT or ~/Finance)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$(cd "$HERE/../vault-template" && pwd)"
VAULT="${1:-${FINANCE_VAULT:-$HOME/Finance}}"

# --- preflight: fail BEFORE writing anything ---------------------------
if [ -e "$VAULT/CLAUDE.md" ] || [ -e "$VAULT/ledger/main.beancount" ]; then
  echo "❌ A vault already exists at $VAULT. Refusing to overwrite." >&2
  exit 1
fi
missing=()
command -v git      >/dev/null 2>&1 || missing+=("git")
command -v python3  >/dev/null 2>&1 || missing+=("python3")
command -v gitleaks >/dev/null 2>&1 || missing+=("gitleaks   (brew install gitleaks)")
if [ ${#missing[@]} -gt 0 ]; then
  echo "❌ install these first, then re-run:" >&2
  printf '   - %s\n' "${missing[@]}" >&2
  echo "   gitleaks is required: the vault's pre-commit hook refuses to run without it." >&2
  exit 1
fi
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
cd "$VAULT"

# Python env with beancount + beanquery (the tools run under this venv).
# Uses whatever package index this machine is configured for; if that index
# can't serve the packages, fix or request them there — no side-channel install.
python3 -m venv .venv
./.venv/bin/pip install -q beancount beanquery || {
  echo "❌ beancount install failed via this machine's configured package index." >&2
  echo "   Make beancount + beanquery available in that index (or install them" >&2
  echo "   into $VAULT/.venv yourself), then re-run." >&2
  exit 1
}
./.venv/bin/bean-check ledger/main.beancount
echo "✓ ledger validates"

# --- git, with the secret scanner armed BEFORE any commit ---------------
git init -q
git config core.hooksPath .githooks
git add -A
git commit -qm "Scaffold household finance vault"
echo "✓ vault scaffolded at $VAULT (secret scanner armed on every commit)"
echo
echo "Next: 1) create a PRIVATE remote:  gh repo create <name> --private --source=. --push"
echo "      2) then say 'set up my finances' — the onboarding interview fills THESIS.md and facts/."
