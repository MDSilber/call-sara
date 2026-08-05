#!/usr/bin/env python3
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
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules import route_by_acctid  # noqa: E402
from vault import money  # noqa: E402
from importers.common import parse_ofx_amount, read_ofx  # noqa: E402


def _tag(block, tag, default=""):
    m = re.search(rf"<{tag}>([^<\n]+)", block)
    return m.group(1).strip() if m else default


def _numf(block, tag):
    """Numeric field via the shared comma-aware parser; malformed values
    ('1.2.3', EU-style commas) are reported and read as 0 rather than
    silently mangled — this tool only DISPLAYS numbers, it never writes."""
    raw = _tag(block, tag, "")
    if not raw:
        return 0.0
    val = parse_ofx_amount(raw)
    if val is None:
        print(f"; malformed <{tag}> {raw!r} — shown as 0; check the export", file=sys.stderr)
        return 0.0
    return val


def parse(text):
    """-> {securities: {uid: (ticker, name)}, accounts: [{acctid, asof, positions, cash}]}"""
    securities = {}
    for sec in re.findall(r"<SECINFO>(.*?)</SECINFO>", text, re.S):
        uid = _tag(sec, "UNIQUEID")
        if uid:
            securities[uid] = (_tag(sec, "TICKER") or uid, _tag(sec, "SECNAME"))
    accounts = []
    for stmt in re.findall(r"<INVSTMTRS>(.*?)</INVSTMTRS>", text, re.S):
        asof = _tag(stmt, "DTASOF")[:8] or _tag(stmt, "DTEND")[:8]
        positions = []
        for pos in re.findall(r"<INVPOS>(.*?)</INVPOS>", stmt, re.S):
            uid = _tag(pos, "UNIQUEID")
            ticker, name = securities.get(uid, (uid, ""))
            positions.append({
                "ticker": ticker, "name": name,
                "units": _numf(pos, "UNITS"),
                "price": _numf(pos, "UNITPRICE"),
                "mktval": _numf(pos, "MKTVAL"),
                "priced": _tag(pos, "DTPRICEASOF")[:8],
            })
        accounts.append({
            "acctid": _tag(stmt, "ACCTID"),
            "asof": asof,
            "positions": positions,
            "cash": _numf(stmt, "AVAILCASH"),
        })
    return accounts


def _fmt_date(d):
    return datetime.strptime(d, "%Y%m%d").date().isoformat() if d else "????-??-??"


def prices(accounts):
    """One price directive per (date, ticker), deduped across accounts."""
    seen = OrderedDict()
    for a in accounts:
        for p in a["positions"]:
            if p["price"] and p["units"]:
                key = (_fmt_date(p["priced"] or a["asof"]), p["ticker"])
                seen[key] = p["price"]
    print("; --- prices (append to ledger/prices.beancount) ---")
    for (date, ticker), price in seen.items():
        if not re.match(r"^[A-Z][A-Z0-9'._-]*$", ticker):
            print(f"; skipped {ticker}: not a valid commodity (no ticker in the file — map it by hand)")
            continue
        print(f"{date} price {ticker:<10} {price:.4f} USD")


def table(accounts):
    print("\n; --- holdings snapshot (file to facts/accounts/<acct>/index.md) ---")
    for a in accounts:
        route = route_by_acctid(a["acctid"]) or f"(unrouted — add [[accounts]] last4={a['acctid'][-4:]!r})"
        print(f"\n## ...{a['acctid'][-4:]}  ->  {route}   (as of {_fmt_date(a['asof'])})")
        total = a["cash"]
        for p in sorted(a["positions"], key=lambda x: -x["mktval"]):
            total += p["mktval"]
            print(f"- {p['ticker']} — {p['units']:,.3f} sh @ {p['price']:,.2f} = {money(p['mktval'])}"
                  + (f"   ({p['name']})" if p["name"] else ""))
        if a["cash"]:
            print(f"- cash (available) = {money(a['cash'])}")
        print(f"- **total ≈ {money(total)}**")


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.exit(__doc__)
    accounts = parse(read_ofx(args[0]))
    if not accounts:
        sys.exit("no <INVSTMTRS> blocks found — is this an investment OFX/QFX?")
    flags = set(args[1:])
    if "--table" not in flags:
        prices(accounts)
    if "--prices" not in flags:
        table(accounts)


if __name__ == "__main__":
    main()
