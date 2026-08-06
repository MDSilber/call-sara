"""Bank/card OFX (QFX) -> canonical models.

A pure mapper: text in, `BankStatement`s out. Parse reports (malformed rows,
truncation tallies) come back as `notes` in file order for the CLI to print;
nothing here touches the vault or the ledger.

The parsing is the audited regex approach carried over verbatim: OFX SGML
from real banks is too ragged for strict parsers, the terminator lookahead
keeps truncated exports importing their final row, and a tally of <STMTTRN>
opens vs rows parsed-or-reported backstops any other silent drop with a
loud warning.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

from sara.sources.model import BankStatement, CanonBalance, CanonTxn, escape, parse_ofx_amount


def read_ofx(path: str | Path) -> str:
    return Path(path).read_text(encoding="latin-1")


def acctid(text: str) -> str:
    m = re.search(r"<ACCTID>([^<\n]+)", text)
    return m.group(1).strip() if m else ""


def _statement_chunks(text: str) -> list[tuple[str, str]]:
    """(acctid, statement_text) per bank/card statement in the file.

    A single export can carry several accounts (e.g. checking + savings);
    each <STMTRS>/<CCSTMTRS> block has its own <ACCTID> and transaction list.
    """
    chunks = re.split(r"(?=<(?:CC)?STMTRS>)", text)
    found: list[tuple[str, str]] = []
    for chunk in chunks:
        if not re.match(r"<(?:CC)?STMTRS>", chunk):
            continue
        found.append((acctid(chunk), chunk))
    if not found:
        found.append((acctid(text), text))  # header-less / minimal files
    return found


def bank_rows(text: str) -> tuple[list[CanonTxn], list[str]]:
    """Every <STMTTRN> in a bank/card statement as canonical transactions,
    plus parse notes in file order.

    Truncation-safe: the terminator lookahead includes \\Z and <LEDGERBAL>,
    so an export cut off before the closing tags still yields its final row;
    a tally of <STMTTRN> opens vs rows parsed-or-reported backstops any other
    silent drop with a loud warning.
    """
    opens = len(re.findall(r"<STMTTRN>", text))
    rows: list[CanonTxn] = []
    notes: list[str] = []
    reported = 0
    for t in re.findall(r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>|<LEDGERBAL>|\Z)",
                        text, re.S):
        d = re.search(r"<DTPOSTED>(\d{8})", t)
        a = re.search(r"<TRNAMT>([-+\d.,]+)", t)
        if not (d and a):
            reported += 1
            notes.append(f"; skipping malformed row — missing DTPOSTED/TRNAMT "
                         f"({' '.join(t.split())[:80]!r})")
            continue
        # a malformed row must skip with a report, never crash the whole
        # import — and never silently mis-parse (see parse_ofx_amount)
        amt = parse_ofx_amount(a.group(1))
        when: date | None
        try:
            when = datetime.strptime(d.group(1), "%Y%m%d").date()
        except ValueError:
            when = None
        if when is None or amt is None:
            reported += 1
            notes.append(f"; skipping malformed row — unparseable TRNAMT/DTPOSTED "
                         f"({a.group(1)!r} / {d.group(1)!r})")
            continue
        n = re.search(r"<NAME>([^<\n]+)", t)
        m = re.search(r"<MEMO>([^<\n]+)", t)
        ty = re.search(r"<TRNTYPE>([A-Z]+)", t)
        fit = re.search(r"<FITID>([^<\n]+)", t)
        name = escape(((n.group(1) if n else "") + (" " + m.group(1) if m else "")).strip())
        rows.append(CanonTxn(
            date=when,
            amount=amt,
            payee=name,
            kind=(ty.group(1) if ty else ""),
            source_id=(fit.group(1).strip() if fit else ""),
        ))
    if opens != len(rows) + reported:
        notes.append(f"; WARNING: {opens} <STMTTRN> blocks opened but only "
                     f"{len(rows) + reported} parsed or reported — the export looks "
                     f"truncated/corrupt; reconcile the closing balance before "
                     f"trusting this import")
    return rows, notes


def closing_balance(text: str) -> tuple[CanonBalance, list[str]]:
    """The statement's <LEDGERBAL> as a canonical balance claim, plus notes."""
    notes: list[str] = []
    m = re.search(r"<LEDGERBAL>(.*?)(?=</LEDGERBAL>|<AVAILBAL>|<STMTTRN>|\Z)", text, re.S)
    if not m:
        return CanonBalance(), notes
    block = m.group(1)
    b = re.search(r"<BALAMT>([-+\d.,]+)", block)
    d = re.search(r"<DTASOF>(\d{8})", block) or re.search(r"<DTEND>(\d{8})", text)
    closing = None
    asof = None
    if b:
        closing = parse_ofx_amount(b.group(1))
        if closing is None:
            notes.append(f"; ignoring malformed <BALAMT> {b.group(1)!r} — treating the "
                         f"statement as carrying no closing balance")
    if d:
        try:
            asof = datetime.strptime(d.group(1), "%Y%m%d").date()
        except ValueError:
            notes.append(f"; ignoring malformed balance as-of date {d.group(1)!r}")
    return CanonBalance(closing=closing, asof=asof), notes


def parse_bank(text: str) -> list[BankStatement]:
    """Map a full OFX/QFX export into canonical bank statements."""
    out: list[BankStatement] = []
    for acct_id, chunk in _statement_chunks(text):
        rows, notes = bank_rows(chunk)
        balance, bal_notes = closing_balance(chunk)
        out.append(BankStatement(
            account_key=acct_id,
            txns=tuple(rows),
            balance=balance.model_copy(update={"account_key": acct_id}),
            notes=tuple(notes + bal_notes),
        ))
    return out
