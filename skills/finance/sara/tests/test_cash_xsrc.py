"""The cash cross-source tier + the one-shot ledger audit.

The live shape this pins (owner's ledger, Aug 2026): a Chase-CSV-era row
("TST*THE VIAND", -100.11, Aug 3) and a Plaid row ("The Viand", -100.11,
Aug 4) booking the SAME purchase — ids and hashes can never match across
families, payee text differs, dates drift. Eleven such pairs in one week,
~25% of the month's card spend double-counted.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from sara.audit import find_pairs, machine_cash_entries
from sara.ledger.writer import (
    CASH_XSRC_TIER_LABEL,
    FAMILY_CSV,
    FAMILY_OFX,
    FAMILY_PLAID,
    AccountDedupe,
    CashXsrcRow,
    claim_cash_xsrc,
    delete_entries,
    existing_ids,
    import_hash,
    scan_invest_ledger,
)
from tests.conftest import needs_venv

PKG_ROOT = Path(__file__).resolve().parents[1]
CARD = "Liabilities:US:Demo:Card3333"
D = Decimal


def card_entry(when: str, payee: str, amount: str, family: str,
               sid: str = "") -> str:
    """A machine card entry in the exact shape each source family writes."""
    h = import_hash(date.fromisoformat(when), D(amount), payee, CARD)
    if family == FAMILY_CSV:
        meta = f'  chase-type: "Sale"\n  import-hash: "{h}"\n'
    elif family == FAMILY_PLAID:
        meta = (f'  plaid-type: "in store"\n  import-hash: "{h}"\n'
                f'  plaid-id: "{sid or "plaid-seed-" + h[:8]}"\n')
    else:
        meta = f'  ofx-type: "DEBIT"\n  import-hash: "{h}"\n  fitid: "{sid or "F" + h[:8]}"\n'
    return (f'{when} * "{payee}" ""\n{meta}'
            f"  {CARD}   {amount} USD\n  Expenses:Food:Dining\n")


def seed(vault: Path, *entries: str) -> None:
    year = vault / "ledger" / "2026.beancount"
    year.write_text(year.read_text() + "\n" + "\n".join(entries))


def plaid_txn(sid: str, when: str, amount: float, name: str) -> dict[str, object]:
    return {"transaction_id": sid, "account_id": "plaid-acct-card", "date": when,
            "amount": amount, "name": name, "merchant_name": None, "pending": False,
            "payment_channel": "in store", "iso_currency_code": "USD"}


def run_card_sync(vault: Path, tmp_path: Path,
                  rows: list[dict[str, object]]) -> subprocess.CompletedProcess[str]:
    fixture = tmp_path / "xsrc"
    fixture.mkdir(exist_ok=True)
    (fixture / "demo.sync.json").write_text(json.dumps([{
        "accounts": [], "added": rows, "modified": [], "removed": [],
        "next_cursor": "x1", "has_more": False}]))
    env = {**os.environ, "FINANCE_VAULT": str(vault),
           "SARA_PLAID_FIXTURE": str(fixture), "PYTHONPATH": str(PKG_ROOT)}
    return subprocess.run([sys.executable, "-m", "sara.ingest", "--item", "demo"],
                          capture_output=True, text=True, env=env)


class TestClaimSemantics:
    def pool(self) -> list[CashXsrcRow]:
        return [CashXsrcRow(date(2026, 8, 3), D("-100.11"), FAMILY_CSV)]

    def test_cross_family_signed_match_claims_and_consumes(self) -> None:
        pool = self.pool()
        assert claim_cash_xsrc(pool, date(2026, 8, 4), D("-100.11"), FAMILY_PLAID)
        assert pool == []

    def test_window_is_three_days(self) -> None:
        assert claim_cash_xsrc(self.pool(), date(2026, 8, 6), D("-100.11"), FAMILY_PLAID)
        assert not claim_cash_xsrc(self.pool(), date(2026, 8, 7), D("-100.11"), FAMILY_PLAID)

    def test_signed_to_the_cent_a_refund_never_explains_a_charge(self) -> None:
        assert not claim_cash_xsrc(self.pool(), date(2026, 8, 4), D("100.11"), FAMILY_PLAID)
        assert not claim_cash_xsrc(self.pool(), date(2026, 8, 4), D("-100.12"), FAMILY_PLAID)

    def test_same_family_is_absolutely_protected(self) -> None:
        assert not claim_cash_xsrc(self.pool(), date(2026, 8, 4), D("-100.11"), FAMILY_CSV)

    def test_claims_the_closest_date_first(self) -> None:
        pool = [CashXsrcRow(date(2026, 8, 1), D("-12.99"), FAMILY_CSV),
                CashXsrcRow(date(2026, 8, 3), D("-12.99"), FAMILY_CSV)]
        assert claim_cash_xsrc(pool, date(2026, 8, 4), D("-12.99"), FAMILY_PLAID)
        assert pool[0].when == date(2026, 8, 1)  # the FARTHER row survives


class TestScanPool:
    def test_only_machine_pure_cash_entries_enter(self, fresh_vault: Path) -> None:
        seed(fresh_vault,
             card_entry("2026-08-03", "TST*THE VIAND", "-100.11", FAMILY_CSV),
             '2026-08-03 * "HAND ENTRY" ""\n'
             f"  {CARD}   -55.00 USD\n  Expenses:Food:Dining\n")
        pool = scan_invest_ledger(CARD).cash_xsrc
        assert [(r.amount, r.family) for r in pool] == [(D("-100.11"), FAMILY_CSV)]


class TestPipeline:
    def test_the_viand_pair_dedupes(self, fresh_vault: Path, tmp_path: Path) -> None:
        seed(fresh_vault, card_entry("2026-08-03", "TST*THE VIAND", "-100.11", FAMILY_CSV))
        r = run_card_sync(fresh_vault, tmp_path,
                          [plaid_txn("pl-viand", "2026-08-04", 100.11, "The Viand")])
        assert r.returncode == 0, r.stdout + r.stderr
        assert f"deduped ({CASH_XSRC_TIER_LABEL}) 2026-08-04 -100.11 The Viand" in r.stdout
        assert "new 0" in r.stdout

    def test_true_twins_one_ledger_row_one_survives(self, fresh_vault: Path,
                                                    tmp_path: Path) -> None:
        seed(fresh_vault, card_entry("2026-08-02", "COFFEE CO", "-12.99", FAMILY_CSV))
        r = run_card_sync(fresh_vault, tmp_path, [
            plaid_txn("pl-a", "2026-08-01", 12.99, "Coffee Co"),
            plaid_txn("pl-b", "2026-08-03", 12.99, "Coffee Co"),
        ])
        assert "deduped 1" in r.stdout and "new 1" in r.stdout
        # exactly ONE row claimed the single ledger twin; its sibling imported
        assert r.stdout.count(f"deduped ({CASH_XSRC_TIER_LABEL})") == 1

    def test_monthly_subscription_never_cross_claims(self, fresh_vault: Path,
                                                     tmp_path: Path) -> None:
        seed(fresh_vault, card_entry("2026-07-05", "STREAMFLIX", "-15.99", FAMILY_CSV))
        r = run_card_sync(fresh_vault, tmp_path,
                          [plaid_txn("pl-sub", "2026-08-04", 15.99, "Streamflix")])
        assert "new 1" in r.stdout and CASH_XSRC_TIER_LABEL not in r.stdout

    def test_plaid_vs_plaid_twins_both_exist(self, fresh_vault: Path,
                                             tmp_path: Path) -> None:
        # within one family ids are authoritative: a same-amount nearby row
        # with a NEW plaid id is a real second charge
        seed(fresh_vault, card_entry("2026-08-02", "Gym", "-50.00", FAMILY_PLAID,
                                     sid="plaid-old"))
        r = run_card_sync(fresh_vault, tmp_path,
                          [plaid_txn("plaid-new", "2026-08-03", 50.00, "Gym")])
        assert "new 1" in r.stdout and CASH_XSRC_TIER_LABEL not in r.stdout


CARD_SEED = (
    '2026-01-01 * "Chase" "Opening balance — Card 3333 (derived 2026-08-05: '
    'live posted minus YTD-reconciled ledger)"\n'
    "  Liabilities:US:Demo:Card3333   -500.00 USD  ; was -400.00 — adjusted "
    "-100.00 when history backfilled (2026-08-06)\n"
    "  Equity:Opening-Balances\n")


class TestAudit:
    def seed_pairs(self, vault: Path) -> None:
        seed(vault,
             CARD_SEED,
             # the Viand shape: csv + plaid, auto-removable
             card_entry("2026-08-03", "TST*THE VIAND", "-100.11", FAMILY_CSV),
             card_entry("2026-08-04", "The Viand", "-100.11", FAMILY_PLAID, sid="pl-v"),
             # csv + ofx: cross-source but no plaid side -> REVIEW only
             card_entry("2026-07-10", "SQ *DELI", "-42.00", FAMILY_CSV),
             card_entry("2026-07-11", "SQUARE DELI", "-42.00", FAMILY_OFX, sid="F77"),
             # monthly subscription: 30 days apart -> never a pair
             card_entry("2026-07-05", "STREAMFLIX", "-15.99", FAMILY_CSV),
             card_entry("2026-08-04", "Streamflix", "-15.99", FAMILY_PLAID, sid="pl-s"),
             # sub-dollar cross-source match -> below the $1 floor, ignored
             card_entry("2026-08-01", "TIP", "-0.50", FAMILY_CSV),
             card_entry("2026-08-02", "Tip", "-0.50", FAMILY_PLAID, sid="pl-t"))

    def run_audit(self, vault: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "FINANCE_VAULT": str(vault), "PYTHONPATH": str(PKG_ROOT)}
        return subprocess.run([sys.executable, "-m", "sara.audit", *args],
                              capture_output=True, text=True, env=env)

    def test_report_lists_pairs_verdicts_and_monthly_impact(self, fresh_vault: Path) -> None:
        self.seed_pairs(fresh_vault)
        r = self.run_audit(fresh_vault)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "REPORT ONLY" in r.stdout
        assert "keep  2026-08-04 'The Viand' [plaid]" in r.stdout
        assert "drop  2026-08-03 'TST*THE VIAND' [csv]" in r.stdout
        assert "REVIEW (no plaid side — pick by hand)" in r.stdout
        assert "2026-07: 1 pair, $42.00" in r.stdout
        assert "2026-08: 1 pair, $100.11" in r.stdout
        assert "2 pairs, $142.11 duplicated — 1 auto-removable" in r.stdout
        assert "STREAMFLIX" not in r.stdout and "TIP" not in r.stdout
        # report-only never touches the ledger
        assert "TST*THE VIAND" in (fresh_vault / "ledger" / "2026.beancount").read_text()

    @needs_venv
    def test_write_removes_drops_and_compensates_the_seed(self, fresh_vault: Path) -> None:
        self.seed_pairs(fresh_vault)
        r = self.run_audit(fresh_vault, "--write")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "removed 1 duplicate entry and adjusted 1 derivation seed" in r.stdout
        text = (fresh_vault / "ledger" / "2026.beancount").read_text()
        assert "TST*THE VIAND" not in text          # csv twin gone
        assert 'plaid-id: "pl-v"' in text           # plaid row kept
        assert "SQ *DELI" in text and "SQUARE DELI" in text  # REVIEW untouched
        assert "STREAMFLIX" in text                 # non-pairs untouched
        # the seed absorbed the removed -100.11 back, comment trail appended
        assert "-600.11 USD" in text and "-500.00 USD" not in text
        assert ("(2026-08-06); and -100.11 on " in text
                and "when cross-source duplicates were removed" in text)
        r2 = self.run_audit(fresh_vault)
        assert "1 pairs" in r2.stdout and "0 auto-removable" in r2.stdout

    def test_min_amount_flag(self, fresh_vault: Path) -> None:
        self.seed_pairs(fresh_vault)
        r = self.run_audit(fresh_vault, "--min-amount", "0.25")
        assert "3 pairs" in r.stdout  # the $0.50 tip pair now counts
        r = self.run_audit(fresh_vault, "--min-amount", "nope")
        assert r.returncode != 0


class TestDeleteEntries:
    def test_unknown_entry_refuses_untouched(self, fresh_vault: Path) -> None:
        year = fresh_vault / "ledger" / "2026.beancount"
        before = year.read_text()
        with pytest.raises(SystemExit, match="not uniquely addressable"):
            delete_entries([(year, "2026-01-01 * \"GHOST\" \"\"\n")])
        assert year.read_text() == before


class TestAuditMatcherParity:
    def test_audit_and_live_tier_agree_on_the_viand_pair(self, fresh_vault: Path) -> None:
        seed(fresh_vault,
             card_entry("2026-08-03", "TST*THE VIAND", "-100.11", FAMILY_CSV),
             card_entry("2026-08-04", "The Viand", "-100.11", FAMILY_PLAID, sid="pl-v"))
        pairs = find_pairs(machine_cash_entries(D("1.00")))
        assert len(pairs) == 1 and pairs[0].auto
        d = AccountDedupe(CARD, *existing_ids())
        # the live tier sees the csv row as claimable by a plaid candidate too
        assert d.check(date(2026, 8, 4), D("-100.11"), "The Viand", "pl-other",
                       family=FAMILY_PLAID) == CASH_XSRC_TIER_LABEL


class TestSeedCompensation:
    """The round-5 shape: derivation seeds were trued while the duplicates
    existed, so deleting drop-sides must give the sums back — inside one
    atomic gated write — and the live-balance anchors must hold."""

    def anchored_vault(self, vault: Path) -> None:
        # conftest opening (-1,030.85) + seed (-500.00) + both dup rows
        # (-100.11 x2) = the ANCHOR the assertion states. bean-check passing
        # after --write is the proof the anchor survived compensation.
        seed(vault,
             CARD_SEED,
             card_entry("2026-08-03", "TST*THE VIAND", "-100.11", FAMILY_CSV),
             card_entry("2026-08-04", "The Viand", "-100.11", FAMILY_PLAID, sid="pl-v"),
             "2026-08-07 balance Liabilities:US:Demo:Card3333   -1731.07 USD\n")

    def run_audit(self, vault: Path, *args: str) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "FINANCE_VAULT": str(vault), "PYTHONPATH": str(PKG_ROOT)}
        return subprocess.run([sys.executable, "-m", "sara.audit", *args],
                              capture_output=True, text=True, env=env)

    def test_report_shows_the_planned_adjustment(self, fresh_vault: Path) -> None:
        self.anchored_vault(fresh_vault)
        r = self.run_audit(fresh_vault)
        assert "derivation-seed compensation" in r.stdout
        assert "Liabilities:US:Demo:Card3333: seed -500.00 -> -600.11 (-100.11)" in r.stdout
        # report only: nothing moved
        assert "-500.00 USD" in (fresh_vault / "ledger" / "2026.beancount").read_text()

    @needs_venv
    def test_write_keeps_the_anchor_true(self, fresh_vault: Path) -> None:
        self.anchored_vault(fresh_vault)
        r = self.run_audit(fresh_vault, "--write")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "bean-check passed" in r.stdout  # the -1731.07 anchor held
        text = (fresh_vault / "ledger" / "2026.beancount").read_text()
        assert "-600.11 USD" in text and "TST*THE VIAND" not in text
        assert "balance Liabilities:US:Demo:Card3333   -1731.07 USD" in text

    def test_no_seed_account_refuses_unless_forced(self, fresh_vault: Path) -> None:
        # a pair on CHECKING, which has no derivation seed
        seed(fresh_vault,
             '2026-08-03 * "PAYROLL CO" ""\n'
             '  ofx-type: "CREDIT"\n  import-hash: "aaaaaaaaaaaaaa99"\n'
             '  fitid: "F999"\n'
             "  Assets:US:Demo:Checking0766   2500.00 USD\n  Income:US:Other\n",
             '2026-08-04 * "Payroll Co" ""\n'
             '  plaid-type: "other"\n  import-hash: "bbbbbbbbbbbbbb99"\n'
             '  plaid-id: "pl-pay"\n'
             "  Assets:US:Demo:Checking0766   2500.00 USD\n  Income:US:Other\n")
        r = self.run_audit(fresh_vault, "--write")
        assert "Assets:US:Demo:Checking0766: NO SEED" in r.stdout
        assert "EXCLUDED from --write" in r.stdout
        assert "nothing auto-removable — no writes." in r.stdout
        text = (fresh_vault / "ledger" / "2026.beancount").read_text()
        assert "PAYROLL CO" in text and 'plaid-id: "pl-pay"' in text  # both intact

    @needs_venv
    def test_force_no_seed_deletes_without_compensation(self, fresh_vault: Path) -> None:
        seed(fresh_vault,
             card_entry("2026-08-03", "TST*THE VIAND", "-100.11", FAMILY_CSV),
             card_entry("2026-08-04", "The Viand", "-100.11", FAMILY_PLAID, sid="pl-v"))
        r = self.run_audit(fresh_vault, "--write", "--force-no-seed")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "deleting WITHOUT compensation" in r.stdout
        assert "removed 1 duplicate entry and adjusted 0 derivation seeds" in r.stdout
        assert "TST*THE VIAND" not in (fresh_vault / "ledger" / "2026.beancount").read_text()
