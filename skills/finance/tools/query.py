#!/usr/bin/env python3
"""Query the vault — the fast way to answer money questions without eyeballing the ledger.

Usage:  tools/run query.py <command> [args]

  networth                 liquid net worth (+ paper if configured)
  balances                 per-account USD balances
  positions                holdings by commodity, valued in USD
  spend [YYYY|YYYY-MM]     spend by category for a year or month (default: current year)
  cashflow [YYYY]          income vs expenses by month for a year
  payee <regex>            every transaction whose payee matches
  uncategorized [n]        the review queue: top uncategorized payees (default 25)
  accounts                 all open ledger accounts
  sql "<beancount query>"  raw bean-query passthrough

Arithmetic here; judgment stays with the agent. Prefer reports/*.md for the
standing answers — reach for this for anything not precomputed.
"""
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import amount, illiquid_currency_regex, money, query, shadow_currency  # noqa: E402


def bql_str(s):
    """Escape a value for interpolation inside a single-quoted BQL string —
    beanquery understands SQL-style doubled quotes, so text can never break
    out of the '...' literal (payee regexes are user/bank-adjacent input)."""
    return str(s).replace("'", "''")


def cmd_networth(_):
    excl = bql_str(illiquid_currency_regex() or "") or None
    where = "account ~ '^(Assets|Liabilities)'" + (f" AND NOT currency ~ '{excl}'" if excl else "")
    rows = query(f"SELECT root(account,1) AS r, sum(convert(position,'USD')) AS v "
                 f"WHERE {where} GROUP BY r")
    vals = {r["r"]: amount(r["v"]) for r in rows}
    a, l = vals.get("Assets", 0.0), vals.get("Liabilities", 0.0)
    print(f"Liquid net worth: {money(a + l)}   (assets {money(a)} · liabilities {money(l)})")
    if excl:
        p = query(f"SELECT sum(convert(position,'{bql_str(shadow_currency())}')) AS v "
                  f"WHERE account ~ '^Assets' AND currency ~ '{excl}'")
        # the cell is valued in the shadow currency — name it, or amount()
        # rightly refuses to read the units as dollars
        pv = amount(p[0]["v"], shadow_currency()) if p and p[0].get("v") else 0.0
        print(f"Illiquid paper (not counted): {money(pv)}   combined {money(a + l + pv)}")


def cmd_balances(_):
    excl = bql_str(illiquid_currency_regex() or "") or None
    where = "account ~ '^(Assets|Liabilities)'" + (f" AND NOT currency ~ '{excl}'" if excl else "")
    for r in query(f"SELECT account, sum(convert(position,'USD')) AS v WHERE {where} "
                   f"GROUP BY account ORDER BY account"):
        cell = r["v"] or ""
        if not cell.strip():
            continue
        if "USD" not in cell:
            print(f"{'(unpriced)':>14}  {r['account']}   {' '.join(cell.split())}")
            continue
        v = amount(cell, "USD")
        if abs(v) >= 0.005:
            print(f"{money(v):>14}  {r['account']}")


def cmd_positions(_):
    for r in query("SELECT currency, sum(position) AS units, sum(convert(position,'USD')) AS usd "
                   "WHERE account ~ '^Assets' AND currency != 'USD' GROUP BY currency "
                   "ORDER BY currency"):
        units = " ".join((r["units"] or "").split())
        val = money(amount(r["usd"])) if "USD" in (r["usd"] or "") else "(no USD price)"
        print(f"{val:>16}  {r['currency']:<12} {units}")


def cmd_spend(args):
    period = args[0] if args else str(date.today().year)
    if not re.fullmatch(r"\d{4}(-\d{2})?", period):
        sys.exit(f"usage: spend [YYYY|YYYY-MM]   (got {period!r})")
    if len(period) == 4:
        where = f"year = {int(period)}"
    else:
        y, m = period.split("-")
        where = f"year = {int(y)} AND month = {int(m)}"
    rows = query(f"SELECT root(account,2) AS cat, sum(convert(position,'USD')) AS v "
                 f"WHERE account ~ '^Expenses' AND {where} GROUP BY cat")
    rows.sort(key=lambda r: -amount(r["v"]))
    total = 0.0
    for r in rows:
        v = amount(r["v"])
        total += v
        print(f"{money(v):>12}  {r['cat'].replace('Expenses:', '')}")
    print(f"{money(total):>12}  TOTAL ({period})")


def cmd_cashflow(args):
    year = int(args[0]) if args else date.today().year
    rows = query(f"SELECT month, root(account,1) AS r, sum(convert(position,'USD')) AS v "
                 f"WHERE year = {year} AND account ~ '^(Income|Expenses)' GROUP BY month, r "
                 f"ORDER BY month")
    by_month = {}
    for r in rows:
        by_month.setdefault(int(r["month"]), {})[r["r"]] = amount(r["v"])
    print(f"{'month':<8}{'income':>14}{'expenses':>14}{'net':>14}")
    for m, d in sorted(by_month.items()):
        inc = -d.get("Income", 0.0)  # income is negative in beancount
        exp = d.get("Expenses", 0.0)
        print(f"{year}-{m:02d}{money(inc):>14}{money(exp):>14}{money(inc - exp):>14}")


def cmd_payee(args):
    if not args:
        sys.exit("usage: payee <regex>")
    for r in query(f"SELECT date, payee, account, number WHERE payee ~ '{bql_str(args[0])}' "
                   "ORDER BY date"):
        print(f"{r['date']}  {money(amount(r['number'])):>12}  {r['account']:<40} {r['payee']}")


def cmd_uncategorized(args):
    limit = int(args[0]) if args else 25
    rows = query("SELECT payee, count(*) AS n, sum(convert(position,'USD')) AS v "
                 "WHERE account = 'Expenses:Uncategorized' GROUP BY payee")
    rows.sort(key=lambda r: -amount(r["v"]))
    total_n = sum(int(float(r["n"])) for r in rows)
    print(f"{total_n} uncategorized postings across {len(rows)} payees — teach rules.toml:\n")
    for r in rows[:limit]:
        print(f"{money(amount(r['v'])):>12}  x{int(float(r['n'])):<4} {r['payee']}")


def cmd_accounts(_):
    for r in query("SELECT account, open_date(account) AS opened GROUP BY account, opened "
                   "ORDER BY account"):
        print(f"{r['account']}")


def cmd_sql(args):
    if not args:
        sys.exit('usage: sql "<beancount query>"')
    rows = query(args[0])
    if rows:
        print("\t".join(rows[0].keys()))
        for r in rows:
            print("\t".join(str(v) for v in r.values()))


COMMANDS = {"networth": cmd_networth, "balances": cmd_balances, "positions": cmd_positions,
            "spend": cmd_spend, "cashflow": cmd_cashflow, "payee": cmd_payee,
            "uncategorized": cmd_uncategorized, "accounts": cmd_accounts, "sql": cmd_sql}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        sys.exit(__doc__)
    COMMANDS[sys.argv[1]](sys.argv[2:])
