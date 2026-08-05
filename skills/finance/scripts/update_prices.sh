#!/usr/bin/env bash
# Refresh market prices for every commodity tagged with price: "USD:<source>"
# metadata (e.g. price: "USD:yahoo/VTSAX") and append today's directives to
# ledger/prices.beancount. Run before reviews or a dashboard session so
# holdings show market value, not last-snapshot value.
#   scripts/update_prices.sh [--vault <dir>]
set -euo pipefail

VAULT="${FINANCE_VAULT:-}"
[ "${1:-}" = "--vault" ] && { VAULT="$2"; }
if [ -z "$VAULT" ] && [ -f "$HOME/.finance-vault" ]; then
  VAULT="$(head -1 "$HOME/.finance-vault")"
fi
VAULT="${VAULT:-$HOME/Finance}"

BP="$VAULT/.venv/bin/bean-price"
PRICES="$VAULT/ledger/prices.beancount"
[ -f "$PRICES" ] || { echo "❌ no prices file at $PRICES" >&2; exit 1; }
[ -x "$BP" ] || { echo "❌ beanprice not installed — $VAULT/.venv/bin/pip install beanprice" >&2; exit 1; }

TODAY=$(date +%F)
SOURCES=$(grep -ho 'price: "[^"]*"' "$VAULT"/ledger/*.beancount | sed 's/price: "\(.*\)"/\1/' | sort -u)
[ -n "$SOURCES" ] || { echo "no price: metadata on any commodity — tag them first (e.g. price: \"USD:yahoo/VTSAX\")" >&2; exit 1; }

ADDED=0
for src in $SOURCES; do
  line=$("$BP" -e "$src" 2>/dev/null | head -1) || true
  if [ -z "$line" ]; then echo "⚠ no quote from $src" >&2; continue; fi
  # one price per commodity per day — skip if today's already recorded
  comm=$(echo "$line" | awk '{print $3}')
  if grep -q "^$TODAY price $comm " "$PRICES"; then continue; fi
  echo "$line" >> "$PRICES"
  echo "  $line"
  ADDED=$((ADDED+1))
done
"$VAULT/.venv/bin/bean-check" "$VAULT/ledger/main.beancount"
echo "✓ $ADDED price(s) appended to ledger/prices.beancount (bean-check clean)"
