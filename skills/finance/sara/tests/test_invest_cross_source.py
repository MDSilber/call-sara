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

from sara.ledger.invest import build
from sara.ledger.writer import (
    CASH_MIRROR_TIER_LABEL,
    FAMILY_OFX,
    FAMILY_PLAID,
    INVEST_INCOME_TIER_LABEL,
    INVEST_TIER_LABEL,
    AccountDedupe,
    CashLegRow,
    InvestLedgerRow,
    claim_cash_mirror,
    claim_invest_duplicate,
    claim_transfer_pair,
    existing_ids,
    import_hash,
    invest_index,
    scan_invest_ledger,
)
from sara.sources.model import CanonInvestTxn
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
    "REINVEST DIV 103.170 VMFXX @ 1.00": Decimal("-103.17"),
    "INTEREST UST912797KS9": Decimal("1218.75"),
}

# a hand-booked maturity (this morning's treasury corrections shape: no
# import metadata -> family "") and the bank-side half of an external
# transfer already parked on Assets:US:Transfers
HAND_SEEDED_ENTRIES = """
2026-07-20 * "T-NOTE KS9 MATURITY (corrections)" ""
  Assets:US:Demo:Brokerage8642   50000.00 USD
  Income:US:Gains   -50000.00 USD

2026-07-19 * "TRANSFER TO VANGUARD" ""
  Assets:US:Demo:Checking0766   -2000.00 USD
  Assets:US:Transfers   2000.00 USD
"""

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
    _entry("2026-07-15", "REINVEST DIV 103.170 VMFXX @ 1.00", "REINVEST",
           "  Assets:US:Demo:Brokerage8642   103.170 VMFXX {1.00 USD}\n"
           "  Income:US:Dividends   -103.17 USD"),
    _entry("2026-07-17", "INTEREST UST912797KS9", "INCOME",
           "  Assets:US:Demo:Brokerage8642   1218.75 USD\n"
           "  Income:US:Interest   -1218.75 USD"),
]) + HAND_SEEDED_ENTRIES


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


_ROW_UNITS = object()  # default: candidate copies the row's units (exact match)


def claim(r: InvestLedgerRow, *, when: date | None = None,
          units: Decimal | object | None = _ROW_UNITS,
          total: Decimal | None = None, family: str = FAMILY_PLAID) -> bool:
    use_units = r.units if units is _ROW_UNITS else units
    assert use_units is None or isinstance(use_units, Decimal)
    return claim_invest_duplicate({"VBTLX": [r]}, when or r.when, "VBTLX", use_units,
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

    def test_waived_units_match_on_commodity_and_total(self) -> None:
        # the qty-unreported degradation: Vanguard-via-Plaid sweep reinvests
        # arrive as buy/qty 0/amount $X — the real-units ledger twin vouches
        r = row(units=D("103.170"), total=D("103.17"))
        assert claim(r, units=None, total=D("-103.17"))

    def test_waived_units_still_need_the_cents_and_window(self) -> None:
        r = row(units=D("103.170"), total=D("103.17"))
        assert not claim(r, units=None, total=D("103.30"))
        assert not claim(r, units=None, total=D("103.17"), when=date(2026, 7, 20))

    def test_explicit_zero_units_do_not_match_real_units(self) -> None:
        # the waiver is check_invest's translation, not a claim-level default
        r = row(units=D("103.170"), total=D("103.17"))
        assert not claim(r, units=D("0"), total=D("103.17"))

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
            {"security_id": "s-vmfxx", "ticker_symbol": "VMFXX", "name": "Federal MM"},
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
            {"investment_transaction_id": "pl-6", "account_id": "plaid-acct-brokerage",
             "security_id": "s-vmfxx", "date": "2026-07-16", "name": "Buy VANGUARD FEDERAL MM",
             "quantity": 0, "price": 0, "amount": 103.17, "fees": 0,
             "type": "buy", "subtype": "buy", "iso_currency_code": "USD"},
            {"investment_transaction_id": "pl-7", "account_id": "plaid-acct-brokerage",
             "security_id": "s-vmfxx", "date": "2026-07-18", "name": "Buy VANGUARD FEDERAL MM",
             "quantity": 0, "price": 0, "amount": 4.56, "fees": 0,
             "type": "buy", "subtype": "buy", "iso_currency_code": "USD"},
            {"investment_transaction_id": "pl-8", "account_id": "plaid-acct-brokerage",
             "security_id": "s-vmfxx", "date": "2026-07-16", "name": "Dividend VANGUARD FEDERAL MM",
             "quantity": 0, "price": 0, "amount": -103.17, "fees": 0,
             "type": "cash", "subtype": "dividend", "iso_currency_code": "USD"},
            {"investment_transaction_id": "pl-9", "account_id": "plaid-acct-brokerage",
             "security_id": None, "date": "2026-07-21", "name": "Corp Action (Redemption)",
             "quantity": 0, "price": 0, "amount": -50000.00, "fees": 0,
             "type": "cash", "subtype": "deposit", "iso_currency_code": "USD"},
            {"investment_transaction_id": "pl-10", "account_id": "plaid-acct-brokerage",
             "security_id": None, "date": "2026-07-18", "name": "INTEREST",
             "quantity": 0, "price": 0, "amount": -1218.75, "fees": 0,
             "type": "cash", "subtype": "interest", "iso_currency_code": "USD"},
            {"investment_transaction_id": "pl-11", "account_id": "plaid-acct-brokerage",
             "security_id": None, "date": "2026-07-11", "name": "Sweep in",
             "quantity": 0, "price": 0, "amount": 4866.07, "fees": 0,
             "type": "cash", "subtype": "withdrawal", "iso_currency_code": "USD"},
            {"investment_transaction_id": "pl-12", "account_id": "plaid-acct-brokerage",
             "security_id": None, "date": "2026-07-21", "name": "Funds Received",
             "quantity": 0, "price": 0, "amount": -2000.00, "fees": 0,
             "type": "cash", "subtype": "deposit", "iso_currency_code": "USD"},
            {"investment_transaction_id": "pl-13", "account_id": "plaid-acct-brokerage",
             "security_id": None, "date": "2026-07-25", "name": "Wire deposit external",
             "quantity": 0, "price": 0, "amount": -777.77, "fees": 0,
             "type": "cash", "subtype": "deposit", "iso_currency_code": "USD"},
        ],
        "total_investment_transactions": 13,
    }],
    # holdings claim MORE VUSXX than ledger+import can explain -> gap + seed
    "holdings": {
        "accounts": [{"account_id": "plaid-acct-brokerage", "name": "Demo Brokerage",
                      "mask": "8642", "type": "investment", "subtype": "brokerage",
                      "balances": {"current": 145000.00, "available": None,
                                   "iso_currency_code": "USD"}}],
        "securities": [
            {"security_id": "s-vmfxx", "ticker_symbol": "VMFXX", "name": "Federal MM"},
            {"security_id": "s-vbtlx", "ticker_symbol": "VBTLX", "name": "Total Bond"},
            {"security_id": "s-vtiax", "ticker_symbol": "VTIAX", "name": "Total Intl"},
            {"security_id": "s-vtsax", "ticker_symbol": "VTSAX", "name": "Total Stock"},
            {"security_id": "s-vusxx", "ticker_symbol": "VUSXX", "name": "Treasury MM"},
        ],
        "holdings": [
            {"account_id": "plaid-acct-brokerage", "security_id": "s-vmfxx",
             "quantity": 103.170, "institution_price": 1.00, "institution_value": 103.17,
             "institution_price_as_of": "2026-08-06", "iso_currency_code": "USD"},
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
        assert "fetched 13 -> mapped 13 | unmapped 0  ✓ counts reconcile" in r.stdout
        assert "new 4 (cash effect -86,393.44 USD), deduped 9" in r.stdout
        # BOTH Plaid rows of the one reinvest dedupe, each on its own facet
        assert f"deduped ({INVEST_TIER_LABEL}) 2026-07-16 -103.17 BUY 0.000 VMFXX @ 0.00" in r.stdout
        assert f"deduped ({INVEST_INCOME_TIER_LABEL}) 2026-07-16 103.17 DIV VMFXX" in r.stdout
        # the settlement fund's cash shadows dedupe against what the ledger
        # already represents: the fused buy's USD leg, the hand-booked
        # maturity, and the OFX coupon
        assert f"deduped ({CASH_MIRROR_TIER_LABEL}) 2026-07-11 -4866.07 Sweep in" in r.stdout
        assert f"deduped ({CASH_MIRROR_TIER_LABEL}) 2026-07-21 50000.00 Corp Action (Redemption)" in r.stdout
        assert f"deduped ({CASH_MIRROR_TIER_LABEL}) 2026-07-18 1218.75 INTEREST" in r.stdout
        # the un-twinned zero-qty row books as pure cash, bucketed loudly
        assert "cash-only invest rows (zero units): 1 — booked as settlement cash" in r.stdout
        assert "2026-07-18 -4.56 BUY 0.000 VMFXX @ 0.00" in r.stdout
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
        assert "new 4 (cash effect -86,393.44 USD)" in r.stdout

    @needs_venv
    def test_positions_move_to_match_on_the_overlap_side(self, fresh_vault: Path,
                                                         tmp_path: Path) -> None:
        seed(fresh_vault)
        r = run_overlap_ingest(fresh_vault, tmp_path)
        # deduped buys no longer double the ledger: the exceed side is gone
        assert "VBTLX MATCH (506.882)" in r.stdout
        assert "VTIAX MATCH (220.931)" in r.stdout
        assert "VTSAX MATCH (460.105)" in r.stdout
        assert "VMFXX MATCH (103.170)" in r.stdout  # cash-only rows add no units
        assert "EXCEEDS" not in r.stdout
        # the VUSXX sweep gap is still named, with the seed recipe
        assert "VUSXX MISMATCH (ledger+import 89166.650 vs statement 100000.000)" in r.stdout
        assert "seed an opening position of 10833.350 VUSXX" in r.stdout

    def test_without_the_seeded_ledger_everything_imports(self, fresh_vault: Path,
                                                          tmp_path: Path) -> None:
        # no OFX history -> nothing to corroborate against -> all 13 are new
        r = run_overlap_ingest(fresh_vault, tmp_path)
        assert "new 13" in r.stdout and INVEST_TIER_LABEL not in r.stdout
        assert "cash-only invest rows (zero units): 2" in r.stdout


class TestRendererZeroUnitsWall:
    """The last wall: nothing build() returns can divide by zero in booking."""

    def _action(self, units: str, kind: str = "BUYMF") -> CanonInvestTxn:
        return CanonInvestTxn(kind=kind, date=date(2026, 7, 1), source_id="F1",
                              ticker="VMFXX", units=Decimal(units),
                              price=Decimal("0"), total=Decimal("-19.68"))

    def test_exact_zero_units_books_as_pure_cash(self, fresh_vault: Path) -> None:
        entry, deltas, _used, cash_only = build(self._action("0"), BROKERAGE,
                                                "BUY 0.000 VMFXX @ 0.00", "ab" * 8)
        assert cash_only and deltas == {}
        assert "{" not in entry and "VMFXX" not in entry.splitlines()[-2]
        assert f"  {BROKERAGE}   -19.68 USD" in entry

    def test_dust_that_would_print_as_zero_books_as_pure_cash(self, fresh_vault: Path) -> None:
        # 4e-7 renders "0" at the 6-decimal posting precision -> same hazard
        entry, deltas, _used, cash_only = build(self._action("0.0000004"), BROKERAGE,
                                                "BUY 0 VMFXX @ 0.00", "cd" * 8)
        assert cash_only and deltas == {} and "{" not in entry

    def test_sub_penny_units_stay_a_position(self, fresh_vault: Path) -> None:
        a = CanonInvestTxn(kind="BUYSTOCK", date=date(2026, 7, 1), source_id="F2",
                           ticker="VMOT", units=Decimal("0.0001"),
                           price=Decimal("100.00"), total=Decimal("-0.01"))
        entry, deltas, _used, cash_only = build(a, BROKERAGE, "BUY 0.0001 VMOT @ 100.00",
                                                "ef" * 8)
        assert not cash_only
        assert "0.0001 VMOT {100.00 USD}" in entry
        assert deltas == {"VMOT": Decimal("0.0001")}

    def test_sells_and_reinvests_take_the_wall_too(self, fresh_vault: Path) -> None:
        for kind, sign in (("SELLMF", "19.68"), ("REINVEST", "-19.68")):
            a = CanonInvestTxn(kind=kind, date=date(2026, 7, 1), source_id="F3",
                               ticker="VMFXX", units=Decimal("0"),
                               price=Decimal("0"), total=Decimal(sign))
            entry, deltas, _used, cash_only = build(a, BROKERAGE, "X", "aa" * 8)
            assert cash_only and deltas == {} and "{" not in entry


class TestReinvestFacets:
    """One cross-family reinvest entry = two consumable facets: Plaid ships
    the event as a cash DIV plus a qty-0 buy, and BOTH must dedupe against
    the single fused OFX entry — never stealing each other's facet."""

    REINVEST = "\n" + _entry(
        "2026-07-15", "REINVEST DIV 103.170 VMFXX @ 1.00", "REINVEST",
        "  Assets:US:Demo:Brokerage8642   103.170 VMFXX {1.00 USD}\n"
        "  Income:US:Dividends   -103.17 USD")

    def _deduper(self, vault: Path) -> AccountDedupe:
        seed(vault, self.REINVEST)
        return AccountDedupe(BROKERAGE, *existing_ids())

    def test_index_marks_the_income_facet(self, fresh_vault: Path) -> None:
        seed(fresh_vault, self.REINVEST + '''
2026-07-16 * "BUY 1.000 VMFXX @ 1.00" ""
  ofx-type: "BUYMF"
  import-hash: "bbbbbbbbbbbbbb01"
  Assets:US:Demo:Brokerage8642   1.000 VMFXX {1.00 USD}
  Assets:US:Demo:Brokerage8642   -1.00 USD
''')
        rows = {r.when: r for r in invest_index(BROKERAGE)["VMFXX"]}
        assert rows[date(2026, 7, 15)].income_facet is True
        assert rows[date(2026, 7, 16)].income_facet is False

    def test_both_plaid_rows_of_one_reinvest_dedupe(self, fresh_vault: Path) -> None:
        d = self._deduper(fresh_vault)
        when = date(2026, 7, 16)
        # the cash DIV side claims the income facet...
        assert d.check_invest(when, D("103.17"), "DIV VMFXX", "pl-div",
                              kind="INCOME", ticker="VMFXX", units=D("0"),
                              family=FAMILY_PLAID) == INVEST_INCOME_TIER_LABEL
        # ...and the qty-0 buy still claims the units facet of the SAME entry
        assert d.check_invest(when, D("-103.17"), "BUY 0.000 VMFXX @ 0.00", "pl-buy",
                              kind="BUYSTOCK", ticker="VMFXX", units=D("0"),
                              family=FAMILY_PLAID) == INVEST_TIER_LABEL

    def test_each_facet_is_consumed_once(self, fresh_vault: Path) -> None:
        d = self._deduper(fresh_vault)
        when = date(2026, 7, 15)
        assert d.check_invest(when, D("103.17"), "DIV VMFXX", "pl-1", kind="INCOME",
                              ticker="VMFXX", units=D("0"),
                              family=FAMILY_PLAID) == INVEST_INCOME_TIER_LABEL
        assert d.check_invest(when, D("103.17"), "DIV VMFXX AGAIN", "pl-2", kind="INCOME",
                              ticker="VMFXX", units=D("0"), family=FAMILY_PLAID) is None

    def test_income_facet_never_claims_a_plain_buy(self, fresh_vault: Path) -> None:
        # against a buy (no Income: leg) the income FACET refuses; the row
        # falls through to the cash-mirror tier — labels keep the two honest
        seed(fresh_vault, '''
2026-07-15 * "BUY 103.170 VMFXX @ 1.00" ""
  ofx-type: "BUYMF"
  import-hash: "bbbbbbbbbbbbbb02"
  Assets:US:Demo:Brokerage8642   103.170 VMFXX {1.00 USD}
  Assets:US:Demo:Brokerage8642   -103.17 USD
''')
        d = AccountDedupe(BROKERAGE, *existing_ids())
        why = d.check_invest(date(2026, 7, 15), D("103.17"), "DIV VMFXX", "pl-3",
                             kind="INCOME", ticker="VMFXX", units=D("0"),
                             family=FAMILY_PLAID)
        assert why == CASH_MIRROR_TIER_LABEL
        assert why != INVEST_INCOME_TIER_LABEL

    def test_plain_cash_rows_never_touch_the_invest_index(self, fresh_vault: Path) -> None:
        d = self._deduper(fresh_vault)
        assert d.check_invest(date(2026, 7, 15), D("103.17"), "ACH IN", "pl-4",
                              kind="INVBANKTRAN", ticker="VMFXX", units=D("0"),
                              family=FAMILY_PLAID) is None


class TestCashMirror:
    """The third facet: a brokerage's cash-shadow rows against the entries
    the ledger already holds."""

    def _pool(self) -> list[CashLegRow]:
        return [CashLegRow(date(2026, 7, 10), D("4866.07"), FAMILY_OFX)]

    def test_claims_sign_blind_within_cents_and_window(self) -> None:
        pool = self._pool()
        assert claim_cash_mirror(pool, date(2026, 7, 11), D("-4866.07"), FAMILY_PLAID)
        assert pool == []  # consumed

    def test_window_and_cents_edges(self) -> None:
        assert claim_cash_mirror(self._pool(), date(2026, 7, 15), D("4866.08"), FAMILY_PLAID)
        assert not claim_cash_mirror(self._pool(), date(2026, 7, 16), D("4866.07"), FAMILY_PLAID)
        assert not claim_cash_mirror(self._pool(), date(2026, 7, 11), D("4866.09"), FAMILY_PLAID)

    def test_same_family_never_claims(self) -> None:
        assert not claim_cash_mirror(self._pool(), date(2026, 7, 11), D("4866.07"), FAMILY_OFX)
        hand = [CashLegRow(date(2026, 7, 10), D("50000.00"), "")]
        assert claim_cash_mirror(hand, date(2026, 7, 11), D("50000.00"), FAMILY_PLAID)

    def test_scan_indexes_every_cash_moving_entry_shape(self, fresh_vault: Path) -> None:
        seed(fresh_vault)
        _idx, cash = scan_invest_ledger(BROKERAGE)
        amounts = sorted(c.amount for c in cash)
        # fused buys, the coupon, the hand maturity — reinvests (net 0) excluded
        assert amounts == [D("1218.75"), D("4866.07"), D("6848.86"),
                           D("44818.20"), D("50000.00")]
        assert {c.family for c in cash} == {FAMILY_OFX, ""}

    def test_position_kinds_never_use_the_mirror(self, fresh_vault: Path) -> None:
        seed(fresh_vault, HAND_SEEDED_ENTRIES)
        d = AccountDedupe(BROKERAGE, *existing_ids())
        # a BUY candidate matching the maturity's |50,000| must NOT mirror-claim
        why = d.check_invest(date(2026, 7, 21), D("-50000.00"), "BUY 50000.000 VUSXX @ 1.00",
                             "pl-x", kind="BUYSTOCK", ticker="VUSXX",
                             units=D("50000"), family=FAMILY_PLAID)
        assert why is None


class TestTransferPairing:
    def test_claims_within_window_and_cents_and_consumes(self) -> None:
        pool = [(date(2026, 7, 19), D("2000.00"))]
        assert claim_transfer_pair(pool, date(2026, 7, 21), D("2000.00"))
        assert pool == []
        assert not claim_transfer_pair([(date(2026, 7, 10), D("2000.00"))],
                                       date(2026, 7, 21), D("2000.00"))
        assert not claim_transfer_pair([(date(2026, 7, 19), D("2000.05"))],
                                       date(2026, 7, 21), D("2000.00"))

    @needs_venv
    def test_funds_received_routes_to_transfers_with_the_label(self, fresh_vault: Path,
                                                               tmp_path: Path) -> None:
        seed(fresh_vault)
        r = run_overlap_ingest(fresh_vault, tmp_path)
        assert "transfer-paired 1 -> Assets:US:Transfers" in r.stdout
        assert "2026-07-21 2000.00 Funds Received" in r.stdout

    @needs_venv
    def test_paired_entry_books_against_transfers_not_income(self, fresh_vault: Path,
                                                             tmp_path: Path) -> None:
        seed(fresh_vault)
        fixture = tmp_path / "overlap"
        env = {**os.environ, "FINANCE_VAULT": str(fresh_vault),
               "SARA_PLAID_FIXTURE": str(fixture), "PYTHONPATH": str(PKG_ROOT)}
        run_overlap_ingest(fresh_vault, tmp_path)  # writes the fixture dir
        r = subprocess.run([sys.executable, "-m", "sara.ingest", "--item", "vg",
                            "--verbose"], capture_output=True, text=True, env=env)
        entries = r.stdout.split("--- entries ---", 1)[1]
        block = entries.split('"Funds Received"')[1].split("2026-07-25")[0]
        assert "Assets:US:Transfers" in block
        assert "Income:US:Other" not in block

    @needs_venv
    def test_unpaired_external_deposit_survives_to_review(self, fresh_vault: Path,
                                                          tmp_path: Path) -> None:
        seed(fresh_vault)
        r = run_overlap_ingest(fresh_vault, tmp_path)
        assert "transfer-paired 1" in r.stdout  # exactly one — the wire is NOT paired
        assert "Wire deposit external" not in r.stdout.split("transfer-paired")[1].split("positions")[0]
