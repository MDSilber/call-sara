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

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal

from sara.cli.shared import err, reject_unknown_flags, since_from_argv
from sara.ledger.writer import (
    DISCREPANCY,
    VERIFIED,
    AccountDedupe,
    Entry,
    append_to_ledger,
    assertion_date,
    check_continuity_ledger,
    emit,
    existing_assertion_dates,
    existing_ids,
    render_assertion,
)
from sara.rules import categorize, route_by_acctid
from sara.sources.model import CanonTxn
from sara.sources.ofx import parse_bank, read_ofx
from sara.vault import routing_help

FLAGS = frozenset({"--all", "--write", "--dry-run", "--allow-discrepancy"})


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = __doc__ or ""
    since, argv = since_from_argv(argv, usage)
    reject_unknown_flags(argv, FLAGS, usage)
    args = [a for a in argv if not a.startswith("--")]
    dedupe = "--all" not in argv
    write = "--write" in argv and "--dry-run" not in argv
    allow = "--allow-discrepancy" in argv
    if not args:
        raise SystemExit(usage)
    path = args[0]
    statements = parse_bank(read_ofx(path))
    if len(args) > 1 and len(statements) > 1:
        raise SystemExit(
            f"{path} holds {len(statements)} accounts — an explicit ledger account "
            f"can't apply to all of them. Add [[accounts]] routing entries instead.")
    ledger_hashes, ledger_fitids = existing_ids() if dedupe else ({}, {})
    entries: list[Entry] = []
    blocked: list[str] = []
    for stmt in statements:
        account = args[1] if len(args) > 1 else route_by_acctid(stmt.account_key)
        if not account:
            err(f"; skipping account ending {stmt.account_key[-4:]!r}: no [[accounts]] entry in "
                f"rules.toml — add one (or pass the ledger account explicitly)")
            err(routing_help())
            continue
        for note in stmt.notes:
            err(note)
        deduper = AccountDedupe(account, ledger_hashes, ledger_fitids, enabled=dedupe)
        txns = sorted(stmt.txns, key=lambda t: t.date)
        if since:
            pre = len(txns)
            txns = [t for t in txns if str(t.date) >= since]
            if pre - len(txns):
                err(f";   --since {since}: ignoring {pre - len(txns)} earlier rows "
                    f"(pre-snapshot history the opening balance already nets)")
        kept: list[tuple[date, Decimal]] = []
        skipped: list[tuple[CanonTxn, str]] = []
        for t in txns:
            h = deduper.hash_for(t.date, t.amount, t.payee)
            why = deduper.check(t.date, t.amount, t.payee, t.source_id, h)
            if why:
                skipped.append((t, why))
                continue
            deduper.record(h, t.source_id)
            counter = categorize(t.payee, t.kind, t.amount, account)
            entries.append((t.date, emit(t.date, t.payee,
                                         {"ofx-type": t.kind, "import-hash": h,
                                          "fitid": t.source_id},
                                         account, t.amount, counter)))
            kept.append((t.date, t.amount))
        closing = stmt.balance.closing if stmt.balance else None
        asof = stmt.balance.asof if stmt.balance else None
        tag, detail = check_continuity_ledger(account, closing, asof, kept)
        err(f"; {account}: balance continuity {tag} — {detail}")
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
                err(f"; {account}: assertion near {a_date} already recorded — "
                    f"not re-emitting")
        err(f"; imported {len(kept)} transactions to {account}"
            + (f" (skipped {len(skipped)} already in ledger)" if skipped else ""))
        for t, why in skipped:
            err(f";   skipped ({why}) {t.date} {t.amount:.2f} {t.payee}")
        if tag == DISCREPANCY and not allow:
            blocked.append(account)
    for _, e in entries:
        print(e)
    sys.stdout.flush()  # entries must be visible before any write-failure exit
    if blocked:
        raise SystemExit(
            f"BLOCKED: balance continuity DISCREPANCY for {', '.join(blocked)} — "
            f"nothing {'written' if write else 'imported'}. The entries above show "
            f"what WOULD import. Fix the export or the ledger, or re-run with "
            f"--allow-discrepancy.")
    if write:
        if not entries:
            err("; nothing new to write")
            return
        paths = append_to_ledger(entries)
        err(f"; wrote {len(entries)} entries to {', '.join(paths)} (bean-check passed "
            f"or was unavailable — see above)")


if __name__ == "__main__":
    main()
