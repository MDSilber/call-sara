"""Investment OFX/QFX (INVSTMTMSGSRS — the brokerage 'Quicken' download
format) -> canonical models.

A pure mapper: text in, `InvestStatement`s out — typed actions
(buys/sells/reinvests/income/embedded bank rows), positions, and the as-of
date. Parse reports come back as `notes` in file order; unsupported OFX
action types are named, skipped rows are listed, and a row that would mint
a phantom-zero cost basis is refused here so it can never reach the writer.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from sara.sources.model import (
    CanonInvestTxn,
    CanonPosition,
    InvestStatement,
    parse_ofx_amount,
)
from sara.sources.ofx import bank_rows

BUY_KINDS = ("BUYMF", "BUYSTOCK")
SELL_KINDS = ("SELLMF", "SELLSTOCK")
HANDLED = BUY_KINDS + SELL_KINDS + ("REINVEST", "INCOME", "INVBANKTRAN")
UNSUPPORTED = ("BUYDEBT", "BUYOPT", "BUYOTHER", "SELLDEBT", "SELLOPT",
               "SELLOTHER", "TRANSFER", "INVEXPENSE", "MARGININTEREST",
               "RETOFCAP", "SPLIT", "JRNLSEC", "JRNLFUND", "CLOSUREOPT")
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9'._-]*$")

ZERO = Decimal(0)

Securities = dict[str, tuple[str, str]]  # UNIQUEID -> (ticker-or-uid, name)


def _tag(block: str, tag: str, default: str = "") -> str:
    m = re.search(rf"<{tag}>([^<\n]+)", block)
    return m.group(1).strip() if m else default


def _num(block: str, tag: str, notes: list[str]) -> Decimal | None:
    """Numeric field, or None when absent OR malformed ('1.2.3', EU commas)
    — the caller's missing-field handling then reports-and-skips the row
    instead of importing a silently mangled amount."""
    m = re.search(rf"<{tag}>([-+\d.,]+)", block)
    if not m:
        return None
    val = parse_ofx_amount(m.group(1))
    if val is None:
        notes.append(f";   malformed <{tag}> {m.group(1)!r} — treated as absent")
    return val


def _date(block: str, tag: str) -> date | None:
    m = re.search(rf"<{tag}>(\d{{8}})", block)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def securities(text: str) -> Securities:
    """<SECLIST> UNIQUEID -> (ticker, security name); ticker falls back to
    the UNIQUEID itself."""
    out: Securities = {}
    for sec in re.findall(r"<SECINFO>(.*?)</SECINFO>", text, re.S):
        uid = _tag(sec, "UNIQUEID")
        if uid:
            out[uid] = (_tag(sec, "TICKER") or uid, _tag(sec, "SECNAME"))
    return out


def _actions(stmt: str, sec: Securities) -> tuple[list[CanonInvestTxn], list[str]]:
    """Every supported action in a statement's <INVTRANLIST> as canonical
    investment transactions, plus notes (malformed fields as encountered,
    then skipped-row summaries, then unsupported action types)."""
    out: list[CanonInvestTxn] = []
    notes: list[str] = []
    skipped: list[tuple[str, str]] = []
    for kind, block in re.findall(
            rf"<({'|'.join(HANDLED)})>(.*?)</\1>", stmt, re.S):
        if kind == "INVBANKTRAN":
            # the embedded <STMTTRN> lacks the </BANKTRANLIST> terminator
            # the bank-row parser expects — supply one
            rows, row_notes = bank_rows(block + "</BANKTRANLIST>")
            notes.extend(row_notes)
            out.extend(CanonInvestTxn(kind=kind, date=t.date, source_id=t.source_id,
                                      cash_txn=t)
                       for t in rows)
            continue
        when = _date(block, "DTTRADE") or _date(block, "DTPOSTED")
        ticker = sec.get(_tag(block, "UNIQUEID"), (_tag(block, "UNIQUEID"), ""))[0]
        units = _num(block, "UNITS", notes)
        price = _num(block, "UNITPRICE", notes)
        total = _num(block, "TOTAL", notes)
        income = _tag(block, "INCOMETYPE").upper()
        if not when or (kind != "INCOME" and not units) or (kind == "INCOME" and total is None):
            skipped.append((kind, f"{when or 'no date'}: missing units/total — row skipped"))
            continue
        if kind != "INCOME" and not TICKER_RE.match(ticker):
            skipped.append((kind, f"{when}: security {ticker!r} has no usable "
                                  f"ticker — book this row by hand"))
            continue
        if kind in (*BUY_KINDS, "REINVEST") and not price and total is None:
            # No UNITPRICE and no TOTAL: the lot would book at {0.00 USD}
            # basis — a phantom-zero cost that silently overstates every
            # future gain. Report-and-skip; book by hand with the real basis.
            skipped.append((kind, f"{when}: {ticker} carries neither UNITPRICE "
                                  f"nor TOTAL — refusing to mint a {{0.00 USD}} "
                                  f"basis lot; book this row by hand"))
            continue
        out.append(CanonInvestTxn(
            kind=kind, date=when, ticker=ticker,
            units=units if units is not None else ZERO,
            price=price or ZERO, total=total, income=income,
            source_id=_tag(block, "FITID"),
        ))
    notes.extend(f";   skipped ({kind}) {note}" for kind, note in skipped)
    unsupported = [k for k in UNSUPPORTED if re.search(rf"<{k}>", stmt)]
    if unsupported:
        notes.append(f";   unsupported OFX action types present, NOT imported: "
                     f"{', '.join(unsupported)} — book by hand if material")
    return out, notes


def _positions(stmt: str, sec: Securities) -> tuple[list[CanonPosition], list[str]]:
    """<INVPOSLIST> rows in file order. A position whose UNITS field is
    absent or malformed is reported and dropped — a corrupt row must never
    silently enter the reconcile."""
    m = re.search(r"<INVPOSLIST>(.*?)</INVPOSLIST>", stmt, re.S)
    out: list[CanonPosition] = []
    notes: list[str] = []
    if not m:
        return out, notes
    for pos in re.findall(r"<INVPOS>(.*?)</INVPOS>", m.group(1), re.S):
        uid = _tag(pos, "UNIQUEID")
        ticker, name = sec.get(uid, (uid, ""))
        units = _num(pos, "UNITS", notes)
        if units is None:
            notes.append(f";   position {ticker!r} has no parseable <UNITS> — dropped "
                         f"from the reconcile; check the export")
            continue
        priced_raw = _tag(pos, "DTPRICEASOF")[:8]
        priced: date | None = None
        if priced_raw:
            try:
                priced = datetime.strptime(priced_raw, "%Y%m%d").date()
            except ValueError:
                priced = None
        out.append(CanonPosition(
            ticker=ticker, units=units,
            price=_num(pos, "UNITPRICE", notes) or ZERO,
            mktval=_num(pos, "MKTVAL", notes) or ZERO,
            priced_asof=priced, name=name,
        ))
    return out, notes


def parse_invest(text: str) -> list[InvestStatement]:
    """Map a full investment OFX/QFX export into canonical statements.
    Empty when the file holds no <INVSTMTRS> blocks (not an investment OFX)."""
    sec = securities(text)
    out: list[InvestStatement] = []
    for stmt in re.findall(r"<INVSTMTRS>(.*?)</INVSTMTRS>", text, re.S):
        actions, action_notes = _actions(stmt, sec)
        positions, pos_notes = _positions(stmt, sec)
        cash_notes: list[str] = []
        cash = _num(stmt, "AVAILCASH", cash_notes)
        out.append(InvestStatement(
            account_key=_tag(stmt, "ACCTID"),
            actions=tuple(actions),
            positions=tuple(positions),
            cash=cash if cash is not None else ZERO,
            asof=_date(stmt, "DTASOF") or _date(stmt, "DTEND"),
            notes=tuple(action_notes + pos_notes + cash_notes),
        ))
    return out


def units_by_ticker(positions: tuple[CanonPosition, ...] | list[CanonPosition]) -> dict[str, Decimal]:
    """{ticker: units summed} for tickers that are usable commodities —
    the reconcile view of a statement's positions."""
    out: dict[str, Decimal] = {}
    for p in positions:
        if TICKER_RE.match(p.ticker):
            out[p.ticker] = out.get(p.ticker, ZERO) + p.units
    return out
