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

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal

from sara.cli.shared import err, reject_unknown_flags, since_from_argv
from sara.ledger.writer import (
    DISCREPANCY,
    FAMILY_CSV,
    VERIFIED,
    AccountDedupe,
    Entry,
    append_to_ledger,
    assertion_date,
    check_continuity_ledger,
    check_continuity_rows,
    emit,
    existing_ids,
    render_assertion,
)
from sara.rules import EXPENSE_DEFAULT, TRANSFER_ACCOUNT, chase_category, match_rule
from sara.sources.chase_csv import ChaseRow, parse_rows
from sara.vault import routing_help

FLAGS = frozenset({"--all", "--write", "--dry-run", "--allow-discrepancy"})


def counter_account(desc: str, chase_cat: str | None, txn_type: str,
                    amount: Decimal, account: str) -> str:
    if (txn_type or "").lower() == "payment":
        return TRANSFER_ACCOUNT
    # A learned merchant rule beats Chase's classifier.
    rule = match_rule(desc, "", amount)
    if rule:
        return rule
    return chase_category(chase_cat) or EXPENSE_DEFAULT


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = __doc__ or ""
    since, argv = since_from_argv(argv, usage)
    reject_unknown_flags(argv, FLAGS, usage)
    args = [a for a in argv if not a.startswith("--")]
    dedupe = "--all" not in argv
    write = "--write" in argv and "--dry-run" not in argv
    allow = "--allow-discrepancy" in argv
    if len(args) != 2:
        msg = usage
        if len(args) == 1:  # CSV given but no ledger account — list the known options
            msg += ("\nNo ledger account given. The CSV carries no ACCTID, so name "
                    "one of the configured accounts (or add its [[accounts]] entry):\n"
                    + routing_help())
        raise SystemExit(msg)
    path, account = args
    rows, bad = parse_rows(path)
    # Continuity runs over FILE order (the order the bank wrote the balances);
    # emission below is date-sorted.
    tag, detail, closing = check_continuity_rows([(r.txn.amount, r.balance) for r in rows])
    rows.sort(key=lambda r: r.txn.date)
    if since:
        pre = len(rows)
        rows = [r for r in rows if str(r.txn.date) >= since]
        if pre - len(rows):
            err(f";   --since {since}: ignoring {pre - len(rows)} earlier rows "
                f"(pre-snapshot history the opening balance already nets)")
    ledger_hashes, ledger_fitids = existing_ids() if dedupe else ({}, {})
    deduper = AccountDedupe(account, ledger_hashes, ledger_fitids, enabled=dedupe)
    entries: list[Entry] = []
    kept: list[tuple[date, Decimal]] = []
    skipped: list[tuple[ChaseRow, str]] = []
    for r in rows:
        t = r.txn
        h = deduper.hash_for(t.date, t.amount, t.payee)
        # No FITID in a CSV: source_id="" makes any hash hit a duplicate — the
        # right call both against QFX-imported twins (same transaction via
        # another format) and in-batch (a CSV cannot disambiguate same-day
        # identical rows; import the card's QFX when that matters).
        why = deduper.check(t.date, t.amount, t.payee, "", h, family=FAMILY_CSV)
        if why:
            skipped.append((r, why))
            continue
        deduper.record(h, "")
        counter = counter_account(t.rule_text, r.category, t.kind, t.amount, account)
        entries.append((t.date, emit(t.date, t.payee,
                                     {"chase-type": t.kind, "import-hash": h},
                                     account, t.amount, counter)))
        kept.append((t.date, t.amount))
    err(f"; {account}: balance continuity {tag} — {detail}")
    if kept and closing is not None:
        # No statement-end date in a CSV — the last transaction date stands in.
        stmt_end = max(r.txn.date for r in rows)
        anchored, _ = check_continuity_ledger(account, closing, stmt_end, kept)
        a_date = assertion_date(stmt_end, max(d for d, _ in kept))
        entries.append((a_date, render_assertion(account, closing, a_date,
                                                 anchored == VERIFIED)))
    err(f"; imported {len(kept)} transactions to {account}"
        + (f" (skipped {len(skipped)} already in ledger)" if skipped else ""))
    for r, why in skipped:
        err(f";   skipped ({why}) {r.txn.date} {r.txn.amount:.2f} {r.txn.payee}")
    for lineno, e in bad:
        err(f";   UNPARSEABLE line {lineno}: {e} — fix the row and re-import")
    if tag == DISCREPANCY and not allow:
        raise SystemExit(
            f"BLOCKED: balance continuity DISCREPANCY for {account} — nothing "
            f"{'written' if write else 'imported'}. Fix the export or the ledger, "
            f"or re-run with --allow-discrepancy.")
    for _, e in entries:
        print(e)
    if write:
        if not entries:
            err("; nothing new to write")
            return
        paths = append_to_ledger(entries)
        err(f"; wrote {len(entries)} entries to {', '.join(paths)} (bean-check passed "
            f"or was unavailable — see above)")


if __name__ == "__main__":
    main()
