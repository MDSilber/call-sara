#!/usr/bin/env python3
"""Import a Chase credit-card CSV export into Beancount transactions.

Usage:
  chase_csv.py <activity.csv> <liability-account> [--write] [--all] [--allow-discrepancy] [--dry-run]

Prefer this over the QFX for CARDS: the CSV carries Chase's own Category
column, mapped via rules.toml [chase_categories]. Merchant-specific
[[payee_rules]] win first (so a corrected merchant is fixed forever).
Payments to the card go to Assets:US:Transfers; unmatched merchants land in
Expenses:Uncategorized (the review queue — imports never stall on
categorization), and rows that fail to parse are reported, not fatal.

DRY-RUN BY DEFAULT: prints the would-be entries to stdout and a summary
(dedupe skips, balance continuity) to stderr, and writes nothing. Review,
then re-run with --write to append to ledger/<year>.beancount (bean-check
runs after; a failed check rolls the append back).

Dedupe, in order: (1) hash-exact via each entry's `import-hash:` metadata —
a CSV row carries no bank FITID, so a hash hit against a fitid-bearing QFX
entry still counts as the same transaction (the cross-format case);
(2) fuzzy fallback — same amount and payee within ±5 days, applied ONLY to
legacy ledger entries that carry neither fitid nor import-hash (transaction
vs post dates differ across formats). Skips are listed on stderr; --all
disables dedupe.

Balance continuity ("Golden Rule"): when the export carries a running
Balance column, every row must chain (balance = previous + amount) — tagged
VERIFIED / DISCREPANCY / UNVERIFIABLE. A DISCREPANCY blocks the import
(exit 1, nothing written) unless --allow-discrepancy.
"""
import csv
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules import EXPENSE_DEFAULT, TRANSFER_ACCOUNT, chase_category, match_rule  # noqa: E402
from importers.common import (DISCREPANCY, VERIFIED, append_to_ledger,  # noqa: E402
                              assertion_date, check_continuity_ledger,
                              check_continuity_rows, emit, escape, existing_ids,
                              existing_index, hash_is_duplicate, import_hash,
                              is_duplicate, render_assertion, routing_help,
                              since_from_argv)

FLAGS = {"--all", "--write", "--dry-run", "--allow-discrepancy"}
VALUE_FLAGS = {"--since"}  # --since YYYY-MM-DD: ignore rows before this date



def counter_account(desc, chase_cat, txn_type, amount, account):
    if (txn_type or "").lower() == "payment":
        return TRANSFER_ACCOUNT
    # A learned merchant rule beats Chase's classifier.
    rule = match_rule(desc, "", amount)
    if rule:
        return rule
    return chase_category(chase_cat) or EXPENSE_DEFAULT


def parse_rows(path):
    """-> (parsed rows in FILE order, [(line#, error)]). A malformed row is
    reported and skipped, never fatal — one bad line must not stall the
    import (same never-block spirit as the Uncategorized fallback)."""
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        raw = list(reader)
    if raw and not {"Transaction Date", "Description", "Amount"} <= set(raw[0]):
        sys.exit(f"{path}: not a Chase activity CSV — need Transaction Date / "
                 f"Description / Amount columns, got: {', '.join(raw[0])}")
    parsed, bad = [], []
    for lineno, r in enumerate(raw, 2):  # 2 = first data line of the file
        try:
            date = datetime.strptime((r["Transaction Date"] or "").strip(), "%m/%d/%Y").date()
            amt = float((r["Amount"] or "").replace(",", "").replace("$", ""))
        except (TypeError, ValueError) as e:
            bad.append((lineno, str(e)))
            continue
        balance = None
        if (r.get("Balance") or "").strip():
            try:
                balance = float(r["Balance"].replace(",", ""))
            except ValueError:
                pass  # treated as no balance -> continuity UNVERIFIABLE
        parsed.append({"date": date, "amount": amt, "balance": balance,
                       "payee": escape(r["Description"]), "desc": r["Description"],
                       "category": r.get("Category"), "type": r.get("Type", "")})
    return parsed, bad


def main():
    argv = sys.argv[1:]
    since, argv = since_from_argv(argv, __doc__)
    unknown = {a for a in argv if a.startswith("--")} - FLAGS
    if unknown:
        sys.exit(f"unknown flag(s): {', '.join(sorted(unknown))}\n\n{__doc__}")
    args = [a for a in argv if not a.startswith("--")]
    dedupe = "--all" not in argv
    write = "--write" in argv and "--dry-run" not in argv
    allow = "--allow-discrepancy" in argv
    if len(args) != 2:
        msg = __doc__
        if len(args) == 1:  # CSV given but no ledger account — list the known options
            msg += ("\nNo ledger account given. The CSV carries no ACCTID, so name "
                    "one of the configured accounts (or add its [[accounts]] entry):\n"
                    + routing_help())
        sys.exit(msg)
    path, account = args
    rows, bad = parse_rows(path)
    # Continuity runs over FILE order (the order the bank wrote the balances);
    # emission below is date-sorted.
    tag, detail, closing = check_continuity_rows([(r["amount"], r["balance"]) for r in rows])
    rows.sort(key=lambda r: r["date"])
    if since:
        pre = len(rows)
        rows = [r for r in rows if str(r["date"]) >= since]
        if pre - len(rows):
            print(f";   --since {since}: ignoring {pre - len(rows)} earlier rows "
                  f"(pre-snapshot history the opening balance already nets)", file=sys.stderr)
    ledger_hashes, _ledger_fitids = existing_ids() if dedupe else ({}, {})
    idx = existing_index(account, set(ledger_hashes)) if dedupe else {}
    new_hashes = {}
    entries, kept, skipped = [], [], []
    for r in rows:
        h = import_hash(r["date"], r["amount"], r["payee"], account)
        # No FITID in a CSV: fitid="" makes any hash hit a duplicate — the
        # right call both against QFX-imported twins (same transaction via
        # another format) and in-batch (a CSV cannot disambiguate same-day
        # identical rows; import the card's QFX when that matters).
        if dedupe and hash_is_duplicate(h, "", ledger_hashes, new_hashes):
            skipped.append((r, "hash"))
            continue
        if dedupe and is_duplicate(idx, r["date"], r["amount"], r["payee"]):
            skipped.append((r, "±5d"))
            continue
        new_hashes.setdefault(h, set())
        counter = counter_account(r["desc"], r["category"], r["type"], r["amount"], account)
        entries.append((r["date"], emit(r["date"], r["payee"],
                                        {"chase-type": r["type"], "import-hash": h},
                                        account, r["amount"], counter)))
        kept.append((r["date"], r["amount"]))
    print(f"; {account}: balance continuity {tag} — {detail}", file=sys.stderr)
    if kept and closing is not None:
        # No statement-end date in a CSV — the last transaction date stands in.
        stmt_end = max(r["date"] for r in rows)
        anchored, _ = check_continuity_ledger(account, closing, stmt_end, kept)
        a_date = assertion_date(stmt_end, max(d for d, _ in kept))
        entries.append((a_date, render_assertion(account, closing, a_date,
                                                 anchored == VERIFIED)))
    print(f"; imported {len(kept)} transactions to {account}"
          + (f" (skipped {len(skipped)} already in ledger)" if skipped else ""), file=sys.stderr)
    for r, why in skipped:
        print(f";   skipped ({why}) {r['date']} {r['amount']:.2f} {r['payee']}", file=sys.stderr)
    for lineno, err in bad:
        print(f";   UNPARSEABLE line {lineno}: {err} — fix the row and re-import", file=sys.stderr)
    if tag == DISCREPANCY and not allow:
        sys.exit(f"BLOCKED: balance continuity DISCREPANCY for {account} — nothing "
                 f"{'written' if write else 'imported'}. Fix the export or the ledger, "
                 f"or re-run with --allow-discrepancy.")
    for _, e in entries:
        print(e)
    if write:
        if not entries:
            print("; nothing new to write", file=sys.stderr)
            return
        paths = append_to_ledger(entries)
        print(f"; wrote {len(entries)} entries to {', '.join(paths)} (bean-check passed "
              f"or was unavailable — see above)", file=sys.stderr)


if __name__ == "__main__":
    main()
