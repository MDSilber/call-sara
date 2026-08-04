#!/usr/bin/env python3
"""Import a bank or credit-card OFX/QFX export into Beancount transactions.

Usage:
  ofx.py <file.qfx> [ledger-account] [--all]

The ledger account is looked up from the file's <ACCTID> via rules.toml
[[accounts]] (matched on last-4); pass it explicitly if there's no match.
Each transaction's counter-account comes from rules.toml [[payee_rules]];
unmatched debits land in Expenses:Uncategorized (the review queue).

By default a transaction is skipped when the ledger already has one with the
same amount and payee within ±5 days (transaction vs post dates differ across
export formats); rows inside this file are compared exactly. Every skip is
listed on stderr; --all disables dedupe entirely.
Prints Beancount to stdout — review, then append to ledger/<year>.beancount.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules import categorize, route_by_acctid  # noqa: E402
from importers.common import (bank_statements, bank_transactions, emit,  # noqa: E402
                              existing_index, is_duplicate, read_ofx, seen_in_file)


def main():
    args = [a for a in sys.argv[1:] if a != "--all"]
    dedupe = "--all" not in sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    path = args[0]
    statements = list(bank_statements(read_ofx(path)))
    if len(args) > 1 and len(statements) > 1:
        sys.exit(f"{path} holds {len(statements)} accounts — an explicit ledger account "
                 f"can't apply to all of them. Add [[accounts]] routing entries instead.")
    for acct_id, text in statements:
        account = args[1] if len(args) > 1 else route_by_acctid(acct_id)
        if not account:
            print(f"; skipping account ending {acct_id[-4:]!r}: no [[accounts]] entry in "
                  f"rules.toml — add one (or pass the ledger account explicitly)", file=sys.stderr)
            continue
        idx = existing_index(account) if dedupe else {}
        fileset = set()
        txns = sorted(bank_transactions(text), key=lambda t: t["date"])
        kept = 0
        skipped = []
        for t in txns:
            if dedupe and (seen_in_file(fileset, t["date"], t["amount"], t["payee"])
                           or is_duplicate(idx, t["date"], t["amount"], t["payee"])):
                skipped.append(t)
                continue
            counter = categorize(t["payee"], t["type"], t["amount"], account)
            emit(t["date"], t["payee"], {"ofx-type": t["type"]}, account, t["amount"], counter)
            kept += 1
        print(f"; imported {kept} transactions to {account}"
              + (f" (skipped {len(skipped)} already in ledger)" if skipped else ""), file=sys.stderr)
        for t in skipped:
            print(f";   skipped {t['date']} {t['amount']:.2f} {t['payee']}", file=sys.stderr)


if __name__ == "__main__":
    main()
