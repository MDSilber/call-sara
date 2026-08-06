"""The write gates — continuity, atomic append + rollback, addressed edits.

A poisoned batch must leave the ledger byte-identical to before; a broken
balance chain must be named, not papered over. These are the tests that
guard actual money.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from sara.ledger.writer import (
    DISCREPANCY,
    UNVERIFIABLE,
    VERIFIED,
    append_to_ledger,
    assertion_date,
    check_continuity_ledger,
    check_continuity_rows,
    emit,
    replace_by_source_id,
)
from tests.conftest import needs_venv

CHECKING = "Assets:US:Demo:Checking0766"
D = Decimal


def ledger_snapshot(vault: Path) -> dict[str, str]:
    return {p.name: p.read_text() for p in (vault / "ledger").glob("*.beancount")}


class TestRowContinuity:
    def test_chains_oldest_first(self) -> None:
        rows = [(D("800.00"), D("1300.00")), (D("-100.00"), D("1200.00")),
                (D("4.10"), D("1204.10"))]
        tag, detail, closing = check_continuity_rows(rows[::-1])  # oldest-first file
        assert tag == VERIFIED and closing == D("1204.10")
        assert "opening 500.00" in detail

    def test_chains_newest_first(self) -> None:
        rows = [(D("4.10"), D("1204.10")), (D("-100.00"), D("1200.00")),
                (D("800.00"), D("1300.00"))]
        tag, _, closing = check_continuity_rows(rows)
        assert tag == VERIFIED and closing == D("1204.10")

    def test_broken_chain_is_a_discrepancy(self) -> None:
        rows = [(D("4.10"), D("1204.10")), (D("-100.00"), D("1150.00")),
                (D("800.00"), D("1300.00"))]
        tag, detail, closing = check_continuity_rows(rows)
        assert tag == DISCREPANCY and closing is None
        assert "does not chain" in detail

    def test_missing_balance_column_is_unverifiable(self) -> None:
        tag, _, closing = check_continuity_rows([(D("1.00"), None)])
        assert tag == UNVERIFIABLE and closing is None
        assert check_continuity_rows([])[0] == UNVERIFIABLE


class TestLedgerContinuity:
    def test_no_closing_balance_is_unverifiable(self, fresh_vault: Path) -> None:
        tag, detail = check_continuity_ledger(CHECKING, None, date(2026, 7, 1), [])
        assert tag == UNVERIFIABLE and "no closing balance" in detail

    def test_no_asof_is_unverifiable(self, fresh_vault: Path) -> None:
        tag, _ = check_continuity_ledger(CHECKING, D("1.00"), None, [])
        assert tag == UNVERIFIABLE

    @needs_venv
    def test_verified_when_ledger_plus_import_hits_closing(self, fresh_vault: Path) -> None:
        # seed: checking opened at 1765.73 (conftest OPENING)
        kept = [(date(2026, 7, 1), D("100.00"))]
        tag, detail = check_continuity_ledger(CHECKING, D("1865.73"), date(2026, 7, 2), kept)
        assert tag == VERIFIED and "= closing 1865.73" in detail

    @needs_venv
    def test_discrepancy_names_the_delta(self, fresh_vault: Path) -> None:
        tag, detail = check_continuity_ledger(CHECKING, D("2000.00"), date(2026, 7, 2),
                                              [(date(2026, 7, 1), D("100.00"))])
        assert tag == DISCREPANCY
        assert "off by +134.27" in detail  # 2000 - (1765.73 + 100)

    @needs_venv
    def test_rows_after_asof_do_not_count(self, fresh_vault: Path) -> None:
        kept = [(date(2026, 7, 1), D("100.00")), (date(2026, 7, 9), D("-40.00"))]
        tag, _ = check_continuity_ledger(CHECKING, D("1865.73"), date(2026, 7, 2), kept)
        assert tag == VERIFIED  # the 7-09 row postdates the closing balance


class TestAssertionDate:
    def test_backs_off_two_days_then_day_after(self) -> None:
        assert assertion_date(date(2026, 6, 30), date(2026, 6, 20)) == date(2026, 6, 29)

    def test_never_before_last_imported_row(self) -> None:
        assert assertion_date(date(2026, 6, 30), date(2026, 6, 30)) == date(2026, 7, 1)

    def test_capped_at_statement_end_plus_one(self) -> None:
        # a row AFTER the balance as-of date must not push the assertion past it
        assert assertion_date(date(2026, 7, 15), date(2026, 7, 18)) == date(2026, 7, 16)


class TestAtomicWriteAndRollback:
    def entry(self, day: int, amount: str, counter: str = "Expenses:Uncategorized") -> tuple[date, str]:
        when = date(2026, 7, day)
        return when, emit(when, "TEST ROW", {"import-hash": f"{day:016x}"},
                          CHECKING, D(amount), counter)

    @needs_venv
    def test_append_then_bean_check_passes(self, fresh_vault: Path) -> None:
        paths = append_to_ledger([self.entry(1, "-10.00")])
        assert paths == ["ledger/2026.beancount"]
        assert 'import-hash: "0000000000000001"' in (fresh_vault / "ledger" / "2026.beancount").read_text()

    @needs_venv
    def test_poisoned_entry_rolls_back_every_file(self, fresh_vault: Path) -> None:
        before = ledger_snapshot(fresh_vault)
        bad = [self.entry(1, "-10.00"),  # fine on its own
               self.entry(2, "-5.00", "Expenses:Never:Opened")]  # bean-check must reject
        with pytest.raises(SystemExit, match="rolled back"):
            append_to_ledger(bad)
        assert ledger_snapshot(fresh_vault) == before  # byte-identical, both files

    @needs_venv
    def test_new_year_file_rollback_removes_it(self, fresh_vault: Path) -> None:
        before = ledger_snapshot(fresh_vault)
        when = date(2027, 1, 2)
        bad = (when, emit(when, "BAD", {}, CHECKING, D("1.00"), "Expenses:Never:Opened"))
        with pytest.raises(SystemExit):
            append_to_ledger([bad])
        assert ledger_snapshot(fresh_vault) == before
        assert not (fresh_vault / "ledger" / "2027.beancount").exists()

    def test_missing_main_refuses(self, fresh_vault: Path) -> None:
        (fresh_vault / "ledger" / "main.beancount").unlink()
        with pytest.raises(SystemExit, match="no ledger"):
            append_to_ledger([self.entry(1, "-1.00")])


class TestReplaceBySourceId:
    def seed(self, vault: Path) -> str:
        entry = emit(date(2026, 7, 5), "COFFEE", {"import-hash": "ab" * 8,
                                                  "plaid-id": "plaid-x1"},
                     CHECKING, D("-11.25"), "Expenses:Food:Dining")
        year = vault / "ledger" / "2026.beancount"
        year.write_text(year.read_text() + "\n" + entry)
        return entry

    @needs_venv
    def test_replaces_exactly_one_entry_in_place(self, fresh_vault: Path) -> None:
        self.seed(fresh_vault)
        new = emit(date(2026, 7, 5), "COFFEE", {"import-hash": "cd" * 8,
                                                "plaid-id": "plaid-x1"},
                   CHECKING, D("-12.25"), "Expenses:Food:Dining")
        replace_by_source_id({"plaid-x1": new})
        text = (fresh_vault / "ledger" / "2026.beancount").read_text()
        assert "-12.25 USD" in text and "-11.25 USD" not in text
        assert text.count('plaid-id: "plaid-x1"') == 1

    def test_unknown_source_id_refuses_untouched(self, fresh_vault: Path) -> None:
        before = ledger_snapshot(fresh_vault)
        with pytest.raises(SystemExit, match="no ledger entry carries"):
            replace_by_source_id({"plaid-nope": "x"})
        assert ledger_snapshot(fresh_vault) == before

    @needs_venv
    def test_bad_replacement_rolls_back(self, fresh_vault: Path) -> None:
        self.seed(fresh_vault)
        before = ledger_snapshot(fresh_vault)
        broken = emit(date(2026, 7, 5), "COFFEE", {"plaid-id": "plaid-x1"},
                      CHECKING, D("-12.25"), "Expenses:Never:Opened")
        with pytest.raises(SystemExit, match="rolled back"):
            replace_by_source_id({"plaid-x1": broken})
        assert ledger_snapshot(fresh_vault) == before
