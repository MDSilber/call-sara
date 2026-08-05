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

Dedupe, in order: (1) FITID-exact — the bank's own transaction id, stored
as `fitid:` metadata on every imported entry, is the primary identity; a row
whose FITID is already recorded for the account skips. (2) hash-exact — the
`import-hash:` (date|amount|payee|account) content hash, honored only when
the recorded entry has no fitid or the fitids agree: two same-day identical
rows with DIFFERENT FITIDs are two real transactions and BOTH import.
(3) fuzzy fallback — same amount and payee within ±5 days, applied ONLY to
legacy ledger entries that carry neither fitid nor import-hash (transaction
vs post dates differ across export formats). Every skip is listed on
stderr; --all disables dedupe entirely.

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
                              check_continuity_ledger, emit,
                              existing_assertion_dates, existing_ids,
                              existing_index, hash_is_duplicate, import_hash,
                              is_duplicate, ofx_closing_balance, read_ofx,
                              render_assertion, routing_help, since_from_argv)

FLAGS = {"--all", "--write", "--dry-run", "--allow-discrepancy"}
VALUE_FLAGS = {"--since"}  # --since YYYY-MM-DD: ignore rows before this date



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
    if not args:
        sys.exit(__doc__)
    path = args[0]
    statements = list(bank_statements(read_ofx(path)))
    if len(args) > 1 and len(statements) > 1:
        sys.exit(f"{path} holds {len(statements)} accounts — an explicit ledger account "
                 f"can't apply to all of them. Add [[accounts]] routing entries instead.")
    ledger_hashes, ledger_fitids = existing_ids() if dedupe else ({}, {})
    entries, blocked = [], []
    for acct_id, text in statements:
        account = args[1] if len(args) > 1 else route_by_acctid(acct_id)
        if not account:
            print(f"; skipping account ending {acct_id[-4:]!r}: no [[accounts]] entry in "
                  f"rules.toml — add one (or pass the ledger account explicitly)", file=sys.stderr)
            print(routing_help(), file=sys.stderr)
            continue
        idx = existing_index(account, set(ledger_hashes)) if dedupe else {}
        new_hashes, new_fitids = {}, set()
        txns = sorted(bank_transactions(text), key=lambda t: t["date"])
        if since:
            pre = len(txns)
            txns = [t for t in txns if str(t["date"]) >= since]
            if pre - len(txns):
                print(f";   --since {since}: ignoring {pre - len(txns)} earlier rows "
                      f"(pre-snapshot history the opening balance already nets)", file=sys.stderr)
        kept, skipped = [], []
        for t in txns:
            h = import_hash(t["date"], t["amount"], t["payee"], account)
            fid = t["fitid"]
            if dedupe and fid and (fid in ledger_fitids.get(account, ())
                                   or fid in new_fitids):
                skipped.append((t, "fitid"))
                continue
            if dedupe and hash_is_duplicate(h, fid, ledger_hashes, new_hashes):
                skipped.append((t, "hash"))
                continue
            if dedupe and is_duplicate(idx, t["date"], t["amount"], t["payee"]):
                skipped.append((t, "±5d"))
                continue
            new_hashes.setdefault(h, set())
            if fid:
                new_hashes[h].add(fid)
                new_fitids.add(fid)
            counter = categorize(t["payee"], t["type"], t["amount"], account)
            entries.append((t["date"], emit(t["date"], t["payee"],
                                            {"ofx-type": t["type"], "import-hash": h,
                                             "fitid": fid},
                                            account, t["amount"], counter)))
            kept.append((t["date"], t["amount"]))
        closing, asof = ofx_closing_balance(text)
        tag, detail = check_continuity_ledger(account, closing, asof, kept)
        print(f"; {account}: balance continuity {tag} — {detail}", file=sys.stderr)
        if closing is not None and asof and (kept or tag == VERIFIED):
            last_kept = max((d for d, _ in kept), default=asof)
            a_date = assertion_date(asof, last_kept)
            # Zero rows kept = a re-import (or a fully-deduped overlap): the
            # assertion may still re-anchor a NEWER statement, but must not
            # pile up a duplicate for the same one on every re-run.
            if kept or not any(abs((a_date - d).days) <= 3
                               for d in existing_assertion_dates(account)):
                entries.append((a_date, render_assertion(account, closing, a_date,
                                                         tag == VERIFIED)))
            else:
                print(f"; {account}: assertion near {a_date} already recorded — "
                      f"not re-emitting", file=sys.stderr)
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
