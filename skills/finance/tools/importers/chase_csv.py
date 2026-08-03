#!/usr/bin/env python3
"""Import a Chase credit-card CSV export into Beancount transactions.

Usage:
  chase_csv.py <activity.csv> <liability-account> [--all]

Prefer this over the QFX for CARDS: the CSV carries Chase's own Category
column, mapped via rules.toml [chase_categories]. Merchant-specific
[[payee_rules]] win first (so a corrected merchant is fixed forever).
Payments to the card go to Assets:US:Transfers.

Skips transactions whose (date, amount) already exist on the account unless
--all. Prints Beancount to stdout for review.
"""
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules import EXPENSE_DEFAULT, TRANSFER_ACCOUNT, chase_category, match_rule  # noqa: E402
from importers.common import emit, escape, existing_keys, payee_key  # noqa: E402


def counter_account(desc, chase_cat, txn_type, amount, account):
    if (txn_type or "").lower() == "payment":
        return TRANSFER_ACCOUNT
    # A learned merchant rule beats Chase's classifier.
    rule = match_rule(desc, "", amount)
    if rule:
        return rule
    return chase_category(chase_cat) or EXPENSE_DEFAULT


def main():
    args = [a for a in sys.argv[1:] if a != "--all"]
    dedupe = "--all" not in sys.argv[1:]
    if len(args) != 2:
        sys.exit(__doc__)
    path, account = args
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows.sort(key=lambda r: datetime.strptime(r["Transaction Date"], "%m/%d/%Y"))
    seen = existing_keys(account) if dedupe else set()
    kept, skipped = 0, []
    for r in rows:
        date = datetime.strptime(r["Transaction Date"], "%m/%d/%Y").date()
        amount = float(r["Amount"])  # Chase: charges negative, payments/credits positive
        payee = escape(r["Description"])
        key = (date.isoformat(), round(amount, 2), payee_key(payee))
        if key in seen:
            skipped.append((date, amount, payee))
            continue
        seen.add(key)
        counter = counter_account(r["Description"], r.get("Category"), r.get("Type"), amount, account)
        emit(date, payee, {"chase-type": r.get("Type", "")}, account, amount, counter)
        kept += 1
    print(f"; imported {kept} transactions to {account}"
          + (f" (skipped {len(skipped)} already in ledger)" if skipped else ""), file=sys.stderr)
    for d, a, pz in skipped:
        print(f";   skipped {d} {a:.2f} {pz}", file=sys.stderr)


if __name__ == "__main__":
    main()
