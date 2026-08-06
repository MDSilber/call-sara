"""Ledger → plain rows: the in-process read seam for the analytics exporter.

Lives in sara.ledger because it is beancount-facing — this module is the
only place the analytics layer touches beancount types. It loads the vault
ledger once and flattens it into plain dataclass rows (postings at the
fact grain, transaction headers, account/commodity dims, prices, balance
checks) that sara/analytics.py pours into DuckDB without knowing anything
about beancount.

beancount's NamedTuples are built in a style type checkers cannot infer,
so — exactly like the plaid boundary in sara/plaid_api.py — the library is
held at arm's length behind ``Any`` aliases and narrow Protocols, and
every value crosses into typed rows immediately.

Conventions the rows carry, decided once, here:

* ``txn_id`` is beanquery's id: ``beancount.core.compare.hash_entry`` — a
  deterministic content hash (source file/line included), so ids are
  stable across rebuilds of an unchanged ledger and unique per entry.
* ``*_home`` amounts are the posting converted to the vault's home
  currency (the ledger's first ``operating_currency``, USD when unset) at
  the latest price ON OR BEFORE the transaction date; NULL when no price
  is known by then. Home-currency amounts pass through unchanged.
* ``prices`` holds the ledger's explicit price directives (source
  ``price``) plus prices implied by costs and ``@`` conversions (source
  ``implicit``, via beancount's implicit_prices plugin) so holdings are
  valuable from their acquisition date onward.
* ``source_file`` is relative to the vault when possible, and metadata is
  serialized to JSON with Decimals as strings — exactness survives.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path
from typing import Any, Protocol, cast

import beancount
from beancount import loader
from beancount.core import compare, convert, data, prices
from beancount.core.position import Cost
from beancount.plugins import implicit_prices

from sara.typed import as_dicts, as_str
from sara.vault import LEDGER, VAULT, require_vault, rules

__all__ = [
    "AccountRow",
    "BalanceCheckRow",
    "CommodityRow",
    "LoadedLedger",
    "PostingRow",
    "PriceRow",
    "TxnRow",
    "load_ledger",
]

# The untyped beancount surface, held at arm's length (see module docstring).
_loader: Any = loader
_data: Any = data
_compare: Any = compare
_convert: Any = convert
_prices: Any = prices
_implicit: Any = implicit_prices
_COST_T: type[Any] = cast("type[Any]", Cost)

HOME_QUANT = Decimal("0.0001")  # *_home precision: DECIMAL(18,4)
EXTERNAL_ID_KEYS = ("fitid", "plaid-id")  # the writer's source-id metadata slots
_META_DROP = frozenset({"filename", "lineno"})
# account-kind heuristics over the leaf segment, first match wins (rules.toml
# routing metadata has no kind field, so the name convention is the signal)
_ACCOUNT_KINDS: tuple[tuple[str, str], ...] = (
    (r"checking", "checking"),
    (r"savings", "savings"),
    (r"card", "card"),
    (r"brokerage", "brokerage"),
    (r"401k|403b|\bira|rothira", "retirement"),
    (r"529", "529"),
    (r"hsa", "hsa"),
    (r"mortgage", "mortgage"),
    (r"loan", "loan"),
    (r"cash", "cash"),
)

Meta = dict[str, Any]


class _PostingP(Protocol):
    account: str
    units: Any
    cost: Any
    price: Any
    flag: str | None
    meta: Meta | None


class _TxnP(Protocol):
    meta: Meta
    date: dt.date
    flag: str | None
    payee: str | None
    narration: str | None
    tags: frozenset[str]
    links: frozenset[str]
    postings: Sequence[_PostingP]


class _OpenP(Protocol):
    meta: Meta
    date: dt.date
    account: str
    currencies: Sequence[str] | None


class _CloseP(Protocol):
    date: dt.date
    account: str


class _CommodityP(Protocol):
    meta: Meta
    date: dt.date
    currency: str


class _BalanceP(Protocol):
    date: dt.date
    account: str
    amount: Any
    diff_amount: Any


class _PriceP(Protocol):
    meta: Meta
    date: dt.date
    currency: str
    amount: Any


class _CostP(Protocol):
    number: Decimal
    currency: str
    date: dt.date | None
    label: str | None


@dataclass(frozen=True, slots=True)
class PostingRow:
    """One ledger posting — the fact grain (field order = table order)."""

    posting_id: int
    txn_id: str
    posting_index: int
    date: dt.date
    flag: str | None
    payee: str | None
    narration: str | None
    tags: list[str]
    links: list[str]
    account: str
    other_account: str | None
    amount: Decimal | None
    currency: str | None
    cost_number: Decimal | None
    cost_currency: str | None
    cost_date: dt.date | None
    cost_label: str | None
    price_number: Decimal | None
    price_currency: str | None
    weight_number: Decimal | None
    weight_currency: str | None
    amount_home: Decimal | None
    external_id: str | None
    source_file: str | None
    source_line: int | None
    meta: str | None


@dataclass(frozen=True, slots=True)
class TxnRow:
    txn_id: str
    date: dt.date
    flag: str | None
    payee: str | None
    narration: str | None
    tags: list[str]
    links: list[str]
    n_postings: int
    accounts: list[str]
    source_file: str | None
    source_line: int | None
    meta: str | None


@dataclass(frozen=True, slots=True)
class AccountRow:
    account: str
    parent: str | None
    leaf: str
    root: str
    depth: int
    is_open: bool
    open_date: dt.date | None
    close_date: dt.date | None
    currencies: list[str]
    owner: str | None
    institution: str | None
    kind: str | None
    external_ids: str | None
    meta: str | None


@dataclass(frozen=True, slots=True)
class CommodityRow:
    currency: str
    name: str | None
    precision: int | None
    kind: str | None
    meta: str | None


@dataclass(frozen=True, slots=True)
class PriceRow:
    commodity: str
    quote_currency: str
    date: dt.date
    price: Decimal
    source: str  # "price" (explicit directive) | "implicit" (from costs / @)


@dataclass(frozen=True, slots=True)
class BalanceCheckRow:
    date: dt.date
    account: str
    currency: str
    expected: Decimal
    actual: Decimal
    diff: Decimal


@dataclass(frozen=True, slots=True)
class LoadedLedger:
    """Everything the exporter needs, as plain rows — no beancount types."""

    home_currency: str
    postings: list[PostingRow]
    transactions: list[TxnRow]
    accounts: list[AccountRow]
    commodities: list[CommodityRow]
    prices: list[PriceRow]
    balance_checks: list[BalanceCheckRow]
    min_date: dt.date | None
    max_date: dt.date | None
    beancount_version: str
    n_load_errors: int


# ------------------------------------------------------- typed boundaries
def _amount_parts(amt: object) -> tuple[Decimal | None, str | None]:
    """(number, currency) off a beancount Amount — the one untyped crossing."""
    if amt is None:
        return None, None
    number = cast("Decimal | None", getattr(amt, "number", None))
    currency = cast("str | None", getattr(amt, "currency", None))
    return number, currency


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)  # exactness survives the JSON crossing
    if isinstance(value, dt.date):
        return value.isoformat()
    if value is None or isinstance(value, str | int | bool):
        return value
    return str(value)


def _meta_json(meta: Meta | None) -> str | None:
    """Directive metadata as compact JSON; lineage and dunder keys dropped."""
    if not meta:
        return None
    clean = {k: _jsonable(v) for k, v in meta.items()
             if k not in _META_DROP and not k.startswith("__")}
    return json.dumps(clean, sort_keys=True) if clean else None


def _rel_to_vault(filename: object) -> str | None:
    """Source path relative to the vault when under it (portable lineage)."""
    if not isinstance(filename, str) or not filename:
        return None
    try:
        return str(Path(filename).resolve().relative_to(VAULT.resolve()))
    except ValueError:
        return filename


def _lineage(meta: Meta | None) -> tuple[str | None, int | None]:
    if not meta:
        return None, None
    line = meta.get("lineno")
    return _rel_to_vault(meta.get("filename")), line if isinstance(line, int) else None


def _external_id(*metas: Meta | None) -> str | None:
    """The import/dedup id — posting metadata wins over the transaction's."""
    for meta in metas:
        if not meta:
            continue
        for key in EXTERNAL_ID_KEYS:
            value = meta.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _to_home(number: Decimal, currency: str, when: dt.date, home: str,
             price_map: object) -> Decimal | None:
    """`number currency` in the home currency at the latest price <= `when`."""
    if currency == home:
        return number.quantize(HOME_QUANT, rounding=ROUND_HALF_EVEN)
    found = cast("tuple[dt.date | None, Decimal | None]",
                 _prices.get_price(price_map, (currency, home), when))
    rate = found[1] if found else None
    if rate is None:
        return None
    return (number * rate).quantize(HOME_QUANT, rounding=ROUND_HALF_EVEN)


# ------------------------------------------------------------ dim helpers
def _account_kind(root: str, leaf: str) -> str | None:
    if root not in ("Assets", "Liabilities"):
        return None
    lowered = leaf.lower()
    for pattern, kind in _ACCOUNT_KINDS:
        if re.search(pattern, lowered):
            return kind
    return None


def _rules_by_account() -> dict[str, dict[str, Any]]:
    """rules.toml [[accounts]] entries keyed by ledger_account."""
    return {as_str(entry.get("ledger_account")): entry
            for entry in as_dicts(rules().get("accounts"))
            if as_str(entry.get("ledger_account"))}


def _owner(meta: Meta | None) -> str | None:
    """The household-lens `owner:` metadata, normalized like tools/vault.py."""
    raw = meta.get("owner") if meta else None
    owner = raw.strip().lower() if isinstance(raw, str) else ""
    return owner or None


def _account_row(entry: _OpenP, close: _CloseP | None,
                 routing: dict[str, dict[str, Any]]) -> AccountRow:
    parts = entry.account.split(":")
    route = routing.get(entry.account, {})
    institution = as_str(route.get("institution")) or None
    if institution is None and parts[0] in ("Assets", "Liabilities") and len(parts) >= 4:
        institution = parts[2]  # the Assets:US:<Institution>:<Name> convention
    last4 = as_str(route.get("last4"))
    return AccountRow(
        account=entry.account,
        parent=":".join(parts[:-1]) if len(parts) > 1 else None,
        leaf=parts[-1],
        root=parts[0],
        depth=len(parts),
        is_open=close is None,
        open_date=entry.date,
        close_date=close.date if close else None,
        currencies=list(entry.currencies or []),
        owner=_owner(entry.meta),
        institution=institution,
        kind=_account_kind(parts[0], parts[-1]),
        external_ids=json.dumps({"last4": last4}) if last4 else None,
        meta=_meta_json(entry.meta),
    )


def _commodity_kind(currency: str, operating: set[str],
                    meta: Meta | None) -> str | None:
    declared = as_str(meta.get("kind")) if meta else ""
    if declared:
        return declared
    if currency in operating:
        return "fiat"
    if meta and any(k.startswith("asset_allocation_") for k in meta):
        return "fund"
    return None


def _commodity_rows(directives: list[_CommodityP], seen: set[str],
                    operating: set[str]) -> list[CommodityRow]:
    rows: dict[str, CommodityRow] = {}
    for entry in directives:
        meta = entry.meta or {}
        precision = meta.get("precision")
        rows[entry.currency] = CommodityRow(
            currency=entry.currency,
            name=as_str(meta.get("name")) or None,
            precision=precision if isinstance(precision, int) else None,
            kind=_commodity_kind(entry.currency, operating, meta),
            meta=_meta_json(entry.meta),
        )
    for currency in sorted(seen - rows.keys()):  # posting/price currencies without a directive
        rows[currency] = CommodityRow(
            currency=currency, name=None, precision=None,
            kind=_commodity_kind(currency, operating, None), meta=None)
    return [rows[c] for c in sorted(rows)]


# ---------------------------------------------------------------- the load
def _posting_rows(txn: _TxnP, txn_id: str, txn_row: TxnRow,
                  first_id: int, home: str, price_map: object) -> list[PostingRow]:
    rows: list[PostingRow] = []
    two_sided = len(txn.postings) == 2
    for index, posting in enumerate(txn.postings):
        number, currency = _amount_parts(posting.units)
        cost = (cast("_CostP", posting.cost)
                if isinstance(posting.cost, _COST_T) else None)
        price_number, price_currency = _amount_parts(posting.price)
        weight_number, weight_currency = _amount_parts(_convert.get_weight(posting))
        source_file, source_line = _lineage(posting.meta)
        other = txn.postings[1 - index].account if two_sided else None
        rows.append(PostingRow(
            posting_id=first_id + index,
            txn_id=txn_id,
            posting_index=index,
            date=txn.date,
            flag=posting.flag or txn.flag,
            payee=txn.payee,
            narration=txn.narration,
            tags=txn_row.tags,
            links=txn_row.links,
            account=posting.account,
            other_account=other,
            amount=number,
            currency=currency,
            cost_number=cost.number if cost else None,
            cost_currency=cost.currency if cost else None,
            cost_date=cost.date if cost else None,
            cost_label=cost.label if cost else None,
            price_number=price_number,
            price_currency=price_currency,
            weight_number=weight_number,
            weight_currency=weight_currency,
            amount_home=(_to_home(number, currency, txn.date, home, price_map)
                         if number is not None and currency else None),
            external_id=_external_id(posting.meta, txn.meta),
            source_file=source_file or txn_row.source_file,
            source_line=source_line if source_file else txn_row.source_line,
            meta=_meta_json(posting.meta),
        ))
    return rows


def _txn_row(txn: _TxnP, txn_id: str) -> TxnRow:
    source_file, source_line = _lineage(txn.meta)
    return TxnRow(
        txn_id=txn_id,
        date=txn.date,
        flag=txn.flag,
        payee=txn.payee,
        narration=txn.narration,
        tags=sorted(txn.tags),
        links=sorted(txn.links),
        n_postings=len(txn.postings),
        accounts=sorted({p.account for p in txn.postings}),
        source_file=source_file,
        source_line=source_line,
        meta=_meta_json(txn.meta),
    )


def _balance_check_row(entry: _BalanceP) -> BalanceCheckRow | None:
    expected, currency = _amount_parts(entry.amount)
    if expected is None or not currency:
        return None
    diff, _ = _amount_parts(entry.diff_amount)  # set by the loader only on failure
    diff = diff if diff is not None else Decimal(0)
    return BalanceCheckRow(
        date=entry.date, account=entry.account, currency=currency,
        expected=expected, actual=expected + diff, diff=diff)


def _price_row(entry: _PriceP, implicit_key: str) -> PriceRow | None:
    price, quote = _amount_parts(entry.amount)
    if price is None or not quote:
        return None
    source = "implicit" if entry.meta and implicit_key in entry.meta else "price"
    return PriceRow(commodity=entry.currency, quote_currency=quote,
                    date=entry.date, price=price, source=source)


def load_ledger() -> LoadedLedger:
    """Load the vault ledger and flatten it into plain rows."""
    require_vault()
    entries, errors, options = cast(
        "tuple[list[object], list[object], dict[str, Any]]",
        _loader.load_file(str(LEDGER)))
    operating = [c for c in cast("list[str]", options.get("operating_currency") or [])
                 if c]
    home = operating[0] if operating else "USD"

    # implicit prices (from costs and @ conversions) join the explicit ones so
    # holdings can be valued from their acquisition date onward
    priced_entries = cast(
        "list[object]", _implicit.add_implicit_prices(list(entries), options)[0])
    price_map: object = _prices.build_price_map(priced_entries)
    implicit_key = cast("str", _implicit.METADATA_FIELD)
    price_rows: dict[tuple[str, str, dt.date, str], PriceRow] = {}
    for entry in priced_entries:
        if isinstance(entry, _data.Price):
            row = _price_row(cast("_PriceP", entry), implicit_key)
            if row:
                key = (row.commodity, row.quote_currency, row.date, row.source)
                price_rows.setdefault(key, row)

    txns: list[TxnRow] = []
    postings: list[PostingRow] = []
    opens: dict[str, _OpenP] = {}
    closes: dict[str, _CloseP] = {}
    commodity_directives: list[_CommodityP] = []
    balance_checks: list[BalanceCheckRow] = []
    for entry in entries:
        if isinstance(entry, _data.Transaction):
            txn = cast("_TxnP", entry)
            txn_id = cast("str", _compare.hash_entry(entry))
            row = _txn_row(txn, txn_id)
            txns.append(row)
            postings.extend(_posting_rows(txn, txn_id, row,
                                          len(postings) + 1, home, price_map))
        elif isinstance(entry, _data.Open):
            opened = cast("_OpenP", entry)
            opens[opened.account] = opened
        elif isinstance(entry, _data.Close):
            closed = cast("_CloseP", entry)
            closes[closed.account] = closed
        elif isinstance(entry, _data.Commodity):
            commodity_directives.append(cast("_CommodityP", entry))
        elif isinstance(entry, _data.Balance):
            check = _balance_check_row(cast("_BalanceP", entry))
            if check:
                balance_checks.append(check)

    # explicit-price rows win over implicit ones on the same (commodity, quote, date)
    deduped: dict[tuple[str, str, dt.date], PriceRow] = {}
    for row in sorted(price_rows.values(), key=lambda r: (r.commodity, r.quote_currency,
                                                          r.date, r.source != "price")):
        deduped.setdefault((row.commodity, row.quote_currency, row.date), row)

    seen_currencies = ({c for p in postings if (c := p.currency)}
                       | {r.commodity for r in deduped.values()}
                       | {r.quote_currency for r in deduped.values()})
    routing = _rules_by_account()
    dates = [t.date for t in txns]
    return LoadedLedger(
        home_currency=home,
        postings=postings,
        transactions=txns,
        accounts=[_account_row(opens[a], closes.get(a), routing) for a in sorted(opens)],
        commodities=_commodity_rows(commodity_directives, seen_currencies, set(operating)),
        prices=sorted(deduped.values(), key=lambda r: (r.commodity, r.quote_currency, r.date)),
        balance_checks=balance_checks,
        min_date=min(dates) if dates else None,
        max_date=max(dates) if dates else None,
        beancount_version=cast("str", getattr(beancount, "__version__", "unknown")),
        n_load_errors=len(errors),
    )
