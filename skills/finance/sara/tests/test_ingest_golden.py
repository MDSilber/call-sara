"""End to end: fixture JSON -> sara.ingest -> the real writer -> the ledger.

Round 1 writes the golden file byte for byte; round 2 exercises the
upstream-correction machinery (modified replaces in place, removed is
reported and NEVER deleted, a posted pending arrives as its own row).
Cursor persistence is asserted at every step: report-only never advances,
--write advances only after the ledger landed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sara.sources.plaid_src import PlaidInvestBatch, PlaidTxnBatch
from tests.conftest import has_venv, needs_venv

PKG_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = Path(__file__).parent / "expected"
REGEN = os.environ.get("SARA_REGEN_GOLDENS") == "1"


def run_ingest(vault: Path, *args: str, fixture_dir: Path = FIXTURES) -> subprocess.CompletedProcess[str]:
    env = {**os.environ,
           "FINANCE_VAULT": str(vault),
           "SARA_PLAID_FIXTURE": str(fixture_dir),
           "PYTHONPATH": str(PKG_ROOT)}
    return subprocess.run([sys.executable, "-m", "sara.ingest", *args],
                          capture_output=True, text=True, env=env)


def year_entries(vault: Path) -> str:
    """The 2026 ledger file minus the conftest opening seed — entries only."""
    text = (vault / "ledger" / "2026.beancount").read_text()
    text = re.sub(r'2026-06-01 \* "Opening balances \(test seed\)".*?\n\n', "", text, flags=re.S)
    return "\n".join(ln.rstrip() for ln in text.splitlines()).strip() + "\n"


def cursors(vault: Path) -> dict[str, object]:
    p = vault / ".secrets" / "plaid-cursors.json"
    return json.loads(p.read_text()) if p.exists() else {}


class TestReportOnly:
    def test_reports_reconcile_and_write_nothing(self, fresh_vault: Path) -> None:
        before = (fresh_vault / "ledger" / "2026.beancount").read_text()
        r = run_ingest(fresh_vault)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "REPORT ONLY" in r.stdout
        assert r.stdout.count("✓ counts reconcile") == 3  # demo txns, vg txns, vg invest
        assert "UNMAPPED (never silent): invest 2026-07-01" in r.stdout  # the stock distribution
        assert "new 2 (sum 2,476.60 USD)" in r.stdout  # checking
        assert "new 2 (sum 488.75 USD)" in r.stdout   # card
        assert "pending excluded 1" in r.stdout
        assert "Re-run with --write" in r.stdout
        # nothing moved: ledger identical, cursor never advances on report-only
        assert (fresh_vault / "ledger" / "2026.beancount").read_text() == before
        assert cursors(fresh_vault) == {}

    @needs_venv
    def test_balance_reconciliation_matches_to_the_cent(self, fresh_vault: Path) -> None:
        r = run_ingest(fresh_vault)
        assert "balance: plaid 4,242.33 vs ledger after import 4,242.33 — MATCH" in r.stdout
        assert "balance: plaid -542.10 vs ledger after import -542.10 — MATCH" in r.stdout

    @needs_venv
    def test_balance_delta_names_the_gap_and_never_blocks(self, bare_vault: Path) -> None:
        r = run_ingest(bare_vault)  # no opening seed -> ledger starts at zero
        assert r.returncode == 0
        assert "DELTA +1,765.73" in r.stdout  # 4242.33 - 2476.60, the missing opening
        assert "seed a dated opening balance" in r.stdout


class TestWriteRound1:
    @needs_venv
    def test_golden_ledger_and_cursor_advance(self, fresh_vault: Path) -> None:
        r = run_ingest(fresh_vault, "--write")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "bean-check passed" in r.stdout
        got = year_entries(fresh_vault)
        golden = EXPECTED / "ingest_round1.beancount"
        if REGEN:
            golden.write_text(got)
        assert got == golden.read_text(), f"--- got ---\n{got}"
        c = cursors(fresh_vault)
        items = c.get("items", {})
        assert isinstance(items, dict)
        assert items["demo"]["cursor"] == "cursor-final"
        assert items["vg"]["cursor"] == "vg-cursor-1"
        assert items["demo"]["last_synced"]
        # secrets hygiene: cursor file is 0600 inside a 0700 dir
        assert (fresh_vault / ".secrets").stat().st_mode & 0o077 == 0
        assert (fresh_vault / ".secrets" / "plaid-cursors.json").stat().st_mode & 0o077 == 0

    @needs_venv
    def test_rerun_is_zero_new(self, fresh_vault: Path) -> None:
        run_ingest(fresh_vault, "--write")
        after_once = year_entries(fresh_vault)
        r = run_ingest(fresh_vault, "--write")
        assert r.returncode == 0
        assert "nothing new to write" in r.stdout
        assert 'deduped (fitid)' in r.stdout or "deduped 2 (fitid)" in r.stdout
        assert year_entries(fresh_vault) == after_once

    @needs_venv
    def test_positions_reconcile_reports_match_and_gap(self, fresh_vault: Path) -> None:
        r = run_ingest(fresh_vault)
        assert "TIF MATCH (7.810)" in r.stdout          # ledger+import == statement
        assert "TWC MISMATCH" in r.stdout               # pre-vault history gap
        assert "seed an opening position of 10.500 TWC" in r.stdout


class TestWriteRound2:
    @needs_venv
    def test_modified_replaces_removed_reports_pending_posts(self, fresh_vault: Path,
                                                             tmp_path: Path) -> None:
        run_ingest(fresh_vault, "--write")
        round2 = tmp_path / "round2"
        round2.mkdir()
        shutil.copy(FIXTURES / "demo2.sync.json", round2 / "demo.sync.json")
        shutil.copy(FIXTURES / "vg.sync.json", round2 / "vg.sync.json")
        shutil.copy(FIXTURES / "vg.investments.json", round2 / "vg.investments.json")
        shutil.copy(FIXTURES / "vg.holdings.json", round2 / "vg.holdings.json")
        r = run_ingest(fresh_vault, "--write", fixture_dir=round2)
        assert r.returncode == 0, r.stdout + r.stderr
        text = (fresh_vault / "ledger" / "2026.beancount").read_text()
        # modified: the coffee charge was corrected upstream -> replaced in place
        assert "replace in place by plaid-id: 1 entry" in r.stdout
        assert "-12.25 USD" in text and "-11.25 USD" not in text
        assert text.count('plaid-id: "plaid-txn-003"') == 1
        # removed: reported LOUDLY, entry still present
        assert "REMOVED UPSTREAM but present" in r.stdout and "NOT deleted" in r.stdout
        assert 'plaid-id: "plaid-txn-004"' in text
        assert "never imported (likely pending): plaid-txn-999" in r.stdout
        # the pending row posted with its own id
        assert 'plaid-id: "plaid-txn-006"' in text
        assert 'plaid-id: "plaid-txn-005-pending"' not in text
        assert cursors(fresh_vault)["items"]["demo"]["cursor"] == "cursor-round-2"  # type: ignore[index]


class TestIntegrityGate:
    def test_batches_that_lose_rows_refuse_to_reconcile(self) -> None:
        """The gate the CLI exits 2 on: fetched != accounted-for."""
        assert PlaidTxnBatch(fetched_added=5).reconciles() is False
        assert PlaidInvestBatch(fetched=3).reconciles() is False
        assert PlaidTxnBatch().reconciles() is True

    def test_missing_token_is_a_hard_error_not_a_crash(self, fresh_vault: Path,
                                                       tmp_path: Path) -> None:
        empty = tmp_path / "nofix"
        empty.mkdir()
        # no fixture files AND no token -> per-item hard error, exit 1, still a report
        env = {**os.environ, "FINANCE_VAULT": str(fresh_vault), "PYTHONPATH": str(PKG_ROOT)}
        env.pop("SARA_PLAID_FIXTURE", None)
        r = subprocess.run([sys.executable, "-m", "sara.ingest"],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 1
        assert "no access token" in r.stdout

    def test_unknown_item_filter_names_the_configured_ones(self, fresh_vault: Path) -> None:
        r = run_ingest(fresh_vault, "--item", "nope")
        assert r.returncode != 0
        assert "demo, vg" in r.stderr + r.stdout


@pytest.mark.skipif(not has_venv(), reason="golden write path needs bean-check")
def test_verbose_prints_the_entries(fresh_vault: Path) -> None:
    r = run_ingest(fresh_vault, "--verbose")
    assert "--- entries ---" in r.stdout
    assert 'plaid-category: "FOOD_AND_DRINK_GROCERIES (very_high)"' in r.stdout
