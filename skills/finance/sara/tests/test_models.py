"""Canonical models: round-trips, frozenness, and the boundary sanitizers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from sara.sources.model import (
    BankStatement,
    CanonBalance,
    CanonInvestTxn,
    CanonPosition,
    CanonTxn,
    escape,
    parse_ofx_amount,
)


def txn(**kw: object) -> CanonTxn:
    base: dict[str, object] = {"date": date(2026, 7, 1), "amount": Decimal("-42.50"),
                               "payee": "BLUE BOTTLE", "source_id": "F1"}
    base.update(kw)
    return CanonTxn.model_validate(base)


class TestRoundTrips:
    def test_txn_json_round_trip_preserves_decimal_exactly(self) -> None:
        t = txn(amount=Decimal("-42.50"), meta=(("plaid-category", "FOOD (high)"),))
        back = CanonTxn.model_validate_json(t.model_dump_json())
        assert back == t
        assert str(back.amount) == "-42.50"  # trailing zero survives

    def test_invest_round_trip_with_nested_cash_txn(self) -> None:
        a = CanonInvestTxn(kind="INVBANKTRAN", date=date(2026, 4, 1), source_id="V1",
                           cash_txn=txn(amount=Decimal("2000.00")))
        assert CanonInvestTxn.model_validate(a.model_dump()) == a

    def test_statement_round_trip(self) -> None:
        s = BankStatement(account_key="000009123", txns=(txn(),),
                          balance=CanonBalance(closing=Decimal("2170.37"),
                                               asof=date(2026, 6, 30)),
                          notes=("; note",))
        assert BankStatement.model_validate(s.model_dump()) == s

    def test_position_round_trip_full_precision(self) -> None:
        p = CanonPosition(ticker="VTSAX", units=Decimal("7426.96045"),
                          price=Decimal("130.1234"), mktval=Decimal("966363.53"))
        assert CanonPosition.model_validate(p.model_dump()).units == Decimal("7426.96045")


class TestBoundaryGuards:
    def test_models_are_frozen(self) -> None:
        with pytest.raises(ValidationError):
            txn().amount = Decimal(1)  # type: ignore[misc]

    @pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_money_rejected(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            txn(amount=Decimal(bad))
        with pytest.raises(ValidationError):
            CanonBalance(closing=Decimal(bad))
        with pytest.raises(ValidationError):
            CanonPosition(ticker="X", units=Decimal(bad))

    def test_unknown_fields_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CanonTxn.model_validate({"date": "2026-07-01", "amount": "1", "payee": "x",
                                     "surprise": True})

    def test_rule_text_falls_back_to_payee(self) -> None:
        assert txn(match_text="").rule_text == "BLUE BOTTLE"
        assert txn(match_text="raw desc").rule_text == "raw desc"


class TestEscape:
    def test_neutralizes_injection(self) -> None:
        evil = 'EVIL"\n2026-01-01 open Assets:Oops\\'
        assert escape(evil) == "EVIL' 2026-01-01 open Assets:Oops/"

    @given(st.text(max_size=200))
    def test_output_never_breaks_out_of_a_beancount_string(self, s: str) -> None:
        out = escape(s)
        assert '"' not in out
        assert "\\" not in out
        assert "\n" not in out and "\r" not in out
        assert out == escape(out)  # idempotent — safe to escape twice


class TestParseOfxAmount:
    @pytest.mark.parametrize(("raw", "want"), [
        ("2,500.00", Decimal("2500.00")),
        ("-1,234,567.89", Decimal("-1234567.89")),
        ("+42.5", Decimal("42.5")),
        ("-87.13", Decimal("-87.13")),
        ("0", Decimal("0")),
    ])
    def test_us_shapes_parse_exactly(self, raw: str, want: Decimal) -> None:
        assert parse_ofx_amount(raw) == want

    @pytest.mark.parametrize("raw", [
        "1.2.3",        # corrupt multi-dot
        "-1.234,56",    # EU decimal comma — ambiguous, never guess
        "12,34",        # comma in a non-thousands position
        "1,23.45",      # malformed grouping
        "", None, "abc", "1e3",  # not OFX number shapes
    ])
    def test_ambiguous_or_corrupt_returns_none(self, raw: str | None) -> None:
        assert parse_ofx_amount(raw) is None

    @given(st.decimals(min_value=Decimal("-9999999"), max_value=Decimal("9999999"),
                       places=2, allow_nan=False, allow_infinity=False))
    def test_round_trips_any_plain_2dp_amount(self, d: Decimal) -> None:
        assert parse_ofx_amount(f"{d:.2f}") == d
