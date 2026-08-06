"""Beancount-facing read helpers: run bean-query, read numbers as Decimal.

`sara.ledger` is deliberately the ONLY part of the package that knows the
ledger is Beancount (query binary, file format, check tool) — swap the
engine and only this package changes. Queries shell out to the vault
venv's bean-query, exactly like the tools always have; no beancount import
means the mappers stay runnable anywhere.
"""

from __future__ import annotations

import csv
import io
import re
import subprocess
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sara.vault import BEAN_QUERY, LEDGER, VAULT, require_vault

Row = dict[str, str]
ZERO = Decimal(0)
CENT = Decimal("0.01")


def query(sql: str) -> list[Row]:
    """Run a bean-query statement, return rows as dicts."""
    require_vault()
    if not BEAN_QUERY.exists():
        raise SystemExit(f"bean-query not found at {BEAN_QUERY} — is the vault venv set up?")
    out = subprocess.run([str(BEAN_QUERY), "-f", "csv", str(LEDGER), sql],
                         capture_output=True, text=True, cwd=VAULT)
    if out.returncode != 0:
        raise RuntimeError(f"bean-query failed:\n{out.stderr.strip()}\n  query: {sql}")
    return [dict(r) for r in csv.DictReader(io.StringIO(out.stdout))]


def bql_account(account: str) -> str:
    """Account names never contain quotes; strip them so rules.toml-sourced
    text can't break out of a single-quoted BQL string."""
    return str(account).replace("'", "")


def amount(cell: str | None, currency: str = "USD") -> Decimal:
    """Pull the numeric value out of a bean-query cell, as Decimal.

    '1,234.56 USD' -> Decimal('1234.56'). A cell can hold a multi-currency
    inventory rendered as aligned columns (' , , 1,234.56 USD'), so prefer
    the number tagged with `currency`. A bare number (count(*), sum(number))
    passes through unchanged. A cell holding ONLY other commodities ('12.000
    VTSAX') returns 0 for `currency` math — those are UNITS, and reading
    them as dollars is how an unpriced holding used to leak into USD sums.
    """
    cell = cell or ""
    m = re.search(rf"(-?\d[\d,]*(?:\.\d+)?)\s+{re.escape(currency)}(?![.\w])", cell)
    if m:
        return _dec(m.group(1))
    if re.search(r"-?\d[\d,.]*\s*[A-Z]", cell):
        return ZERO  # some commodity, none of it `currency` — never a dollar figure
    m = re.search(r"(-?\d[\d,]*(?:\.\d+)?)", cell)
    return _dec(m.group(1)) if m else ZERO


def _dec(text: str) -> Decimal:
    try:
        return Decimal(text.replace(",", ""))
    except InvalidOperation:
        return ZERO


def money(x: Decimal) -> str:
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


def ledger_balance_asof(account: str, asof: date) -> tuple[Decimal | None, int]:
    """(USD balance, posting count) for an account through a date; (None, 0)
    when the ledger can't be queried (vault venv missing)."""
    account = bql_account(account)
    try:
        rows = query(f"SELECT count(*) AS n, sum(convert(position,'USD')) AS v "
                     f"WHERE account = '{account}' AND date <= {asof.isoformat()}")
    except (RuntimeError, SystemExit):
        return None, 0
    if not rows:
        return ZERO, 0
    n = int(amount(rows[0].get("n") or "0"))
    return amount(rows[0].get("v")), n


def account_rows(account: str) -> list[tuple[date, Decimal, str]]:
    """(date, amount, payee) for every posting on an account — fuel for the
    legacy fuzzy dedupe index. Empty when the ledger can't be queried."""
    account = bql_account(account)
    try:
        rows = query(f"SELECT date, number, payee WHERE account = '{account}'")
    except (RuntimeError, SystemExit):
        return []
    out: list[tuple[date, Decimal, str]] = []
    for r in rows:
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
            amt = Decimal(r["number"]).quantize(CENT)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
        out.append((d, amt, r.get("payee") or ""))
    return out


def ledger_units(account: str, asof: date) -> dict[str, Decimal] | None:
    """{commodity: units} held in the ledger through a date; None when the
    ledger can't be queried (vault venv missing)."""
    account = bql_account(account)
    try:
        rows = query(f"SELECT currency, sum(number) AS units WHERE account = '{account}' "
                     f"AND currency != 'USD' AND date <= {asof.isoformat()} GROUP BY currency")
    except (RuntimeError, SystemExit):
        return None
    out: dict[str, Decimal] = {}
    for r in rows:
        try:
            out[r["currency"]] = out.get(r["currency"], ZERO) + Decimal(r["units"])
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
    return out


def opened_accounts() -> set[str]:
    """Accounts with an `open` directive anywhere in ledger/*.beancount."""
    out: set[str] = set()
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            out.update(re.findall(r"^\s*\d{4}-\d{2}-\d{2}\s+open\s+(\S+)",
                                  f.read_text(), re.M))
        except OSError:
            continue
    return out
