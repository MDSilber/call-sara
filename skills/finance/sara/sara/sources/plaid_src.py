"""Plaid -> canonical models.

Pure mappers over the JSON shapes Plaid's API returns (`/transactions/sync`,
`/investments/holdings/get`, `/investments/transactions/get`) — dicts in,
canonical frozen models out, so the whole lane is testable from fixture
files with no network. The API-calling client lives in `sara.plaid_api`;
the daemon (`sara.ingest`) glues the two.

Data-integrity contract (the whole point of this lane):
  * Every fetched row is accounted for: mapped, excluded-as-pending, or
    listed in `unmapped` with its id and reason — NOTHING drops silently.
    The ingest verification report reconciles these counts exactly.
  * Amounts convert float->str->Decimal (exact for Plaid's 2dp money) and
    must be finite; a row that fails validation lands in `unmapped`.
  * SIGN FLIP: Plaid's `amount` is positive when money LEAVES the account.
    The ledger convention (account perspective) is the opposite, so
    canonical amount = -plaid_amount. Property-tested.
  * PENDING EXCLUDED: pending transactions are never imported — they lack
    final ids/amounts and would churn. The posted row arrives later with
    its own transaction_id. Excluded ids are reported, not dropped.
  * Plaid's personal_finance_category (primary/detailed/confidence) rides
    along as `plaid-category:` metadata on every written entry — signal
    banked for a future classification pass; rules.toml [[payee_rules]]
    remain the only categorizer today.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import Field

from sara.sources.invest_ofx import TICKER_RE
from sara.sources.model import (
    CanonInvestTxn,
    CanonPosition,
    CanonTxn,
    MetaItems,
    SaraModel,
    escape,
)
from sara.typed import as_dict, as_dicts, as_list, as_str

Raw = dict[str, Any]
ZERO = Decimal(0)


class UnmappedRow(SaraModel):
    """A fetched row that could not become canonical — always reported."""

    source_id: str
    reason: str
    raw_ref: str  # compact human reference to the payload (kind/date/amount)


class PlaidAccount(SaraModel):
    """One account as Plaid reports it in the same sync — balance fuel for
    the verification report."""

    account_id: str
    name: str = ""
    mask: str = ""
    type: str = ""  # depository / credit / investment / loan
    current: Decimal | None = None
    available: Decimal | None = None

    def ledger_signed_current(self) -> Decimal | None:
        """Plaid reports liabilities as positive magnitudes; the ledger books
        them negative. Flip credit/loan balances so the two are comparable."""
        if self.current is None:
            return None
        return -self.current if self.type in ("credit", "loan") else self.current


class PlaidTxnBatch(SaraModel):
    """One /transactions/sync pass, fully accounted for."""

    added: tuple[CanonTxn, ...] = ()
    modified: tuple[CanonTxn, ...] = ()
    removed_ids: tuple[str, ...] = ()
    accounts: tuple[PlaidAccount, ...] = ()
    excluded_pending: tuple[str, ...] = ()  # transaction_ids, reported
    unmapped: tuple[UnmappedRow, ...] = ()
    fetched_added: int = 0
    fetched_modified: int = 0
    next_cursor: str = ""

    def reconciles(self) -> bool:
        """fetched == mapped + pending-excluded + unmapped, exactly."""
        pending = set(self.excluded_pending)
        mapped = len(self.added) + len(self.modified)
        return (self.fetched_added + self.fetched_modified
                == mapped + len(pending) + len(self.unmapped))


class PlaidInvestBatch(SaraModel):
    """One investments pull (holdings + transactions), fully accounted for."""

    actions: tuple[CanonInvestTxn, ...] = ()
    positions: dict[str, tuple[CanonPosition, ...]] = Field(default_factory=dict)  # account_id -> positions
    accounts: tuple[PlaidAccount, ...] = ()
    unmapped: tuple[UnmappedRow, ...] = ()
    fetched: int = 0

    def reconciles(self) -> bool:
        return self.fetched == len(self.actions) + len(self.unmapped)


def _dec(value: object) -> Decimal | None:
    """JSON number -> exact Decimal via the shortest-repr string; None for
    anything non-numeric or non-finite. (str() of a JSON-parsed 2dp money
    value always round-trips exactly.)"""
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def _date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    return None


def _category_meta(txn: Raw) -> MetaItems:
    pfc = as_dict(txn.get("personal_finance_category"))
    if not pfc:
        return ()
    primary = str(pfc.get("primary") or "")
    detailed = str(pfc.get("detailed") or "")
    confidence = str(pfc.get("confidence_level") or "")
    if not (primary or detailed):
        return ()
    label = detailed or primary
    if confidence:
        label = f"{label} ({confidence.lower()})"
    return (("plaid-category", label),)


def _ref(kind: str, txn: Raw) -> str:
    return (f"{kind} {txn.get('date', '?')} {txn.get('amount', '?')} "
            f"{str(txn.get('name') or txn.get('type') or '')[:40]!r}")


def map_transaction(txn: Raw) -> CanonTxn | UnmappedRow:
    """One /transactions/sync row -> canonical (or an accounted-for miss)."""
    sid = str(txn.get("transaction_id") or "")
    when = _date(txn.get("date"))
    amount = _dec(txn.get("amount"))
    account_id = str(txn.get("account_id") or "")
    name = str(txn.get("merchant_name") or "") or str(txn.get("name") or "")
    if not sid:
        return UnmappedRow(source_id="", reason="missing transaction_id",
                           raw_ref=_ref("txn", txn))
    if when is None or amount is None or not account_id:
        return UnmappedRow(source_id=sid, reason="missing/unparseable date, amount, or account_id",
                           raw_ref=_ref("txn", txn))
    return CanonTxn(
        date=when,
        amount=-amount,  # Plaid: positive = money out; ledger: the opposite
        payee=escape(name),
        match_text=str(txn.get("name") or name),
        source_id=sid,
        account_key=account_id,
        kind=str(txn.get("payment_channel") or ""),
        meta=_category_meta(txn),
    )


def map_account(acct: Raw) -> PlaidAccount:
    balances = as_dict(acct.get("balances"))
    return PlaidAccount(
        account_id=str(acct.get("account_id") or ""),
        name=str(acct.get("name") or ""),
        mask=str(acct.get("mask") or ""),
        type=str(acct.get("type") or ""),
        current=_dec(balances.get("current")),
        available=_dec(balances.get("available")),
    )


def map_sync_response(pages: list[Raw]) -> PlaidTxnBatch:
    """All pages of one /transactions/sync pass -> one accounted batch.
    `modified` rows come back as full canonical transactions — the writer
    replaces them by source_id; `removed` ids are reported, never deleted."""
    added: list[CanonTxn] = []
    modified: list[CanonTxn] = []
    removed: list[str] = []
    pending: list[str] = []
    unmapped: list[UnmappedRow] = []
    accounts: dict[str, PlaidAccount] = {}
    fetched_added = fetched_modified = 0
    cursor = ""
    for page in pages:
        for acct in as_dicts(page.get("accounts")):
            a = map_account(acct)
            if a.account_id:
                accounts[a.account_id] = a
        for bucket, sink in (("added", added), ("modified", modified)):
            rows = as_list(page.get(bucket))
            if bucket == "added":
                fetched_added += len(rows)
            else:
                fetched_modified += len(rows)
            for raw_txn in rows:
                txn = as_dict(raw_txn)
                if not txn:
                    unmapped.append(UnmappedRow(source_id="", reason="non-object row",
                                                raw_ref=f"{bucket} {str(raw_txn)[:60]!r}"))
                    continue
                if txn.get("pending"):
                    pending.append(str(txn.get("transaction_id") or "?"))
                    continue
                out = map_transaction(txn)
                if isinstance(out, UnmappedRow):
                    unmapped.append(out)
                else:
                    sink.append(out)
        for r in as_list(page.get("removed")):
            rid = str(as_dict(r).get("transaction_id") or r or "")
            if rid:
                removed.append(rid)
        cursor = as_str(page.get("next_cursor"), cursor) or cursor
    return PlaidTxnBatch(
        added=tuple(added), modified=tuple(modified), removed_ids=tuple(removed),
        accounts=tuple(accounts.values()), excluded_pending=tuple(pending),
        unmapped=tuple(unmapped), fetched_added=fetched_added,
        fetched_modified=fetched_modified, next_cursor=cursor,
    )


# ------------------------------------------------------------- investments
def security_tickers(securities: list[Raw]) -> dict[str, str]:
    """security_id -> ticker (falling back to the id, like OFX UNIQUEID)."""
    out: dict[str, str] = {}
    for sec in securities:
        sid = str(sec.get("security_id") or "")
        if sid:
            out[sid] = str(sec.get("ticker_symbol") or "") or sid
    return out


def security_names(securities: list[Raw]) -> dict[str, str]:
    return {str(s.get("security_id") or ""): str(s.get("name") or "")
            for s in securities if s.get("security_id")}


_INCOME_BY_SUBTYPE = {
    "dividend": "DIV",
    "qualified dividend": "DIV",
    "non-qualified dividend": "DIV",
    "interest": "INTEREST",
    "long-term capital gain": "CGLONG",
    "short-term capital gain": "CGSHORT",
    "capital gain": "CGLONG",
}
_CASH_FLOW_SUBTYPES = ("contribution", "deposit", "withdrawal", "request", "transfer",
                       "account fee", "management fee", "legal fee", "adjustment")


def map_investment_transaction(txn: Raw, tickers: dict[str, str]) -> CanonInvestTxn | UnmappedRow:
    """One /investments/transactions row -> canonical action.

    Plaid types: buy / sell / cash / fee / transfer, with granular subtypes.
    Anything without a faithful canonical shape is returned as unmapped —
    the report names it and the row waits for a human, never guesses.
    """
    sid = str(txn.get("investment_transaction_id") or "")
    when = _date(txn.get("date"))
    amount = _dec(txn.get("amount"))
    account_id = str(txn.get("account_id") or "")
    ty = str(txn.get("type") or "").lower()
    subtype = str(txn.get("subtype") or "").lower()
    name = str(txn.get("name") or "")
    if not sid or when is None or amount is None or not account_id:
        return UnmappedRow(source_id=sid, reason="missing/unparseable id, date, amount, or account_id",
                           raw_ref=_ref("invest", txn))
    quantity = _dec(txn.get("quantity")) or ZERO
    price = _dec(txn.get("price")) or ZERO
    ticker = tickers.get(str(txn.get("security_id") or ""), str(txn.get("security_id") or ""))

    def _invest(kind: str, units: Decimal, total: Decimal, income: str = "") -> CanonInvestTxn | UnmappedRow:
        if not TICKER_RE.match(ticker):
            return UnmappedRow(source_id=sid,
                               reason=f"security {ticker!r} has no usable ticker — book by hand",
                               raw_ref=_ref("invest", txn))
        return CanonInvestTxn(kind=kind, date=when, source_id=sid, ticker=ticker,
                              units=units, price=price, total=total, income=income,
                              account_key=account_id)

    def _cash(income_kind: str = "") -> CanonInvestTxn:
        # Plaid: positive amount = cash out of the account; flip for the ledger.
        cash = CanonTxn(date=when, amount=-amount, payee=escape(name),
                        match_text=name, source_id=sid, account_key=account_id,
                        kind=(subtype or ty).upper(), meta=())
        if income_kind:
            return CanonInvestTxn(kind="INCOME", date=when, source_id=sid,
                                  ticker=ticker if TICKER_RE.match(ticker) else "",
                                  total=-amount, income=income_kind,
                                  account_key=account_id)
        return CanonInvestTxn(kind="INVBANKTRAN", date=when, source_id=sid,
                              cash_txn=cash, account_key=account_id)

    if ty == "buy":
        if "reinvest" in subtype:
            # income earned convention: negative total = the income leg
            return _invest("REINVEST", abs(quantity), -abs(amount), income="DIV")
        return _invest("BUYSTOCK", abs(quantity), -abs(amount))
    if ty == "sell":
        return _invest("SELLSTOCK", -abs(quantity), abs(amount))
    if ty == "cash":
        income = _INCOME_BY_SUBTYPE.get(subtype, "")
        if income:
            return _cash(income_kind=income)
        if subtype in _CASH_FLOW_SUBTYPES or not subtype:
            return _cash()
        return UnmappedRow(source_id=sid, reason=f"cash subtype {subtype!r} not mapped — book by hand",
                           raw_ref=_ref("invest", txn))
    if ty in ("fee", "transfer"):
        return _cash()
    return UnmappedRow(source_id=sid, reason=f"investment type {ty!r} not mapped — book by hand",
                       raw_ref=_ref("invest", txn))


def map_holdings(holdings: list[Raw], securities: list[Raw]) -> dict[str, list[CanonPosition]]:
    """/investments/holdings/get -> {account_id: positions}."""
    tickers = security_tickers(securities)
    names = security_names(securities)
    out: dict[str, list[CanonPosition]] = {}
    for h in holdings:
        account_id = str(h.get("account_id") or "")
        sid = str(h.get("security_id") or "")
        units = _dec(h.get("quantity"))
        if not account_id or units is None:
            continue  # a holding without account/quantity carries no reconcile signal
        out.setdefault(account_id, []).append(CanonPosition(
            ticker=tickers.get(sid, sid),
            units=units,
            price=_dec(h.get("institution_price")) or ZERO,
            mktval=_dec(h.get("institution_value")) or ZERO,
            priced_asof=_date(h.get("institution_price_as_of")),
            name=names.get(sid, ""),
        ))
    return out


def map_investments(txn_pages: list[Raw], holdings_resp: Raw | None) -> PlaidInvestBatch:
    """Investments transactions pages + holdings -> one accounted batch."""
    securities: list[Raw] = []
    rows: list[Raw] = []
    accounts: dict[str, PlaidAccount] = {}
    for page in txn_pages + ([holdings_resp] if holdings_resp else []):
        securities.extend(as_dicts(page.get("securities")))
        for acct in as_dicts(page.get("accounts")):
            a = map_account(acct)
            if a.account_id:
                accounts[a.account_id] = a
    for page in txn_pages:
        rows.extend(as_dicts(page.get("investment_transactions")))
    tickers = security_tickers(securities)
    actions: list[CanonInvestTxn] = []
    unmapped: list[UnmappedRow] = []
    for t in rows:
        out = map_investment_transaction(t, tickers)
        if isinstance(out, UnmappedRow):
            unmapped.append(out)
        else:
            actions.append(out)
    positions: dict[str, tuple[CanonPosition, ...]] = {}
    if holdings_resp:
        holdings = as_dicts(holdings_resp.get("holdings"))
        positions = {k: tuple(v) for k, v in map_holdings(holdings, securities).items()}
    return PlaidInvestBatch(actions=tuple(actions), positions=positions,
                            accounts=tuple(accounts.values()),
                            unmapped=tuple(unmapped), fetched=len(rows))
