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

BP="$VAULT/.venv/bin/bean-price"
PRICES="$VAULT/ledger/prices.beancount"
[ -f "$PRICES" ] || { echo "❌ no prices file at $PRICES" >&2; exit 1; }

TODAY=$(date +%F)
# Harvest price: tags from ACTIVE lines only — a commented example
# (';  price: "USD:yahoo/VTSAX"') is documentation, not configuration.
SOURCES=$(grep -hv '^[[:space:]]*;' "$VAULT"/ledger/*.beancount 2>/dev/null | grep -o 'price: "[^"]*"' | sed 's/price: "\(.*\)"/\1/' | sort -u) || true
if [ -z "$SOURCES" ]; then
  echo "no price: metadata on any (uncommented) commodity — nothing to refresh."
  echo "To enable quotes, tag holdings in the ledger, e.g.:"
  echo '  2020-01-01 commodity VTSAX'
  echo '    price: "USD:yahoo/VTSAX"'
  exit 0
fi
[ -x "$BP" ] || { echo "❌ beanprice not installed — $VAULT/.venv/bin/pip install beanprice" >&2; exit 1; }

# Stage the appends in a temp copy; the real file changes only via mv, and a
# failed bean-check restores the original — no corrupt line is ever left.
TMP=$(mktemp "$PRICES.new.XXXXXX")
cp "$PRICES" "$TMP"
ADDED=0
for src in $SOURCES; do
  line=$("$BP" -e "$src" 2>/dev/null | head -1) || true
  if [ -z "$line" ]; then echo "⚠ no quote from $src" >&2; continue; fi
  # one price per commodity per day — LITERAL prefix match (the '.' in a
  # ticker like BRK.B must not regex-match other commodities)
  comm=$(echo "$line" | awk '{print $3}')
  if awk -v pat="$TODAY price $comm " 'index($0, pat) == 1 {found=1} END {exit !found}' "$TMP"; then continue; fi
  echo "$line" >> "$TMP"
  echo "  $line"
  ADDED=$((ADDED+1))
done
if [ "$ADDED" = 0 ]; then
  rm -f "$TMP"
  echo "✓ no new prices to append (already current for $TODAY, or no quotes came back)"
  exit 0
fi
BAK=$(mktemp "$PRICES.bak.XXXXXX")
cp "$PRICES" "$BAK"
mv "$TMP" "$PRICES"
if ! "$VAULT/.venv/bin/bean-check" "$VAULT/ledger/main.beancount"; then
  mv "$BAK" "$PRICES"
  echo "❌ bean-check rejected the new prices — rolled back, nothing appended" >&2
  exit 1
fi
rm -f "$BAK"
echo "✓ $ADDED price(s) appended to ledger/prices.beancount (bean-check clean)"
