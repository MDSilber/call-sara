"""Every Plaid mapper branch, and the accounting identities the report
stands on: counts reconcile by construction, sums survive the float->Decimal
crossing to the cent, signs always flip into ledger convention.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sara.sources.model import CanonInvestTxn, CanonTxn
from sara.sources.plaid_src import (
    PlaidAccount,
    UnmappedRow,
    map_investment_transaction,
    map_investments,
    map_sync_response,
    map_transaction,
)

FIXTURES = Path(__file__).parent / "fixtures"
D = Decimal


def base_txn(**kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "transaction_id": "t1", "account_id": "a1", "date": "2026-07-01",
        "amount": 23.40, "name": "WHOLE FOODS MARKET #123",
        "merchant_name": "Whole Foods", "pending": False,
        "payment_channel": "in store",
        "personal_finance_category": {"primary": "FOOD_AND_DRINK",
                                      "detailed": "FOOD_AND_DRINK_GROCERIES",
                                      "confidence_level": "VERY_HIGH"},
    }
    d.update(kw)
    return d


class TestTransactionMapper:
    def test_full_row_maps_with_sign_flip_and_category(self) -> None:
        t = map_transaction(base_txn())
        assert isinstance(t, CanonTxn)
        assert t.amount == D("-23.40")  # plaid positive = money out = ledger negative
        assert t.payee == "Whole Foods"  # merchant_name preferred for display
        assert t.rule_text == "WHOLE FOODS MARKET #123"  # rules match the raw name
        assert t.source_id == "t1" and t.account_key == "a1"
        assert t.meta == (("plaid-category", "FOOD_AND_DRINK_GROCERIES (very_high)"),)

    def test_category_absent_means_no_meta(self) -> None:
        t = map_transaction(base_txn(personal_finance_category=None))
        assert isinstance(t, CanonTxn) and t.meta == ()

    def test_inflow_becomes_positive(self) -> None:
        t = map_transaction(base_txn(amount=-2500.00, merchant_name=None))
        assert isinstance(t, CanonTxn) and t.amount == D("2500.00")
        assert t.payee == "WHOLE FOODS MARKET #123"

    @pytest.mark.parametrize("hole", ["transaction_id", "date", "amount", "account_id"])
    def test_missing_required_field_is_unmapped_with_reference(self, hole: str) -> None:
        row = base_txn()
        row[hole] = None
        out = map_transaction(row)
        assert isinstance(out, UnmappedRow)
        assert "2026-07-01" in out.raw_ref or hole == "date"

    def test_garbage_amount_is_unmapped_never_guessed(self) -> None:
        assert isinstance(map_transaction(base_txn(amount=True)), UnmappedRow)
        assert isinstance(map_transaction(base_txn(amount="soon")), UnmappedRow)

    def test_payee_is_escaped_against_ledger_injection(self) -> None:
        t = map_transaction(base_txn(merchant_name='EVIL"\n2026-01-01 open Assets:Oops'))
        assert isinstance(t, CanonTxn) and '"' not in t.payee and "\n" not in t.payee


class TestSyncBatch:
    def pages(self) -> list[dict[str, Any]]:
        return json.loads((FIXTURES / "demo.sync.json").read_text())

    def test_counts_reconcile_by_construction(self) -> None:
        batch = map_sync_response(self.pages())
        assert batch.reconciles()
        assert batch.fetched_added == 5 and batch.fetched_modified == 0
        assert len(batch.added) == 4
        assert batch.excluded_pending == ("plaid-txn-005-pending",)
        assert batch.unmapped == ()

    def test_mapped_sum_equals_fixture_sum_to_the_cent(self) -> None:
        """The mapper's totals ARE the fixture's totals (sign-flipped):
        nothing rounds, nothing drifts across the float->Decimal boundary."""
        pages = self.pages()
        fixture_total = sum(
            D(str(t["amount"])) for p in pages for t in p["added"] if not t["pending"])
        batch = map_sync_response(pages)
        assert sum((t.amount for t in batch.added), D(0)) == -fixture_total

    def test_cursor_comes_from_the_last_page(self) -> None:
        assert map_sync_response(self.pages()).next_cursor == "cursor-final"

    def test_accounts_deduped_across_pages_with_balances(self) -> None:
        batch = map_sync_response(self.pages())
        by_id = {a.account_id: a for a in batch.accounts}
        assert set(by_id) == {"plaid-acct-checking", "plaid-acct-card"}
        assert by_id["plaid-acct-checking"].current == D("4242.33")

    def test_modified_and_removed_flow_through(self) -> None:
        batch = map_sync_response(json.loads((FIXTURES / "demo2.sync.json").read_text()))
        assert batch.reconciles()
        assert [t.source_id for t in batch.modified] == ["plaid-txn-003"]
        assert batch.modified[0].amount == D("-12.25")
        assert batch.removed_ids == ("plaid-txn-004", "plaid-txn-999")

    def test_liability_balance_sign_flips_for_comparison(self) -> None:
        card = PlaidAccount(account_id="c", type="credit", current=D("542.10"))
        checking = PlaidAccount(account_id="k", type="depository", current=D("100"))
        assert card.ledger_signed_current() == D("-542.10")
        assert checking.ledger_signed_current() == D("100")
        assert PlaidAccount(account_id="x").ledger_signed_current() is None


TICKERS = {"sec-tif": "TIF", "sec-twc": "TWC", "sec-sweep": "sec-sweep"}


def inv(**kw: Any) -> dict[str, Any]:
    d: dict[str, Any] = {
        "investment_transaction_id": "i1", "account_id": "a1", "security_id": "sec-tif",
        "date": "2026-04-05", "name": "BUY", "quantity": 10.5, "price": 40.0,
        "amount": 420.00, "type": "buy", "subtype": "buy",
    }
    d.update(kw)
    return d


class TestInvestmentMapper:
    def test_buy_units_in_cash_out(self) -> None:
        a = map_investment_transaction(inv(), TICKERS)
        assert isinstance(a, CanonInvestTxn)
        assert (a.kind, a.ticker, a.units, a.total) == ("BUYSTOCK", "TIF", D("10.5"), D("-420.00"))

    def test_sell_units_out_cash_in(self) -> None:
        a = map_investment_transaction(
            inv(type="sell", subtype="sell", quantity=-3.0, price=42.0, amount=-126.00), TICKERS)
        assert isinstance(a, CanonInvestTxn)
        assert (a.kind, a.units, a.total) == ("SELLSTOCK", D("-3"), D("126.00"))

    def test_dividend_reinvestment_is_a_reinvest_with_income_leg(self) -> None:
        a = map_investment_transaction(
            inv(subtype="dividend reinvestment", quantity=0.31, amount=12.40), TICKERS)
        assert isinstance(a, CanonInvestTxn)
        assert (a.kind, a.income, a.units, a.total) == ("REINVEST", "DIV", D("0.31"), D("-12.40"))

    @pytest.mark.parametrize(("subtype", "income"), [
        ("dividend", "DIV"), ("interest", "INTEREST"),
        ("long-term capital gain", "CGLONG"), ("short-term capital gain", "CGSHORT"),
    ])
    def test_cash_income_subtypes(self, subtype: str, income: str) -> None:
        a = map_investment_transaction(
            inv(type="cash", subtype=subtype, quantity=0, price=0, amount=-6.00), TICKERS)
        assert isinstance(a, CanonInvestTxn)
        assert (a.kind, a.income, a.total) == ("INCOME", income, D("6.00"))

    @pytest.mark.parametrize("subtype", ["contribution", "deposit", "withdrawal"])
    def test_cash_flows_become_bank_transactions(self, subtype: str) -> None:
        sign = 1 if subtype == "withdrawal" else -1
        a = map_investment_transaction(
            inv(type="cash", subtype=subtype, security_id=None, amount=sign * 2000.0), TICKERS)
        assert isinstance(a, CanonInvestTxn) and a.kind == "INVBANKTRAN"
        assert a.cash_txn is not None
        assert a.cash_txn.amount == D(-sign * 2000)  # plaid sign flips to ledger sign

    def test_fee_and_transfer_route_through_cash(self) -> None:
        for ty in ("fee", "transfer"):
            a = map_investment_transaction(inv(type=ty, subtype="", amount=25.0), TICKERS)
            assert isinstance(a, CanonInvestTxn) and a.kind == "INVBANKTRAN"

    def test_unknown_type_and_subtype_are_unmapped_loudly(self) -> None:
        for kw in ({"type": "options-exercise"}, {"type": "cash", "subtype": "stock distribution"}):
            out = map_investment_transaction(inv(**kw), TICKERS)
            assert isinstance(out, UnmappedRow) and "book by hand" in out.reason

    def test_buy_without_usable_ticker_is_unmapped(self) -> None:
        out = map_investment_transaction(inv(security_id="sec-sweep"), TICKERS)
        assert isinstance(out, UnmappedRow) and "no usable ticker" in out.reason

    def test_missing_core_fields_unmapped(self) -> None:
        out = map_investment_transaction(inv(date=None), TICKERS)
        assert isinstance(out, UnmappedRow)

    def test_zero_units_with_cash_stays_on_its_invest_kind(self) -> None:
        # qty-unreported degradation: dedupe needs the ticker/total context,
        # and the renderer's zero-units wall books any survivor as cash
        a = map_investment_transaction(inv(quantity=0, price=0, amount=25.00), TICKERS)
        assert isinstance(a, CanonInvestTxn)
        assert (a.kind, a.units, a.total) == ("BUYSTOCK", D("0"), D("-25.00"))

    def test_zero_units_zero_cash_is_dropped_loudly(self) -> None:
        out = map_investment_transaction(inv(quantity=0, price=0, amount=0), TICKERS)
        assert isinstance(out, UnmappedRow)
        assert "nothing to book" in out.reason

    def test_sub_penny_units_stay_a_real_position(self) -> None:
        a = map_investment_transaction(
            inv(quantity=0.0001, price=100.0, amount=0.01), TICKERS)
        assert isinstance(a, CanonInvestTxn)
        assert (a.kind, a.units, a.total) == ("BUYSTOCK", D("0.0001"), D("-0.01"))


class TestInvestmentBatch:
    def test_fixture_reconciles_and_positions_map(self) -> None:
        pages = json.loads((FIXTURES / "vg.investments.json").read_text())
        holdings = json.loads((FIXTURES / "vg.holdings.json").read_text())
        batch = map_investments(pages, holdings)
        assert batch.reconciles()
        assert batch.fetched == 10 and len(batch.actions) == 8 and len(batch.unmapped) == 2
        pos = batch.positions["plaid-acct-brokerage"]
        assert {(p.ticker, p.units) for p in pos} == {("TIF", D("7.81")), ("TWC", D("10.5")),
                                                      ("VMOT", D("0.0001"))}
        assert pos[0].priced_asof == date(2026, 7, 1)


json_money = st.one_of(
    st.integers(min_value=-10_000_000, max_value=10_000_000).map(lambda c: c / 100),
    st.integers(min_value=-99999, max_value=99999),
)


class TestMoneyCrossing:
    @settings(max_examples=300, deadline=None)
    @given(amount=json_money)
    def test_any_2dp_json_amount_crosses_exactly(self, amount: float | int) -> None:
        """Sum-preservation at the row level: the Decimal the mapper books is
        exactly the fixture's printed amount, sign-flipped — for every value
        a 2dp JSON feed can carry."""
        t = map_transaction(base_txn(amount=amount, personal_finance_category=None))
        assert isinstance(t, CanonTxn)
        assert t.amount == -D(json.dumps(amount))
