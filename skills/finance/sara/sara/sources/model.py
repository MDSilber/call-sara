"""The canonical statement models — the hub every source maps into.

A source mapper (OFX, Chase CSV, Plaid, a future SimpleFIN/SnapTrade lane)
parses whatever its institution emits and returns THESE frozen types; the
single writer (`sara.ledger.writer`) accepts nothing else. Everything that
crosses the boundary is validated here: dates are real dates, money is
`Decimal` (floats never touch a money path — `tests/test_float_ban.py`
enforces it), text is sanitized so statement fields can never break out of
a Beancount string, and amounts are finite.

Identity: `source_id` is the dedupe key — the bank's FITID, Plaid's
transaction_id, or empty when the format carries none (CSV), in which case
the content hash (`sara.ledger.writer.import_hash`) is the fallback.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "BankStatement",
    "CanonBalance",
    "CanonInvestTxn",
    "CanonPosition",
    "CanonTxn",
    "InvestStatement",
    "MetaItems",
    "SaraModel",
    "escape",
    "parse_ofx_amount",
]


def escape(s: str | None) -> str:
    """Beancount string-safe. This text lands inside "..." in a file that is
    parsed — and in --write mode appended — automatically, so a statement
    field must never break out of the string: no double quotes, no backslashes
    (Beancount reads \\" as an escaped quote), and control characters
    (newlines included) collapse to single spaces."""
    s = "".join(c if c.isprintable() else " " for c in (s or ""))
    return " ".join(s.replace('"', "'").replace("\\", "/").split())


def parse_ofx_amount(raw: str | None) -> Decimal | None:
    """Parse an OFX numeric field to Decimal, tolerating US thousands-commas.

    '2,500.00' -> Decimal('2500.00') (real banks emit this). Ambiguous or
    corrupt shapes — EU-style '1.234,56', '12,34', multi-dot '1.2.3' —
    return None so the caller can report-and-skip the row: a mangled parse
    is just as much of a money bug as crashing, so nothing is ever guessed.
    """
    s = (raw or "").strip()
    m = re.fullmatch(r"([-+]?)([\d.,]+)", s)
    if not m:
        return None
    sign, body = m.group(1), m.group(2)
    if body.count(".") > 1:
        return None  # '1.2.3' — corrupt
    if "," in body:
        # commas are only legal as US thousands separators: 1-3 leading
        # digits, ,ddd groups, then an optional .decimals tail
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", body):
            return None  # '1.234,56' / '12,34' — ambiguous, never guess
        body = body.replace(",", "")
    try:
        value = Decimal(sign + body)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _finite(v: Decimal) -> Decimal:
    if not v.is_finite():
        raise ValueError("money amounts must be finite (no NaN/Infinity)")
    return v


Money = Annotated[Decimal, Field(description="Signed USD amount, account perspective")]
Units = Annotated[Decimal, Field(description="Security units (full statement precision)")]
MetaItems = tuple[tuple[str, str], ...]


class SaraModel(BaseModel):
    """Frozen pydantic base every canonical model derives from: immutable,
    unknown-field-rejecting — the strictness lives in one place."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)


class CanonTxn(SaraModel):
    """One cash transaction against one ledger-routable account."""

    date: date
    amount: Money
    payee: str  # escaped, display/emit-safe
    source_id: str = ""  # FITID / Plaid transaction_id / "" (CSV)
    account_key: str = ""  # routing key: OFX ACCTID, plaid_account_id, or ""
    kind: str = ""  # OFX TRNTYPE / Chase Type / Plaid payment channel
    match_text: str = ""  # raw text payee_rules match against; payee when empty
    meta: MetaItems = ()  # extra metadata persisted on the written entry

    @field_validator("amount")
    @classmethod
    def _amount_finite(cls, v: Decimal) -> Decimal:
        return _finite(v)

    @property
    def rule_text(self) -> str:
        return self.match_text or self.payee


class CanonBalance(SaraModel):
    """A statement's closing balance claim — fuel for the continuity gate."""

    account_key: str = ""
    closing: Money | None = None
    asof: date | None = None

    @field_validator("closing")
    @classmethod
    def _closing_finite(cls, v: Decimal | None) -> Decimal | None:
        return None if v is None else _finite(v)


class CanonPosition(SaraModel):
    """One security position as of a statement date."""

    ticker: str
    units: Units
    price: Money = Decimal(0)
    mktval: Money = Decimal(0)
    priced_asof: date | None = None
    name: str = ""

    @field_validator("units", "price", "mktval")
    @classmethod
    def _pos_finite(cls, v: Decimal) -> Decimal:
        return _finite(v)


class CanonInvestTxn(SaraModel):
    """One brokerage action (buy/sell/reinvest/income) in canonical form.

    `cash_txn` carries the INVBANKTRAN case — a plain bank transaction that
    happens to live inside an investment statement.
    """

    kind: str  # BUYMF/BUYSTOCK/SELLMF/SELLSTOCK/REINVEST/INCOME/INVBANKTRAN
    date: date
    source_id: str = ""
    account_key: str = ""  # routing key (Plaid account_id; OFX routes per statement)
    ticker: str = ""
    units: Units = Decimal(0)
    price: Money = Decimal(0)
    total: Money | None = None
    income: str = ""  # INCOMETYPE: DIV / CGLONG / CGSHORT / INTEREST / ""
    cash_txn: CanonTxn | None = None  # set iff kind == INVBANKTRAN

    @field_validator("units", "price")
    @classmethod
    def _iv_finite(cls, v: Decimal) -> Decimal:
        return _finite(v)

    @field_validator("total")
    @classmethod
    def _total_finite(cls, v: Decimal | None) -> Decimal | None:
        return None if v is None else _finite(v)


class BankStatement(SaraModel):
    """One bank/card statement: routing key, rows, and the balance claim."""

    account_key: str
    txns: tuple[CanonTxn, ...] = ()
    balance: CanonBalance | None = None
    notes: tuple[str, ...] = ()  # parse reports, printed to stderr in file order


class InvestStatement(SaraModel):
    """One brokerage statement: actions, positions, and the as-of date."""

    account_key: str
    actions: tuple[CanonInvestTxn, ...] = ()
    positions: tuple[CanonPosition, ...] = ()
    cash: Money = Decimal(0)  # <AVAILCASH> — display only, never reconciled
    asof: date | None = None
    notes: tuple[str, ...] = ()
