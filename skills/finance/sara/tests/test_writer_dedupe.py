"""The dedupe tiers — where a bug means double-booked money.

Tier a: source_id exact (FITID / plaid-id, survives edits).
Tier b: import-hash content match, honored only when source ids don't disagree.
Tier c: ±5-day fuzzy, legacy ledger entries only (needs bean-query).
Plus the two properties the whole design leans on: same batch twice = zero
new, and the Decimal hash is byte-identical to the float-era hash for every
2dp amount already recorded in real vaults.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from sara.ledger.writer import (
    AccountDedupe,
    existing_ids,
    existing_index,
    import_hash,
    is_duplicate,
)
from tests.conftest import needs_venv

CHECKING = "Assets:US:Demo:Checking0766"


def write_entry(vault: Path, body: str) -> None:
    year = vault / "ledger" / "2026.beancount"
    old = year.read_text() if year.exists() else ""
    year.write_text(old + ("\n" if old and not old.endswith("\n\n") else "") + body)


def imported_entry(when: str, amount: str, payee: str, h: str, fitid: str = "",
                   key: str = "fitid") -> str:
    meta = f'  import-hash: "{h}"\n' + (f'  {key}: "{fitid}"\n' if fitid else "")
    return (f'{when} * "{payee}" ""\n{meta}'
            f"  {CHECKING}   {amount} USD\n  Expenses:Uncategorized\n")


class TestSourceIdTier:
    def test_recorded_fitid_wins_even_when_content_changed(self, fresh_vault: Path) -> None:
        h = import_hash(date(2026, 7, 1), Decimal("-10.00"), "OLD NAME", CHECKING)
        write_entry(fresh_vault, imported_entry("2026-07-01", "-10.00", "OLD NAME", h, "F42"))
        d = AccountDedupe(CHECKING, *existing_ids())
        # bank edited payee AND amount; the FITID is still the same transaction
        assert d.check(date(2026, 7, 1), Decimal("-12.00"), "NEW NAME", "F42") == "fitid"

    def test_plaid_id_metadata_reads_as_the_source_id(self, fresh_vault: Path) -> None:
        h = import_hash(date(2026, 7, 1), Decimal("-10.00"), "COFFEE", CHECKING)
        write_entry(fresh_vault, imported_entry("2026-07-01", "-10.00", "COFFEE", h,
                                                "plaid-txn-9", key="plaid-id"))
        d = AccountDedupe(CHECKING, *existing_ids())
        assert d.check(date(2026, 7, 1), Decimal("-10.00"), "COFFEE", "plaid-txn-9") == "fitid"

    def test_in_batch_repeat_id_dedupes(self, fresh_vault: Path) -> None:
        d = AccountDedupe(CHECKING, *existing_ids())
        h = d.hash_for(date(2026, 7, 1), Decimal("-5.00"), "X")
        assert d.check(date(2026, 7, 1), Decimal("-5.00"), "X", "F1", h) is None
        d.record(h, "F1")
        assert d.check(date(2026, 7, 1), Decimal("-5.00"), "X", "F1") == "fitid"

    def test_source_ids_are_account_scoped(self, fresh_vault: Path) -> None:
        h = import_hash(date(2026, 7, 1), Decimal("-10.00"), "GYM", CHECKING)
        write_entry(fresh_vault, imported_entry("2026-07-01", "-10.00", "GYM", h, "F77"))
        other = AccountDedupe("Liabilities:US:Demo:Card3333", *existing_ids())
        # same FITID string on a DIFFERENT account is a different transaction
        assert other.check(date(2026, 7, 1), Decimal("-10.00"), "GYM", "F77") != "fitid"


class TestHashTier:
    def test_csv_row_matches_fitid_bearing_qfx_twin(self, fresh_vault: Path) -> None:
        h = import_hash(date(2026, 7, 2), Decimal("-63.00"), "MYSTERY VENDOR", CHECKING)
        write_entry(fresh_vault, imported_entry("2026-07-02", "-63.00", "MYSTERY VENDOR", h, "F9"))
        d = AccountDedupe(CHECKING, *existing_ids())
        # the CSV re-import of the same row carries no FITID -> hash tier owns it
        assert d.check(date(2026, 7, 2), Decimal("-63.00"), "MYSTERY VENDOR", "") == "hash"

    def test_identical_rows_with_distinct_fitids_both_import(self, fresh_vault: Path) -> None:
        h = import_hash(date(2026, 7, 3), Decimal("-25.00"), "GYM DROP-IN", CHECKING)
        write_entry(fresh_vault, imported_entry("2026-07-03", "-25.00", "GYM DROP-IN", h, "FA"))
        d = AccountDedupe(CHECKING, *existing_ids())
        assert d.check(date(2026, 7, 3), Decimal("-25.00"), "GYM DROP-IN", "FB") is None

    def test_hashless_legacy_hash_entry_still_matches(self, fresh_vault: Path) -> None:
        # entries written before fitid: existed carry only import-hash:
        h = import_hash(date(2026, 7, 4), Decimal("-9.99"), "APP STORE", CHECKING)
        write_entry(fresh_vault, imported_entry("2026-07-04", "-9.99", "APP STORE", h))
        d = AccountDedupe(CHECKING, *existing_ids())
        assert d.check(date(2026, 7, 4), Decimal("-9.99"), "APP STORE", "F-NEW") == "hash"


class TestFuzzyTier:
    @needs_venv
    def test_legacy_entry_dedupes_within_window(self, fresh_vault: Path) -> None:
        # no import-hash, no fitid — a hand-entered / pre-hash-era row
        write_entry(fresh_vault, '2026-07-10 * "LEGACY STORE" ""\n'
                                 f"  {CHECKING}   -55.00 USD\n  Expenses:Uncategorized\n")
        idx = existing_index(CHECKING, set())
        on = date(2026, 7, 10)
        assert is_duplicate(idx, on + timedelta(days=5), Decimal("-55.00"), "LEGACY STORE")
        assert not is_duplicate(idx, on + timedelta(days=6), Decimal("-55.00"), "LEGACY STORE")
        assert not is_duplicate(idx, on, Decimal("-55.01"), "LEGACY STORE")

    @needs_venv
    def test_machine_imported_entries_never_enter_the_fuzzy_index(self, fresh_vault: Path) -> None:
        h = import_hash(date(2026, 7, 10), Decimal("-55.00"), "PARKING", CHECKING)
        write_entry(fresh_vault, imported_entry("2026-07-10", "-55.00", "PARKING", h, "FMON"))
        hashes, _ = existing_ids()
        idx = existing_index(CHECKING, set(hashes))
        # Thursday's identical-amount charge at the same lot is REAL — the
        # fuzzy tier must not eat it (fitid/hash tiers own the Monday row)
        assert not is_duplicate(idx, date(2026, 7, 13), Decimal("-55.00"), "PARKING")


amounts = st.decimals(min_value=Decimal("-99999"), max_value=Decimal("99999"),
                      places=2, allow_nan=False, allow_infinity=False)
payees = st.text(st.characters(codec="ascii", exclude_characters='"\\'),
                 min_size=1, max_size=24)
rows = st.lists(st.tuples(st.dates(min_value=date(2026, 1, 1), max_value=date(2026, 12, 31)),
                          amounts, payees, st.uuids().map(str)),
                min_size=1, max_size=12)


class TestProperties:
    @settings(max_examples=50, deadline=None)
    @given(batch=rows)
    def test_same_batch_twice_is_zero_new(self, batch: list[tuple[date, Decimal, str, str]]) -> None:
        """Dedupe idempotence: whatever imports once fully dedupes on re-run."""
        hashes: dict[str, set[str]] = {}
        sids: dict[str, set[str]] = {}
        first = AccountDedupe(CHECKING, hashes, sids)
        kept = 0
        for when, amt, payee, sid in batch:
            h = first.hash_for(when, amt, payee)
            if first.check(when, amt, payee, sid, h) is None:
                first.record(h, sid)
                kept += 1
        assert kept >= 1
        # simulate the ledger after --write: recorded hashes/ids become the index
        hashes = dict(first.new_hashes)
        sids = {CHECKING: set(first.new_sids)}
        second = AccountDedupe(CHECKING, hashes, sids)
        for when, amt, payee, sid in batch:
            assert second.check(when, amt, payee, sid) is not None

    @settings(max_examples=200, deadline=None)
    @given(when=st.dates(min_value=date(2000, 1, 1), max_value=date(2099, 12, 31)),
           amt=amounts, payee=payees)
    def test_decimal_hash_matches_the_float_era_hash(self, when: date, amt: Decimal,
                                                     payee: str) -> None:
        """Vaults hold years of hashes computed from floats; the Decimal
        rewrite must recognize every one of them. (2dp statement values —
        the only thing ever hashed — format identically on both paths.)"""
        legacy_key = (f"{when.isoformat()}|{round(float(amt), 2) + 0.0:.2f}"
                      f"|{_legacy_payee_key(payee)}|{CHECKING}")
        legacy = hashlib.sha256(legacy_key.encode()).hexdigest()[:16]
        assert import_hash(when, amt, payee, CHECKING) == legacy


def _legacy_payee_key(payee: str) -> str:
    import re

    return re.sub(r"[^A-Z0-9]", "", (payee or "").upper())[:12]
