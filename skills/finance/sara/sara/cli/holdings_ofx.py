"""Read an investment OFX/QFX (e.g. a brokerage 'Quicken' download) and turn it
into the two things the vault needs: price directives for prices.beancount and
a per-account holdings table for the facts files.

Usage:
  holdings_ofx.py <file.qfx> [--prices|--table]

Default prints both. Positions are matched to ledger accounts by the trailing
digits of each <ACCTID> via rules.toml [[accounts]]; unmatched accounts are
shown by last-4 so you can add the routing entry. This does NOT write to the
ledger — it hands the agent verified numbers to file.
"""

from __future__ import annotations

import sys
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from sara.cli.shared import err
from sara.ledger.queries import money
from sara.rules import route_by_acctid
from sara.sources.invest_ofx import TICKER_RE, parse_invest
from sara.sources.model import InvestStatement
from sara.sources.ofx import read_ofx


def _fmt_date(d: date | None) -> str:
    return d.isoformat() if d else "????-??-??"


def prices(statements: list[InvestStatement]) -> None:
    """One price directive per (date, ticker), deduped across accounts."""
    seen: OrderedDict[tuple[str, str], Decimal] = OrderedDict()
    for stmt in statements:
        for p in stmt.positions:
            if p.price and p.units:
                key = (_fmt_date(p.priced_asof or stmt.asof), p.ticker)
                seen[key] = p.price
    print("; --- prices (append to ledger/prices.beancount) ---")
    for (when, ticker), price in seen.items():
        if not TICKER_RE.match(ticker):
            print(f"; skipped {ticker}: not a valid commodity (no ticker in the file — map it by hand)")
            continue
        print(f"{when} price {ticker:<10} {price:.4f} USD")


def table(statements: list[InvestStatement]) -> None:
    print("\n; --- holdings snapshot (file to facts/accounts/<acct>/index.md) ---")
    for stmt in statements:
        route = (route_by_acctid(stmt.account_key)
                 or f"(unrouted — add [[accounts]] last4={stmt.account_key[-4:]!r})")
        print(f"\n## ...{stmt.account_key[-4:]}  ->  {route}   (as of {_fmt_date(stmt.asof)})")
        total = stmt.cash
        for p in sorted(stmt.positions, key=lambda x: -x.mktval):
            total += p.mktval
            print(f"- {p.ticker} — {p.units:,.3f} sh @ {p.price:,.2f} = {money(p.mktval)}"
                  + (f"   ({p.name})" if p.name else ""))
        if stmt.cash:
            print(f"- cash (available) = {money(stmt.cash)}")
        print(f"- **total ≈ {money(total)}**")


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        raise SystemExit(__doc__)
    statements = parse_invest(read_ofx(args[0]))
    if not statements:
        raise SystemExit("no <INVSTMTRS> blocks found — is this an investment OFX/QFX?")
    for stmt in statements:
        for note in stmt.notes:
            err(note)
    flags = set(args[1:])
    unknown = flags - {"--prices", "--table"}
    if unknown:
        raise SystemExit(f"unknown flag(s): {', '.join(sorted(unknown))} — this "
                         f"importer only prints (append its output by hand)\n{__doc__}")
    if "--table" not in flags:
        prices(statements)
    if "--prices" not in flags:
        table(statements)


if __name__ == "__main__":
    main()
