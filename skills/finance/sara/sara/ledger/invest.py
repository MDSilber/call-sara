"""Investment entry rendering and the positions reconcile.

The lot dialect here is verified against beancount 3.2.3 (see the invest
CLI docstring for the full booking story): buys book {cost} or {{total}}
lots, sells book `-units {} @ price` (FIFO once the account's open
directive says so), reinvests follow the ROW's sign so broker corrections
back income out instead of double-counting it.

Positions reconcile is the investment analog of bank balance continuity:
ledger units through the statement date plus this import's deltas are
compared per commodity against <INVPOSLIST> — MATCH earns dated balance
assertions, MISMATCH never blocks (positions predate the vault everywhere)
but prints the exact opening-lot seed recipe.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal

from sara.ledger.queries import ledger_units
from sara.rules import categorize, invbanktran_default
from sara.sources.model import CanonInvestTxn, escape

MATCH, MISMATCH, UNVERIFIABLE = "MATCH", "MISMATCH", "UNVERIFIABLE"

GAINS = "Income:US:Gains"
INCOME_ACCOUNTS = {
    "DIV": "Income:US:Dividends",
    "CGLONG": "Income:US:CapGainsDistributions",
    "CGSHORT": "Income:US:CapGainsDistributions",
    "INTEREST": "Income:US:Interest",
}
INCOME_DEFAULT = "Income:US:Other"
BUY_KINDS = ("BUYMF", "BUYSTOCK")
SELL_KINDS = ("SELLMF", "SELLSTOCK")

ZERO = Decimal(0)
MILLI = Decimal("0.001")
HALF_CENT = Decimal("0.005")

UnitDeltas = dict[str, Decimal]


def fmt_units(x: Decimal) -> str:
    """Preserve the file's full unit precision (Vanguard MF units carry 5
    decimals): a truncated posting re-multiplies against per-unit cost to a
    DIFFERENT total and bean-check rejects the batch by fractions of a cent."""
    if x != x.quantize(MILLI, rounding=ROUND_HALF_EVEN):
        return f"{x:.6f}".rstrip("0").rstrip(".")
    return f"{x:.3f}"


def fmt_price(price: Decimal) -> str:
    """Full precision, but never fewer than 2 decimals (price conventions)."""
    t = f"{price:.6f}".rstrip("0")
    whole, _, frac = t.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def _cost(units: Decimal, price: Decimal, total: Decimal) -> str:
    """Cost braces for an acquiring posting: per-unit when it explains the
    cash to the cent, else total-cost {{...}} so commissions land in basis
    and the entry always balances exactly."""
    if abs(abs(units) * price - abs(total)) < HALF_CENT:
        # Full price precision: printing a 4-decimal fund price at 2 decimals
        # changes units*price and bean-check rejects by fractions of a cent.
        return "{" + f"{fmt_price(price)} USD" + "}"
    return "{{" + f"{abs(total):.2f} USD" + "}}"


def _render(when: date, payee: str, meta: dict[str, str], postings: list[str]) -> str:
    lines = [f'{when} * "{escape(payee)}" ""']
    lines += [f'  {k}: "{escape(str(v))}"' for k, v in meta.items() if v]
    lines += postings
    return "\n".join(lines) + "\n"


def payee_for(a: CanonInvestTxn) -> str:
    if a.kind == "INVBANKTRAN":
        return a.cash_txn.payee if a.cash_txn else ""
    if a.kind in BUY_KINDS:
        return f"BUY {fmt_units(a.units)} {a.ticker} @ {a.price:.2f}"
    if a.kind in SELL_KINDS:
        return f"SELL {fmt_units(abs(a.units))} {a.ticker} @ {a.price:.2f}"
    if a.kind == "REINVEST":
        return f"REINVEST {a.income or 'DIV'} {fmt_units(a.units)} {a.ticker} @ {a.price:.2f}"
    return f"{a.income or 'INCOME'} {a.ticker}"


def cash_amount(a: CanonInvestTxn) -> Decimal:
    """The signed cash effect used in the dedupe hash (cents-stable)."""
    if a.kind == "INVBANKTRAN":
        return a.cash_txn.amount if a.cash_txn else ZERO
    if a.total is not None:
        return a.total
    if a.kind == "REINVEST":
        return -(a.units * a.price)  # direction follows the row (corrections)
    sign = Decimal(-1) if a.kind in BUY_KINDS else Decimal(1)
    return sign * abs(a.units * a.price)


def build(a: CanonInvestTxn, account: str, payee: str, h: str) -> tuple[str, UnitDeltas, set[str]]:
    """One canonical action -> (entry_text, {ticker: unit_delta}, accounts_used)."""
    when, kind = a.date, a.kind
    if kind == "INVBANKTRAN":
        t = a.cash_txn
        assert t is not None
        # Brokerage cash-ins often carry NO payee, so payee rules can't route
        # them. rules.toml [accounts_invbanktran] maps the brokerage account
        # to a default counter (e.g. Transfers when the bank twin is in the
        # ledger, an Income bucket when the source is external); a payee rule
        # still wins when one matches.
        counter = categorize(t.payee, t.kind, t.amount, account)
        if counter in ("Income:US:Other", "Expenses:Uncategorized"):
            counter = invbanktran_default(account) or counter
        entry = _render(when, t.payee, {"ofx-type": t.kind, "import-hash": h,
                                        "fitid": t.source_id},
                        [f"  {account}   {t.amount:.2f} USD", f"  {counter}"])
        return entry, {}, {account, counter}
    ticker, units, price = a.ticker, a.units, a.price
    if kind in BUY_KINDS:
        total = -abs(a.total if a.total is not None else units * price)
        entry = _render(when, payee, {"ofx-type": kind, "import-hash": h,
                                      "fitid": a.source_id},
                        [f"  {account}   {fmt_units(units)} {ticker} {_cost(units, price, total)}",
                         f"  {account}   {total:.2f} USD"])
        return entry, {ticker: units}, {account}
    if kind in SELL_KINDS:
        total = abs(a.total if a.total is not None else units * price)
        entry = _render(when, payee, {"ofx-type": kind, "import-hash": h,
                                      "fitid": a.source_id},
                        [f"  {account}   -{fmt_units(abs(units))} {ticker} {{}} @ {fmt_price(price)} USD"
                         f"  ; whole lots (FIFO if set) — broker lot data governs at tax time",
                         f"  {account}   {total:.2f} USD",
                         f"  {GAINS}"])
        return entry, {ticker: -abs(units)}, {account, GAINS}
    if kind == "REINVEST":
        income_acct = INCOME_ACCOUNTS.get(a.income, INCOME_DEFAULT)
        # Sign follows the ROW: a normal reinvest carries negative TOTAL
        # (income leg negative = income earned) and positive units; a broker
        # CORRECTION carries negative units and positive TOTAL — forcing
        # -abs() there would book the reversal as a second income hit
        # instead of backing the first one out.
        total = a.total if a.total is not None else -(units * price)
        entry = _render(when, payee, {"ofx-type": kind, "import-hash": h,
                                      "fitid": a.source_id},
                        [f"  {account}   {fmt_units(units)} {ticker} {_cost(units, price, total)}",
                         f"  {income_acct}   {total:.2f} USD"])
        return entry, {ticker: units}, {account, income_acct}
    # INCOME (cash distribution)
    income_acct = INCOME_ACCOUNTS.get(a.income, INCOME_DEFAULT)
    total = a.total if a.total is not None else ZERO
    entry = _render(when, payee, {"ofx-type": kind, "import-hash": h,
                                  "fitid": a.source_id},
                    [f"  {account}   {total:.2f} USD", f"  {income_acct}"])
    return entry, {}, {account, income_acct}


def reconcile(account: str, stated: dict[str, Decimal], asof: date,
              kept_units: UnitDeltas,
              first_activity: date | None) -> tuple[str, dict[str, Decimal], list[str]]:
    """Compare ledger+import units per commodity against <INVPOSLIST>.
    Returns (worst_tag, matched: {ticker: units}, report_lines)."""
    if not stated:
        return UNVERIFIABLE, {}, [
            f"; {account}: positions UNVERIFIABLE — statement has no <INVPOSLIST>"]
    prior = ledger_units(account, asof)
    if prior is None:
        return UNVERIFIABLE, {}, [
            f"; {account}: positions UNVERIFIABLE — ledger not queryable "
            f"(vault venv missing?)"]
    lines: list[str] = []
    parts: list[str] = []
    missing: list[tuple[str, Decimal]] = []
    excess: list[tuple[str, Decimal]] = []
    matched: dict[str, Decimal] = {}
    for ticker in sorted(set(stated) | set(prior) | set(kept_units)):
        computed = prior.get(ticker, ZERO) + kept_units.get(ticker, ZERO)
        want = stated.get(ticker, ZERO)
        if abs(computed - want) <= MILLI:
            parts.append(f"{ticker} MATCH ({fmt_units(want)})")
            matched[ticker] = want
        else:
            parts.append(f"{ticker} MISMATCH (ledger+import {fmt_units(computed)} "
                         f"vs statement {fmt_units(want)})")
            (missing if want > computed else excess).append((ticker, want - computed))
    tag = MISMATCH if (missing or excess) else MATCH
    lines.append(f"; {account}: positions vs statement {asof} {tag} — {', '.join(parts)}")
    if missing:
        seed_date = (first_activity or asof) - timedelta(days=1)
        lines.append(f";   a MISMATCH is a pre-import history gap, not a block — seed an "
                     f"opening position of "
                     f"{', '.join(f'{fmt_units(g)} {t}' for t, g in missing)}, e.g.:")
        lines.append(f';   {seed_date} * "Opening position (history predates the vault)" ""')
        for ticker, gap in missing:
            lines.append(f";     {account}   {fmt_units(gap)} {ticker} {{COST USD}}  "
                         f"; basis from broker records — needed before {ticker} sells can book")
        lines.append(";     Equity:Opening-Balances")
    if excess:
        lines.append(f";   ledger EXCEEDS the statement by "
                     f"{', '.join(f'{fmt_units(-g)} {t}' for t, g in excess)} — that is "
                     f"duplicates or drift, not a history gap; audit manual entries")
    return tag, matched, lines
