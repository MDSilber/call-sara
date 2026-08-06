"""Chase credit-card activity CSV -> canonical models.

A pure mapper: file in, rows out (with each row's running Balance for the
in-file continuity chain and Chase's own Category cell for the mapping the
CLI applies via rules.toml). Malformed rows are collected, never fatal —
one bad line must not stall the import.
"""

from __future__ import annotations

import csv
from contextlib import suppress
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sara.sources.model import CanonTxn, SaraModel, escape

REQUIRED_COLUMNS = {"Transaction Date", "Description", "Amount"}


class ChaseRow(SaraModel):
    """One parsed CSV row: the canonical transaction plus the columns the
    Chase pipeline needs that other sources don't carry."""

    txn: CanonTxn
    balance: Decimal | None = None  # running Balance column, when present
    category: str | None = None  # Chase's own classifier cell


def _dec(cell: str) -> Decimal:
    value = Decimal(cell)
    if not value.is_finite():
        raise InvalidOperation(f"non-finite amount {cell!r}")
    return value


def parse_rows(path: str | Path) -> tuple[list[ChaseRow], list[tuple[int, str]]]:
    """-> (parsed rows in FILE order, [(line#, error)]). A malformed row is
    reported and skipped, never fatal — one bad line must not stall the
    import (same never-block spirit as the Uncategorized fallback)."""
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        raw = list(reader)
    if raw and not set(raw[0]) >= REQUIRED_COLUMNS:
        raise SystemExit(f"{path}: not a Chase activity CSV — need Transaction Date / "
                         f"Description / Amount columns, got: {', '.join(raw[0])}")
    parsed: list[ChaseRow] = []
    bad: list[tuple[int, str]] = []
    for lineno, r in enumerate(raw, 2):  # 2 = first data line of the file
        try:
            when = datetime.strptime((r["Transaction Date"] or "").strip(),
                                     "%m/%d/%Y").date()
            amt = _dec((r["Amount"] or "").replace(",", "").replace("$", "").strip())
        except (TypeError, ValueError, InvalidOperation) as e:
            bad.append((lineno, str(e)))
            continue
        balance: Decimal | None = None
        if (r.get("Balance") or "").strip():
            # an unparseable Balance is treated as no balance -> continuity UNVERIFIABLE
            with suppress(InvalidOperation):
                balance = _dec(r["Balance"].replace(",", "").strip())
        desc = r["Description"] or ""
        parsed.append(ChaseRow(
            txn=CanonTxn(
                date=when,
                amount=amt,
                payee=escape(desc),
                match_text=desc,
                kind=r.get("Type") or "",
            ),
            balance=balance,
            category=r.get("Category"),
        ))
    return parsed, bad
