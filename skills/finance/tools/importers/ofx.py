#!/usr/bin/env python3
"""Import a bank or credit-card OFX/QFX export into Beancount transactions.

Usage:
  ofx.py <file.qfx> [ledger-account] [--write] [--all] [--allow-discrepancy] [--dry-run]

The ledger account is looked up from the file's <ACCTID> via rules.toml
[[accounts]] (matched on last-4); pass it explicitly if there's no match.
Each transaction's counter-account comes from rules.toml [[payee_rules]];
unmatched debits land in Expenses:Uncategorized (the review queue — imports
never stall on categorization).

DRY-RUN BY DEFAULT: prints the would-be entries to stdout and a summary
(dedupe skips, balance continuity) to stderr, and writes nothing. Review,
then re-run with --write to append to ledger/<year>.beancount (bean-check
runs after; a failed check rolls the append back).

Dedupe, in order: (1) hash-exact — every imported transaction carries an
`import-hash:` (date|amount|payee|account), so re-importing the same or an
overlapping export is recognized exactly; (2) fuzzy fallback — same amount
and payee already in the ledger within ±5 days (transaction vs post dates
differ across export formats; also covers pre-hash ledger entries). Every
skip is listed on stderr; --all disables dedupe entirely.

Balance continuity ("Golden Rule"): when the file carries <LEDGERBAL>, the
ledger through the statement date plus this import must equal the closing
balance — tagged VERIFIED / DISCREPANCY / UNVERIFIABLE. A DISCREPANCY blocks
the import (exit 1, nothing written) unless --allow-discrepancy. When
VERIFIED, a dated balance assertion is emitted too.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules import categorize, route_by_acctid  # noqa: E402
from importers.common import (DISCREPANCY, VERIFIED, append_to_ledger,  # noqa: E402
                              assertion_date, bank_statements, bank_transactions,
                              check_continuity_ledger, emit, existing_hashes,
                              existing_index, import_hash, is_duplicate,
                              ofx_closing_balance, read_ofx, render_assertion)

FLAGS = {"--all", "--write", "--dry-run", "--allow-discrepancy"}


def main():
    argv = sys.argv[1:]
    unknown = {a for a in argv if a.startswith("--")} - FLAGS
    if unknown:
        sys.exit(f"unknown flag(s): {', '.join(sorted(unknown))}\n\n{__doc__}")
    args = [a for a in argv if not a.startswith("--")]
    dedupe = "--all" not in argv
    write = "--write" in argv and "--dry-run" not in argv
    allow = "--allow-discrepancy" in argv
    if not args:
        sys.exit(__doc__)
    path = args[0]
    statements = list(bank_statements(read_ofx(path)))
    if len(args) > 1 and len(statements) > 1:
        sys.exit(f"{path} holds {len(statements)} accounts — an explicit ledger account "
                 f"can't apply to all of them. Add [[accounts]] routing entries instead.")
    ledger_hashes = existing_hashes() if dedupe else set()
    entries, blocked = [], []
    for acct_id, text in statements:
        account = args[1] if len(args) > 1 else route_by_acctid(acct_id)
        if not account:
            print(f"; skipping account ending {acct_id[-4:]!r}: no [[accounts]] entry in "
                  f"rules.toml — add one (or pass the ledger account explicitly)", file=sys.stderr)
            continue
        idx = existing_index(account) if dedupe else {}
        new_hashes = set()
        txns = sorted(bank_transactions(text), key=lambda t: t["date"])
        kept, skipped = [], []
        for t in txns:
            h = import_hash(t["date"], t["amount"], t["payee"], account)
            if dedupe and (h in ledger_hashes or h in new_hashes):
                skipped.append((t, "hash"))
                continue
            if dedupe and is_duplicate(idx, t["date"], t["amount"], t["payee"]):
                skipped.append((t, "±5d"))
                continue
            new_hashes.add(h)
            counter = categorize(t["payee"], t["type"], t["amount"], account)
            entries.append((t["date"], emit(t["date"], t["payee"],
                                            {"ofx-type": t["type"], "import-hash": h},
                                            account, t["amount"], counter)))
            kept.append((t["date"], t["amount"]))
        closing, asof = ofx_closing_balance(text)
        tag, detail = check_continuity_ledger(account, closing, asof, kept)
        print(f"; {account}: balance continuity {tag} — {detail}", file=sys.stderr)
        if kept and closing is not None and asof:
            a_date = assertion_date(asof, max(d for d, _ in kept))
            entries.append((a_date, render_assertion(account, closing, a_date, tag == VERIFIED)))
        print(f"; imported {len(kept)} transactions to {account}"
              + (f" (skipped {len(skipped)} already in ledger)" if skipped else ""), file=sys.stderr)
        for t, why in skipped:
            print(f";   skipped ({why}) {t['date']} {t['amount']:.2f} {t['payee']}", file=sys.stderr)
        if tag == DISCREPANCY and not allow:
            blocked.append(account)
    for _, e in entries:
        print(e)
    sys.stdout.flush()  # entries must be visible before any write-failure exit
    if blocked:
        sys.exit(f"BLOCKED: balance continuity DISCREPANCY for {', '.join(blocked)} — "
                 f"nothing {'written' if write else 'imported'}. The entries above show "
                 f"what WOULD import. Fix the export or the ledger, or re-run with "
                 f"--allow-discrepancy.")
    if write:
        if not entries:
            print("; nothing new to write", file=sys.stderr)
            return
        paths = append_to_ledger(entries)
        print(f"; wrote {len(entries)} entries to {', '.join(paths)} (bean-check passed "
              f"or was unavailable — see above)", file=sys.stderr)


if __name__ == "__main__":
    main()
