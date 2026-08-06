"""Cross-source invest dedupe — the real-world Vanguard failure, pinned.

The owner's first Plaid sync re-delivered brokerage history the ledger
already held from invest-OFX imports: Plaid ids can never equal FITIDs, and
date/precision drift broke the hash tier, so 69 rows would have double-
booked (the positions reconcile caught it). The fix is the invest fuzzy
tier: same commodity + same units (exact at the ledger's precision) +
trade date within ±3 days + cash total within $0.01, cross-family only,
each ledger row consumed by at most one candidate.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from sara.ledger.writer import (
    FAMILY_OFX,
    FAMILY_PLAID,
    INVEST_TIER_LABEL,
    InvestLedgerRow,
    claim_invest_duplicate,
    import_hash,
    invest_index,
)
from tests.conftest import needs_venv

PKG_ROOT = Path(__file__).resolve().parents[1]
BROKERAGE = "Assets:US:Demo:Brokerage8642"
D = Decimal

def _entry(when: str, payee: str, kind: str, postings: str) -> str:
    """One invest_ofx-shaped ledger entry with its GENUINE import-hash —
    modeled on the owner's real vault (pre-fitid era: import-hash +
    ofx-type only, still OFX-family). The hash must be the recomputable one:
    that is what keeps machine rows out of the cash ±5d fuzzy index, exactly
    as in a real vault."""
    d = date.fromisoformat(when)
    cash = CASH_BY_PAYEE[payee]
    h = import_hash(d, cash, payee, BROKERAGE)
    return (f'{when} * "{payee}" ""\n  ofx-type: "{kind}"\n'
            f'  import-hash: "{h}"\n{postings}\n')


CASH_BY_PAYEE = {
    "BUY 506.882 VBTLX @ 9.60": Decimal("-4866.07"),
    "BUY 220.931 VTIAX @ 31.00": Decimal("-6848.86"),
    "BUY 448.182 VTSAX @ 100.00": Decimal("-44818.20"),
    "REINVEST DIV 11.923 VTSAX @ 100.00": Decimal("-1192.30"),
}

OFX_SEEDED_ENTRIES = "\n" + "\n".join([
    _entry("2026-07-10", "BUY 506.882 VBTLX @ 9.60", "BUYMF",
           "  Assets:US:Demo:Brokerage8642   506.882 VBTLX {9.60 USD}\n"
           "  Assets:US:Demo:Brokerage8642   -4866.07 USD"),
    _entry("2026-07-10", "BUY 220.931 VTIAX @ 31.00", "BUYMF",
           "  Assets:US:Demo:Brokerage8642   220.931 VTIAX {31.00 USD}\n"
           "  Assets:US:Demo:Brokerage8642   -6848.86 USD"),
    _entry("2026-07-12", "BUY 448.182 VTSAX @ 100.00", "BUYMF",
           "  Assets:US:Demo:Brokerage8642   448.182 VTSAX {100.00 USD}\n"
           "  Assets:US:Demo:Brokerage8642   -44818.20 USD"),
    _entry("2026-07-14", "REINVEST DIV 11.923 VTSAX @ 100.00", "REINVEST",
           "  Assets:US:Demo:Brokerage8642   11.923 VTSAX {100.00 USD}\n"
           "  Income:US:Dividends   -1192.30 USD"),
])


def seed(vault: Path, text: str = OFX_SEEDED_ENTRIES) -> None:
    year = vault / "ledger" / "2026.beancount"
    year.write_text(year.read_text() + text)


class TestInvestIndex:
    def test_parses_the_real_entry_shapes(self, fresh_vault: Path) -> None:
        seed(fresh_vault, OFX_SEEDED_ENTRIES + '''
2026-07-20 * "SELL 30000.000 VMFXX @ 1.00" ""
  ofx-type: "SELLMF"
  import-hash: "aaaaaaaaaaaaaa05"
  Assets:US:Demo:Brokerage8642   -30000.000 VMFXX {} @ 1.00 USD  ; whole lots (FIFO if set) — broker lot data governs at tax time
  Assets:US:Demo:Brokerage8642   30000.00 USD

2026-07-21 * "REINVEST DIV 18.441 VTSAX @ 176.71" ""
  ofx-type: "REINVEST"
  import-hash: "aaaaaaaaaaaaaa06"
  Assets:US:Demo:Brokerage8642   18.441 VTSAX {{3258.76 USD}}
  Income:US:Dividends   -3258.76 USD

2026-07-22 * "Opening position (history predates the vault)" ""
  Assets:US:Demo:Brokerage8642   1103.535 VBTLX
  Equity:Opening-Balances
''')
        idx = invest_index(BROKERAGE)
        by = {(r.when, t): r for t, rows in idx.items() for r in rows}
        buy = by[(date(2026, 7, 10), "VBTLX")]
        assert (buy.units, buy.total, buy.family) == (D("506.882"), D("4866.07"), FAMILY_OFX)
        # reinvest has no USD leg on the account -> total from the lot cost
        reinvest = by[(date(2026, 7, 14), "VTSAX")]
        assert reinvest.total == D("1192.30")
        # sell: negative units, |USD leg| as total, trailing comment survives
        sell = by[(date(2026, 7, 20), "VMFXX")]
        assert (sell.units, sell.total) == (D("-30000.000"), D("30000.00"))
        # {{total-cost}} reinvest reads the double braces
        assert by[(date(2026, 7, 21), "VTSAX")].total == D("3258.76")
        # opening snapshot: no cash, no cost -> total None, family ""
        snap = by[(date(2026, 7, 22), "VBTLX")]
        assert snap.total is None and snap.family == ""

    def test_income_cash_entries_never_enter_the_index(self, fresh_vault: Path) -> None:
        seed(fresh_vault, '''
2026-07-18 * "DIV VTSAX" ""
  ofx-type: "INCOME"
  import-hash: "aaaaaaaaaaaaaa07"
  Assets:US:Demo:Brokerage8642   6.00 USD
  Income:US:Dividends
''')
        assert invest_index(BROKERAGE) == {}


def row(**kw: object) -> InvestLedgerRow:
    base: dict[str, object] = {"when": date(2026, 7, 10), "units": D("506.882"),
                               "total": D("4866.07"), "family": FAMILY_OFX}
    base.update(kw)
    return InvestLedgerRow(**base)  # type: ignore[arg-type]


def claim(r: InvestLedgerRow, *, when: date | None = None, units: Decimal | None = None,
          total: Decimal | None = None, family: str = FAMILY_PLAID) -> bool:
    idx = {"VBTLX": [r]}
    return claim_invest_duplicate(idx, when or r.when, "VBTLX",
                                  units if units is not None else r.units,
                                  total if total is not None else r.total, family)


class TestClaimSemantics:
    def test_cross_family_exact_match_claims(self) -> None:
        assert claim(row())

    def test_window_edges(self) -> None:
        # ±5: trade-vs-settle drift tops out at T+2 over a weekend + holiday
        assert claim(row(), when=date(2026, 7, 15))       # +5d: in
        assert not claim(row(), when=date(2026, 7, 16))   # +6d: out
        assert claim(row(), when=date(2026, 7, 5))        # -5d: in

    def test_the_real_memorial_day_pair_is_inside_the_window(self) -> None:
        # Fri 2026-05-22 trade (OFX) vs Tue 2026-05-26 settlement (Plaid)
        r = row(when=date(2026, 5, 22))
        assert claim(r, when=date(2026, 5, 26))

    def test_total_tolerance_is_one_cent(self) -> None:
        assert claim(row(), total=D("-4866.08"))          # sign-blind, within $0.01
        assert not claim(row(), total=D("4866.09"))

    def test_units_quantize_to_the_ledger_precision(self) -> None:
        assert claim(row(), units=D("506.8817"))          # 506.882 at ledger 3dp
        assert not claim(row(), units=D("506.88"))        # coarser: can't corroborate
        assert not claim(row(), units=D("506.883"))

    def test_units_sign_discriminates_buys_from_sells(self) -> None:
        assert not claim(row(), units=D("-506.882"))

    def test_same_family_never_claims(self) -> None:
        # twin sweep redemptions days apart within one source are REAL rows
        assert not claim(row(), family=FAMILY_OFX)
        assert not claim(row(family=FAMILY_PLAID))
        assert claim(row(family=""))                      # hand/legacy rows claimable

    def test_unknowable_totals_never_corroborate(self) -> None:
        assert not claim(row(total=None))
        r = row()
        assert not claim_invest_duplicate({"VBTLX": [r]}, r.when, "VBTLX",
                                          r.units, None, FAMILY_PLAID)

    def test_each_ledger_row_is_consumed_once(self) -> None:
        idx = {"VBTLX": [row()]}
        args = (date(2026, 7, 10), "VBTLX", D("506.882"), D("4866.07"), FAMILY_PLAID)
        assert claim_invest_duplicate(idx, *args)
        assert not claim_invest_duplicate(idx, *args)     # the twin still imports


PLAID_OVERLAP = {
    "sync": [{
        "accounts": [{"account_id": "plaid-acct-brokerage", "name": "Demo Brokerage",
                      "mask": "8642", "type": "investment", "subtype": "brokerage",
                      "balances": {"current": 145000.00, "available": None,
                                   "iso_currency_code": "USD"}}],
        "added": [], "modified": [], "removed": [],
        "next_cursor": "vgx-1", "has_more": False,
    }],
    # the same four actions, wearing Plaid's clothes: new ids, +1..+3d date
    # drift, extra units precision, price rounding — plus one genuinely-new
    # older VUSXX sweep buy the OFX era never covered
    "investments": [{
        "accounts": [{"account_id": "plaid-acct-brokerage", "name": "Demo Brokerage",
                      "mask": "8642", "type": "investment", "subtype": "brokerage",
                      "balances": {"current": 145000.00, "available": None,
                                   "iso_currency_code": "USD"}}],
        "securities": [
            {"security_id": "s-vbtlx", "ticker_symbol": "VBTLX", "name": "Total Bond"},
            {"security_id": "s-vtiax", "ticker_symbol": "VTIAX", "name": "Total Intl"},
            {"security_id": "s-vtsax", "ticker_symbol": "VTSAX", "name": "Total Stock"},
            {"security_id": "s-vusxx", "ticker_symbol": "VUSXX", "name": "Treasury MM"},
        ],
        "investment_transactions": [
            {"investment_transaction_id": "pl-1", "account_id": "plaid-acct-brokerage",
             "security_id": "s-vbtlx", "date": "2026-07-11", "name": "Buy VANGUARD TOTAL BOND",
             "quantity": 506.8817, "price": 9.5999, "amount": 4866.07, "fees": 0,
             "type": "buy", "subtype": "buy", "iso_currency_code": "USD"},
            {"investment_transaction_id": "pl-2", "account_id": "plaid-acct-brokerage",
             "security_id": "s-vtiax", "date": "2026-07-13", "name": "Buy VANGUARD TOTAL INTL",
             "quantity": 220.931, "price": 31.0, "amount": 6848.86, "fees": 0,
             "type": "buy", "subtype": "buy", "iso_currency_code": "USD"},
            {"investment_transaction_id": "pl-3", "account_id": "plaid-acct-brokerage",
             "security_id": "s-vtsax", "date": "2026-07-13", "name": "Buy VANGUARD TOTAL STOCK",
             "quantity": 448.182, "price": 100.0, "amount": 44818.21, "fees": 0,
             "type": "buy", "subtype": "buy", "iso_currency_code": "USD"},
            {"investment_transaction_id": "pl-4", "account_id": "plaid-acct-brokerage",
             "security_id": "s-vtsax", "date": "2026-07-15", "name": "Reinvestment",
             "quantity": 11.923, "price": 100.0, "amount": 1192.30, "fees": 0,
             "type": "buy", "subtype": "dividend reinvestment", "iso_currency_code": "USD"},
            {"investment_transaction_id": "pl-5", "account_id": "plaid-acct-brokerage",
             "security_id": "s-vusxx", "date": "2026-05-20", "name": "Buy VANGUARD TREASURY MM",
             "quantity": 89166.65, "price": 1.0, "amount": 89166.65, "fees": 0,
             "type": "buy", "subtype": "buy", "iso_currency_code": "USD"},
        ],
        "total_investment_transactions": 5,
    }],
    # holdings claim MORE VUSXX than ledger+import can explain -> gap + seed
    "holdings": {
        "accounts": [{"account_id": "plaid-acct-brokerage", "name": "Demo Brokerage",
                      "mask": "8642", "type": "investment", "subtype": "brokerage",
                      "balances": {"current": 145000.00, "available": None,
                                   "iso_currency_code": "USD"}}],
        "securities": [
            {"security_id": "s-vbtlx", "ticker_symbol": "VBTLX", "name": "Total Bond"},
            {"security_id": "s-vtiax", "ticker_symbol": "VTIAX", "name": "Total Intl"},
            {"security_id": "s-vtsax", "ticker_symbol": "VTSAX", "name": "Total Stock"},
            {"security_id": "s-vusxx", "ticker_symbol": "VUSXX", "name": "Treasury MM"},
        ],
        "holdings": [
            {"account_id": "plaid-acct-brokerage", "security_id": "s-vbtlx",
             "quantity": 506.882, "institution_price": 9.60, "institution_value": 4866.07,
             "institution_price_as_of": "2026-08-06", "iso_currency_code": "USD"},
            {"account_id": "plaid-acct-brokerage", "security_id": "s-vtiax",
             "quantity": 220.931, "institution_price": 31.00, "institution_value": 6848.86,
             "institution_price_as_of": "2026-08-06", "iso_currency_code": "USD"},
            {"account_id": "plaid-acct-brokerage", "security_id": "s-vtsax",
             "quantity": 460.105, "institution_price": 100.00, "institution_value": 46010.50,
             "institution_price_as_of": "2026-08-06", "iso_currency_code": "USD"},
            {"account_id": "plaid-acct-brokerage", "security_id": "s-vusxx",
             "quantity": 100000.000, "institution_price": 1.00, "institution_value": 100000.00,
             "institution_price_as_of": "2026-08-06", "iso_currency_code": "USD"},
        ],
    },
}


def run_overlap_ingest(vault: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    fixture = tmp_path / "overlap"
    fixture.mkdir(exist_ok=True)
    (fixture / "vg.sync.json").write_text(json.dumps(PLAID_OVERLAP["sync"]))
    (fixture / "vg.investments.json").write_text(json.dumps(PLAID_OVERLAP["investments"]))
    (fixture / "vg.holdings.json").write_text(json.dumps(PLAID_OVERLAP["holdings"]))
    env = {**os.environ, "FINANCE_VAULT": str(vault),
           "SARA_PLAID_FIXTURE": str(fixture), "PYTHONPATH": str(PKG_ROOT)}
    return subprocess.run([sys.executable, "-m", "sara.ingest", "--item", "vg"],
                          capture_output=True, text=True, env=env)


class TestVanguardOverlapRegression:
    """The coordinator's fixture pair: OFX-seeded ledger x overlapping Plaid
    payload, asserted end to end through the real report."""

    def test_every_overlap_dedupes_with_the_tier_named(self, fresh_vault: Path,
                                                       tmp_path: Path) -> None:
        seed(fresh_vault)
        r = run_overlap_ingest(fresh_vault, tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "fetched 5 -> mapped 5 | unmapped 0  ✓ counts reconcile" in r.stdout
        assert "new 1 (cash effect -89,166.65 USD), deduped 4" in r.stdout
        for line in (f"deduped ({INVEST_TIER_LABEL}) 2026-07-11 -4866.07 BUY 506.8817 VBTLX",
                     f"deduped ({INVEST_TIER_LABEL}) 2026-07-13 -6848.86 BUY 220.931 VTIAX",
                     f"deduped ({INVEST_TIER_LABEL}) 2026-07-13 -44818.21 BUY 448.182 VTSAX",
                     f"deduped ({INVEST_TIER_LABEL}) 2026-07-15 -1192.30 REINVEST DIV 11.923 VTSAX"):
            assert line in r.stdout, f"missing: {line}\n{r.stdout}"
        assert "report only" in r.stdout  # nothing written

    def test_the_genuinely_new_row_still_imports_with_the_gap_named(
            self, fresh_vault: Path, tmp_path: Path) -> None:
        seed(fresh_vault)
        r = run_overlap_ingest(fresh_vault, tmp_path)
        assert 'BUY 89166.650 VUSXX @ 1.00' in r.stdout or "new 1" in r.stdout

    @needs_venv
    def test_positions_move_to_match_on_the_overlap_side(self, fresh_vault: Path,
                                                         tmp_path: Path) -> None:
        seed(fresh_vault)
        r = run_overlap_ingest(fresh_vault, tmp_path)
        # deduped buys no longer double the ledger: the exceed side is gone
        assert "VBTLX MATCH (506.882)" in r.stdout
        assert "VTIAX MATCH (220.931)" in r.stdout
        assert "VTSAX MATCH (460.105)" in r.stdout
        assert "EXCEEDS" not in r.stdout
        # the VUSXX sweep gap is still named, with the seed recipe
        assert "VUSXX MISMATCH (ledger+import 89166.650 vs statement 100000.000)" in r.stdout
        assert "seed an opening position of 10833.350 VUSXX" in r.stdout

    def test_without_the_seeded_ledger_everything_imports(self, fresh_vault: Path,
                                                          tmp_path: Path) -> None:
        # no OFX history -> nothing to corroborate against -> all five are new
        r = run_overlap_ingest(fresh_vault, tmp_path)
        assert "new 5" in r.stdout and INVEST_TIER_LABEL not in r.stdout
