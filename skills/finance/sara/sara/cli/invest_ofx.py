"""Import brokerage ACTIVITY from an investment OFX/QFX (INVSTMTMSGSRS —
Vanguard's download format) into Beancount transactions.

Usage:
  invest_ofx.py <file.qfx> [ledger-account] [--write] [--all] [--dry-run] [--since YYYY-MM-DD]

This is the ACTIVITY half of the investment pipeline (holdings_ofx.py is the
positions/prices half). Accounts route by <ACCTID> last-4 via rules.toml
[[accounts]], exactly like ofx.py. Cash and units share the routed account —
the OFX cash/settlement sub-account IS the brokerage account. What each OFX
action becomes:

  BUYMF/BUYSTOCK     units {cost} acquiring a lot  +  negative USD cash leg
  SELLMF/SELLSTOCK   -units {} @ price  +  USD proceeds  +  Income:US:Gains
                     (auto-balanced; specific-lot detail needs the broker's
                     lot data at tax time — the {} books whole lots)
  REINVEST           units {cost}  +  Income:US:Dividends (INCOMETYPE DIV)
                     or Income:US:CapGainsDistributions (CGLONG/CGSHORT)
  INCOME             USD cash  +  Income:US:Dividends / :CapGainsDistributions
                     / :Interest by INCOMETYPE
  INVBANKTRAN        the bank-transaction path — contributions/withdrawals
                     categorize via rules.toml [[payee_rules]] (the TRANSFER
                     rule sends them to Assets:US:Transfers)

Unsupported action types (options, debt, journal entries, ...) are reported
and skipped, never fatal. Income/gains accounts that are not yet opened in
ledger/*.beancount are listed as paste-ready `open` directives on stderr.

DRY-RUN BY DEFAULT; --write appends to ledger/<year>.beancount and bean-check
rolls back a bad import. Dedupe is the bank importer's: (1) FITID-exact —
every entry stores the broker's FITID as `fitid:` metadata, the primary
identity; (2) hash-exact via `import-hash:` (date | signed cash cents |
action payee | account), honored only when the recorded fitids don't
disagree (identical same-day actions with different FITIDs BOTH import);
(3) the ±5-day fuzzy fallback, applied only to legacy ledger entries that
carry neither fitid nor hash. --all disables.

POSITIONS RECONCILE (the investment analog of bank balance continuity): the
statement's <INVPOSLIST> holds positions as of <DTASOF>. After parsing, the
ledger's units per commodity through that date plus this import's unit deltas
are compared per commodity and tagged MATCH / MISMATCH (UNVERIFIABLE when the
ledger can't be queried). A MISMATCH never blocks — positions predate the
vault everywhere — it means pre-import history is missing, and the report
suggests seeding a dated opening lot ("seed an opening position of N units").
MATCHed commodities get a dated `balance` assertion. Statement cash
(<AVAILCASH>) is NOT reconciled: at Vanguard the settlement fund is itself a
position, so AVAILCASH is unreliable.

BOOKING — how new costed lots coexist with snapshot-seeded units (verified
against beancount 3.2.3):
  * Existing vault accounts hold units from opening snapshots at NO cost
    (e.g. `Assets:US:Vanguard:Brokerage 7426.960 VTSAX` from
    Equity:Opening-Balances). Under the default STRICT booking, buys with
    {cost} land in the same account without any error — mixed costless +
    costed inventories are legal to AUGMENT. So imports of buys, reinvests,
    dividends, and transfers need NO open-directive change and NO migration.
  * SELLS are where booking bites. `-units {} @ price` books against costed
    lots only. STRICT accepts it while the account holds at most ONE costed
    lot of that commodity; the first sell facing several lots fails
    bean-check as "Ambiguous matches" (the --write rolls back). The fix is
    one token on that account's open directive:
        2020-01-01 open Assets:US:Vanguard:Brokerage "FIFO"
    FIFO books {} sells oldest-lot-first and the gain auto-computes. (NOT
    booking "NONE": under NONE a {} sell fails to interpolate at all —
    "Too many missing numbers".)
  * A sell can NEVER book against costless snapshot units ({} skips them —
    "Not enough lots"). If a sale dips into snapshot-seeded units, first
    convert the snapshot posting into a costed opening lot, e.g.
        Assets:US:Vanguard:Brokerage  7426.960 VTSAX {130.00 USD, 2024-01-01}
    with the basis from the broker's records ({0.00 USD} is a legal
    placeholder but overstates gains at sale). The MISMATCH suggestion
    prints the same seed recipe.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal

from sara.cli.shared import err, reject_unknown_flags, since_from_argv
from sara.ledger.invest import (
    MATCH,
    ZERO,
    UnitDeltas,
    build,
    cash_amount,
    fmt_units,
    payee_for,
    reconcile,
)
from sara.ledger.queries import opened_accounts
from sara.ledger.writer import AccountDedupe, Entry, append_to_ledger, assertion_date, existing_ids
from sara.rules import route_by_acctid
from sara.sources.invest_ofx import parse_invest, units_by_ticker
from sara.sources.model import CanonInvestTxn
from sara.sources.ofx import read_ofx
from sara.vault import routing_help

FLAGS = frozenset({"--all", "--write", "--dry-run"})


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = __doc__ or ""
    since, argv = since_from_argv(argv, usage)
    reject_unknown_flags(argv, FLAGS, usage)
    args = [a for a in argv if not a.startswith("--")]
    dedupe = "--all" not in argv
    write = "--write" in argv and "--dry-run" not in argv
    if not args:
        raise SystemExit(usage)
    statements = parse_invest(read_ofx(args[0]))
    if not statements:
        raise SystemExit("no <INVSTMTRS> blocks found — is this an investment OFX/QFX? "
                         "(bank/card exports go through ofx.py)")
    if len(args) > 1 and len(statements) > 1:
        raise SystemExit(
            f"{args[0]} holds {len(statements)} accounts — an explicit ledger "
            f"account can't apply to all of them. Add [[accounts]] routing entries instead.")
    ledger_hashes, ledger_fitids = existing_ids() if dedupe else ({}, {})
    entries: list[Entry] = []
    used_accounts: set[str] = set()
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
        rows = sorted(stmt.actions, key=lambda a: a.date)
        if since:
            pre = len(rows)
            rows = [a for a in rows if str(a.date) >= since]
            if pre - len(rows):
                err(f";   --since {since}: ignoring {pre - len(rows)} earlier rows "
                    f"(pre-snapshot history the opening position already nets)")
        kept: list[date] = []
        skipped: list[tuple[CanonInvestTxn, Decimal, str, str]] = []
        deltas_by_date: list[tuple[date, UnitDeltas]] = []
        for a in rows:
            payee = payee_for(a)
            amt = cash_amount(a)
            h = deduper.hash_for(a.date, amt, payee)
            why = deduper.check(a.date, amt, payee, a.source_id, h)
            if why:
                skipped.append((a, amt, payee, why))
                continue
            deduper.record(h, a.source_id)
            entry, deltas, used = build(a, account, payee, h)
            entries.append((a.date, entry))
            used_accounts |= used
            if deltas:
                deltas_by_date.append((a.date, deltas))
            kept.append(a.date)
        stated = units_by_ticker(stmt.positions)
        if stmt.asof:
            asof = stmt.asof
            in_window = [d for d in kept if d <= asof]
            kept_units: UnitDeltas = {}  # unit deltas from this import dated on/before asof
            for d, deltas in deltas_by_date:
                if d <= asof:
                    for t, u in deltas.items():
                        kept_units[t] = kept_units.get(t, ZERO) + u
            tag, matched, lines = reconcile(account, stated, asof,
                                            kept_units, min(in_window, default=None))
            for line in lines:
                err(line)
            if tag == MATCH and kept:
                a_date = assertion_date(asof, max(in_window, default=asof))
                for ticker, units in sorted(matched.items()):
                    entries.append((a_date, f"{a_date} balance {account}   "
                                            f"{fmt_units(units)} {ticker}\n"))
        else:
            err(f"; {account}: positions UNVERIFIABLE — no <DTASOF> on the statement")
        err(f"; imported {len(kept)} transactions to {account}"
            + (f" (skipped {len(skipped)} already in ledger)" if skipped else ""))
        for a, amt, payee, why in skipped:
            err(f";   skipped ({why}) {a.date} {amt:.2f} {payee}")
    missing = sorted(used_accounts - opened_accounts())
    if missing:
        err("; NOT YET OPENED in ledger/*.beancount — add to accounts.beancount "
            "(bean-check will reject --write until then):")
        for acct in missing:
            err(f";   2000-01-01 open {acct}" +
                ("   USD" if acct.split(":")[0] in ("Income", "Expenses") else
                 '            ; holds units + settlement cash; add "FIFO" once sells appear'))
    for _, e in entries:
        print(e)
    sys.stdout.flush()
    if write:
        if not entries:
            err("; nothing new to write")
            return
        paths = append_to_ledger(entries)
        err(f"; wrote {len(entries)} entries to {', '.join(paths)} (bean-check passed "
            f"or was unavailable — see above)")


if __name__ == "__main__":
    main()
