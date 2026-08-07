#!/usr/bin/env python3
"""Golden-file regression tests for the importers (beangulp-style).

Run:  python3 skills/finance/tools/importers/tests/run_tests.py
      Python 3.11+ with the sara package's deps importable (pydantic — any
      vault venv after init_vault.sh has it, or `pip install pydantic`); a
      scratch vault is built in a temp dir, so no real vault is ever
      touched. Exit 0 = all pass. The importers under test are shims into
      skills/finance/sara/ — this suite is the behavioral contract the
      package rewrite was built against, and it drives the real new path.

Optional: FINANCE_TEST_VENV=/path/to/a/vault/.venv (a venv holding beancount
+ beanquery, e.g. one made by scripts/init_vault.sh). When set, the
bean-query-backed paths run too: ledger-anchored continuity VERIFIED /
DISCREPANCY, the ±5-day fuzzy dedupe fallback, and bean-check after --write.

Goldens (expected/*) hold the venv-independent slice of stdout — transaction
entries only (comment lines and balance assertions are normalized away, since
those legitimately differ with/without a venv). After an INTENDED output
change:  run_tests.py --regen  rewrites them; review the diff.
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent.parent          # skills/finance/tools
FIX = HERE / "fixtures"
EXP = HERE / "expected"

MAIN_BEANCOUNT = """\
option "title" "Scratch Test Ledger"
option "operating_currency" "USD"

2000-01-01 open Equity:Opening-Balances
2000-01-01 open Assets:US:Transfers               USD
2000-01-01 open Assets:US:TestBank:Checking9123   USD
  owner: "alex"
2000-01-01 open Liabilities:US:TestBank:Card7777  USD
  owner: "jordan"
2000-01-01 open Assets:US:TestBroker:Brokerage8642  "FIFO"  ; units + cash; FIFO so {} sells book — deliberately untagged: exercises the `unassigned` owner bucket
2000-01-01 open Income:US:Dividends               USD
2000-01-01 open Income:US:CapGainsDistributions   USD
2000-01-01 open Income:US:Gains                   USD
2000-01-01 open Income:US:Other                   USD
2000-01-01 open Income:US:Interest                USD
2000-01-01 open Expenses:Uncategorized            USD
2000-01-01 open Expenses:Cash                     USD
2000-01-01 open Expenses:Food:Dining              USD
2000-01-01 open Expenses:Food:Groceries           USD
"""

RULES_TOML = """\
[[accounts]]
last4 = "9123"
ledger_account = "Assets:US:TestBank:Checking9123"

[[accounts]]
last4 = "8642"
ledger_account = "Assets:US:TestBroker:Brokerage8642"

[[payee_rules]]
ofx_type = "ATM"
account = "Expenses:Cash"

[[payee_rules]]
match = "TRANSFER"
account = "Assets:US:Transfers"

[[payee_rules]]
match = "BLUE BOTTLE"
account = "Expenses:Food:Dining"

[chase_categories]
"Groceries" = "Expenses:Food:Groceries"
"""


def make_vault(tmp, name="vault"):
    vault = Path(tmp) / name
    (vault / "ledger").mkdir(parents=True)
    (vault / "ledger" / "main.beancount").write_text(MAIN_BEANCOUNT)
    (vault / "rules.toml").write_text(RULES_TOML)
    venv = os.environ.get("FINANCE_TEST_VENV")
    if venv and (Path(venv) / "bin" / "bean-query").exists():
        (vault / ".venv").symlink_to(Path(venv).resolve())
        return vault, True
    return vault, False


def run(vault, tool, *args, extra_env=None):
    env = {**os.environ, "FINANCE_VAULT": str(vault), **(extra_env or {})}
    return subprocess.run([sys.executable, str(TOOLS / tool), *args],
                          capture_output=True, text=True, env=env)


def normalize(text):
    """Transactions only: drop comments and balance directives (they vary by
    venv availability), trailing spaces, and blank-line runs."""
    keep = [ln.rstrip() for ln in text.splitlines()
            if not ln.lstrip().startswith(";")
            and not re.match(r"^\d{4}-\d{2}-\d{2} balance ", ln)]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()
    return out + "\n" if out else ""


FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        FAILS.append(name)
        if detail:
            print("      " + "\n      ".join(str(detail).splitlines()[:12]))


def golden(name, fname, got, regen):
    path = EXP / fname
    if regen:
        path.write_text(got)
        print(f"REGEN {name} -> {path.name}")
        return
    want = path.read_text() if path.exists() else "<missing golden>"
    check(name, got == want, f"--- got ---\n{got}\n--- want ---\n{want}")


def main():
    regen = "--regen" in sys.argv[1:]
    with tempfile.TemporaryDirectory() as tmp:
        vault, has_venv = make_vault(tmp)
        mode = "with venv (bean-query paths live)" if has_venv else "hermetic (no venv)"
        print(f"# scratch vault {vault} — {mode}\n")

        # 1. clean OFX import: golden entries, all four kept, continuity tagged
        r = run(vault, "importers/ofx.py", str(FIX / "bank.qfx"))
        golden("ofx clean import (golden)", "bank_qfx.beancount", normalize(r.stdout), regen)
        check("ofx clean import: 4 kept, exit 0",
              r.returncode == 0 and "imported 4 transactions" in r.stderr, r.stderr)
        want_tag = "VERIFIED" if has_venv else "UNVERIFIABLE"
        check(f"ofx continuity {want_tag}", f"continuity {want_tag}" in r.stderr, r.stderr)

        # 2. clean Chase CSV import: golden + FIXME fallback for unmatched merchant
        r = run(vault, "importers/chase_csv.py", str(FIX / "card.csv"),
                "Liabilities:US:TestBank:Card7777")
        golden("chase clean import (golden)", "card_csv.beancount", normalize(r.stdout), regen)
        check("chase: uncategorizable row lands in review queue, never blocks",
              r.returncode == 0 and "Expenses:Uncategorized" in r.stdout, r.stdout)
        check("chase without Balance column: continuity UNVERIFIABLE",
              "continuity UNVERIFIABLE" in r.stderr, r.stderr)

        # 3. holdings OFX: golden (prices + table are venv-independent)
        r = run(vault, "importers/holdings_ofx.py", str(FIX / "holdings.qfx"))
        golden("holdings import (golden)", "holdings_qfx.txt", r.stdout, regen)
        check("holdings exit 0", r.returncode == 0, r.stderr)

        # 4. continuity pass: running Balance column chains (newest-first order)
        r = run(vault, "importers/chase_csv.py", str(FIX / "bal_ok.csv"),
                "Liabilities:US:TestBank:Card7777")
        check("csv continuity VERIFIED",
              r.returncode == 0 and "continuity VERIFIED" in r.stderr, r.stderr)
        check("csv continuity VERIFIED: closing balance assertion emitted",
              "balance Liabilities:US:TestBank:Card7777   1204.10 USD" in r.stdout, r.stdout)

        # 5. continuity fail: blocks without --allow-discrepancy, passes with it
        r = run(vault, "importers/chase_csv.py", str(FIX / "bal_bad.csv"),
                "Liabilities:US:TestBank:Card7777")
        check("csv continuity DISCREPANCY blocks",
              r.returncode != 0 and "DISCREPANCY" in r.stderr
              and "BLOCKED" in r.stderr and normalize(r.stdout) == "", r.stderr)
        r = run(vault, "importers/chase_csv.py", str(FIX / "bal_bad.csv"),
                "Liabilities:US:TestBank:Card7777", "--allow-discrepancy")
        check("--allow-discrepancy overrides the block",
              r.returncode == 0 and "imported 3 transactions" in r.stderr, r.stderr)

        # 6. --write appends, keeps includes complete, validates
        r = run(vault, "importers/ofx.py", str(FIX / "bank.qfx"), "--write")
        year = vault / "ledger" / "2026.beancount"
        main_txt = (vault / "ledger" / "main.beancount").read_text()
        check("--write appends to ledger/<year>.beancount",
              r.returncode == 0 and year.exists()
              and year.read_text().count("import-hash:") == 4, r.stderr)
        check("--write adds the year include to main.beancount",
              'include "2026.beancount"' in main_txt, main_txt)

        # 7. re-import: FITID-exact dedupe, zero new (M1: overlapping
        # re-import of the same file still fully dedupes)
        r = run(vault, "importers/ofx.py", str(FIX / "bank.qfx"))
        check("re-import dedupes to zero (fitid-exact)",
              r.returncode == 0 and "imported 0 transactions" in r.stderr
              and r.stderr.count("(fitid)") == 4 and normalize(r.stdout) == "", r.stderr)

        # 8. --all really disables dedupe (allow the discrepancy the dupes cause)
        r = run(vault, "importers/ofx.py", str(FIX / "bank.qfx"), "--all",
                "--allow-discrepancy")
        check("--all disables dedupe",
              r.returncode == 0 and "imported 4 transactions" in r.stderr, r.stderr)

        if has_venv:
            # 9. fuzzy fallback: a pre-hash ledger entry 2 days off still dedupes
            with year.open("a") as fh:
                fh.write('\n2026-08-02 * "LEGACY STORE 12" ""\n'
                         "  Assets:US:TestBank:Checking9123   -55.00 USD\n"
                         "  Expenses:Uncategorized\n")
            r = run(vault, "importers/ofx.py", str(FIX / "legacy.qfx"))
            check("pre-hash ledger entry dedupes via ±5d fuzzy fallback",
                  "imported 0 transactions" in r.stderr and "(±5d)" in r.stderr, r.stderr)

            # 10. ledger-anchored OFX discrepancy blocks
            r = run(vault, "importers/ofx.py", str(FIX / "bank_bad_balance.qfx"))
            check("ofx ledger-anchored DISCREPANCY blocks",
                  r.returncode != 0 and "DISCREPANCY" in r.stderr
                  and "BLOCKED" in r.stderr, r.stderr)

            # 11. checks surface the review-queue gate (it fires only when
            # uncategorized rows exceed 5% of the current month's postings —
            # this vault is nearly all uncategorized, so it must fire)
            r = run(vault, "run_checks.py")
            check("run_checks fires the review-queue gate",
                  r.returncode == 0 and "is uncategorized" in r.stdout, r.stdout)
        else:
            print("\n(skipped 3 venv-only tests: fuzzy fallback, anchored discrepancy, "
                  "run_checks — set FINANCE_TEST_VENV to run them)")

        # 12. invest OFX: golden entries, all 7 kept, positions tag
        r = run(vault, "importers/invest_ofx.py", str(FIX / "vanguard.qfx"))
        golden("invest clean import (golden)", "vanguard_qfx.beancount",
               normalize(r.stdout), regen)
        check("invest clean import: 7 kept, exit 0",
              r.returncode == 0 and "imported 7 transactions" in r.stderr, r.stderr)
        check("invest positions " + ("MATCH" if has_venv else "UNVERIFIABLE"),
              ("2026-06-30 MATCH \u2014" if has_venv else "positions UNVERIFIABLE")
              in r.stderr, r.stderr)

        # 13. (venv) --since creates a history gap -> positions MISMATCH + seed recipe
        if has_venv:
            r = run(vault, "importers/invest_ofx.py", str(FIX / "vanguard.qfx"),
                    "--since", "2026-05-01")
            check("invest --since gap: positions MISMATCH, never blocks",
                  r.returncode == 0 and "2026-06-30 MISMATCH \u2014" in r.stderr, r.stderr)
            check("invest MISMATCH suggests seeding the opening position",
                  "seed an opening position of 10.500 TIF, 4.000 TWC" in r.stderr, r.stderr)

        # 14. invest --write appends (venv: bean-check validates the lot dialect
        # end-to-end — {cost} buys, {{total}} commission buy, {} sell under FIFO)
        r = run(vault, "importers/invest_ofx.py", str(FIX / "vanguard.qfx"), "--write")
        year_txt = year.read_text()
        check("invest --write appends buys/sell/income to the year file",
              r.returncode == 0 and "wrote" in r.stderr
              and year_txt.count("import-hash:") == 11
              and "-3.000 TIF {} @ 42.00 USD" in year_txt
              and "4.000 TWC {{401.00 USD}}" in year_txt, r.stderr)

        # 15. invest re-import: hash dedupe to zero; (venv) positions still MATCH
        r = run(vault, "importers/invest_ofx.py", str(FIX / "vanguard.qfx"))
        check("invest re-import dedupes to zero (fitid-exact)",
              r.returncode == 0 and "imported 0 transactions" in r.stderr
              and r.stderr.count("(fitid)") == 7 and normalize(r.stdout) == "", r.stderr)
        if has_venv:
            check("invest re-import: positions still MATCH",
                  "2026-06-30 MATCH \u2014" in r.stderr, r.stderr)

        # M1: same-day identical pair with DISTINCT FITIDs both import
        r = run(vault, "importers/ofx.py", str(FIX / "twins.qfx"))
        check("M1: same-day identical pair w/ distinct FITIDs both import",
              r.returncode == 0 and "imported 2 transactions" in r.stderr
              and r.stdout.count("GYM DROP-IN FEE") == 2, r.stderr + r.stdout)

        # M2: comma amounts parse; ambiguous/corrupt shapes report-and-skip
        r = run(vault, "importers/ofx.py", str(FIX / "commas.qfx"))
        check("M2: '2,500.00' imports as 2500.00",
              r.returncode == 0 and "2500.00 USD" in r.stdout
              and "imported 1 transactions" in r.stderr, r.stderr + r.stdout)
        check("M2: '1.2.3' and '-1.234,56' rows skipped with a message",
              "'1.2.3'" in r.stderr and "'-1.234,56'" in r.stderr
              and "1.2.3" not in r.stdout, r.stderr)

        # M10: truncated export (no closing tags before EOF) still parses fully
        r = run(vault, "importers/ofx.py", str(FIX / "truncated.qfx"))
        check("M10: truncated export imports all rows (or warns loudly)",
              r.returncode == 0 and ("imported 3 transactions" in r.stderr
                                     or "WARNING" in r.stderr), r.stderr)
        check("M10: nothing silently dropped from the truncated export",
              "imported 3 transactions" in r.stderr and "NEWSSTAND KIOSK" in r.stdout,
              r.stderr + r.stdout)

        # Routing miss lists the known options: an unrouted ACCTID prints the
        # configured [[accounts]] table so a typo'd rules entry is instantly
        # visible (ofx + invest); chase_csv (no ACCTID in a CSV) lists the
        # options when the account argument is missing.
        miss = Path(tmp) / "unrouted.qfx"
        miss.write_text((FIX / "bank.qfx").read_text().replace("000009123", "000005599"))
        r = run(vault, "importers/ofx.py", str(miss))
        check("routing miss (ofx): skip message lists configured last4 -> account",
              r.returncode == 0 and "no [[accounts]] entry" in r.stderr
              and "9123 -> Assets:US:TestBank:Checking9123" in r.stderr
              and "8642 -> Assets:US:TestBroker:Brokerage8642" in r.stderr, r.stderr)
        missi = Path(tmp) / "unrouted_invest.qfx"
        missi.write_text((FIX / "vanguard.qfx").read_text().replace("777008642", "777005599"))
        r = run(vault, "importers/invest_ofx.py", str(missi))
        check("routing miss (invest): skip message lists configured routing",
              r.returncode == 0 and "no [[accounts]] entry" in r.stderr
              and "9123 -> Assets:US:TestBank:Checking9123" in r.stderr, r.stderr)
        r = run(vault, "importers/chase_csv.py", str(FIX / "card.csv"))
        check("chase_csv without an account: usage lists configured routing",
              r.returncode != 0 and "9123 -> Assets:US:TestBank:Checking9123"
              in (r.stderr + r.stdout), r.stderr + r.stdout)

        # M6a: triple re-import of one file yields exactly ONE assertion
        r = run(vault, "importers/ofx.py", str(FIX / "bank.qfx"), "--write")
        r2 = run(vault, "importers/ofx.py", str(FIX / "bank.qfx"), "--write")
        year_txt = year.read_text()
        check("M6a: triple re-import of one file yields exactly one assertion",
              r.returncode == 0 and r2.returncode == 0
              and year_txt.count("balance Assets:US:TestBank:Checking9123   2170.37 USD") == 1,
              year_txt[-800:])

        # M6b: a row after LEDGERBAL's DTASOF caps the assertion at asof+1,
        # so the import can never write an assertion it itself breaks
        r = run(vault, "importers/ofx.py", str(FIX / "bank_late_row.qfx"), "--write")
        year_txt = year.read_text()
        check("M6b: post-DTASOF row imports with the assertion capped at asof+1",
              r.returncode == 0
              and "2026-07-16 balance Assets:US:TestBank:Checking9123   2160.37 USD" in year_txt
              and "2026-07-19 balance" not in year_txt, r.stderr + year_txt[-600:])
        if has_venv:
            check("M6b: capped assertion is VERIFIED and survives bean-check",
                  "continuity VERIFIED" in r.stderr and "wrote" in r.stderr, r.stderr)

        # M1: Monday+Thursday same-amount same-merchant rows in successive
        # files both import — fuzzy no longer eats hash/fitid-bearing entries
        if has_venv:
            r = run(vault, "importers/ofx.py", str(FIX / "monday.qfx"), "--write")
            check("M1: Monday parking row imports and writes",
                  r.returncode == 0 and "imported 1 transactions" in r.stderr, r.stderr)
            r = run(vault, "importers/ofx.py", str(FIX / "thursday.qfx"))
            check("M1: Thursday same-amount same-merchant row still imports",
                  r.returncode == 0 and "imported 1 transactions" in r.stderr
                  and "(±5d)" not in r.stderr, r.stderr)

        # M5: --since must be exactly YYYY-MM-DD, in every importer
        for tool, extra in (("importers/ofx.py", ()),
                            ("importers/chase_csv.py", ("Liabilities:US:TestBank:Card7777",)),
                            ("importers/invest_ofx.py", ())):
            src = FIX / ("card.csv" if "csv" in tool else
                         "vanguard.qfx" if "invest" in tool else "bank.qfx")
            r = run(vault, tool, str(src), *extra, "--since", "2026-6-1")
            check(f"M5: {tool.split('/')[-1]} rejects --since 2026-6-1 with usage",
                  r.returncode != 0 and "--since needs a YYYY-MM-DD" in r.stderr, r.stderr)

        # M4: a BUY with neither UNITPRICE nor TOTAL must not mint {0.00} lots
        r = run(vault, "importers/invest_ofx.py", str(FIX / "zero_basis.qfx"))
        check("M4: zero-basis BUY (no UNITPRICE, no TOTAL) reports-and-skips",
              r.returncode == 0 and "refusing to mint" in r.stderr
              and "{0.00" not in r.stdout
              and "imported 1 transactions" in r.stderr, r.stderr + r.stdout)

        # M8: negative-unit REINVEST (correction) preserves direction
        r = run(vault, "importers/invest_ofx.py", str(FIX / "reinvest_neg.qfx"))
        check("M8: negative REINVEST books -units and a positive income leg",
              r.returncode == 0 and "-0.500 TIF {40.00 USD}" in r.stdout
              and "Income:US:Dividends   20.00 USD" in r.stdout, r.stdout)
        if has_venv:
            r = run(vault, "importers/invest_ofx.py", str(FIX / "reinvest_neg.qfx"), "--write")
            check("M8: reinvest + its reversal balance through bean-check",
                  r.returncode == 0 and "wrote" in r.stderr, r.stderr)

        # M13: query.py spend with a malformed period exits with usage
        r = run(vault, "query.py", "spend", "2026-07-01")
        check("M13: query.py spend 2026-07-01 exits with usage, not a crash",
              r.returncode != 0 and "usage: spend" in r.stderr
              and "Traceback" not in r.stderr, r.stderr)

        # --- M3/M7: recategorize on its own vault --------------------------
        vault3, _ = make_vault(tmp, "vault3")
        y3 = vault3 / "ledger" / "2026.beancount"
        y3.write_text(
            '2026-03-01 * "OFFICE SUPPLY STORE" ""\n'
            "  Assets:US:TestBank:Checking9123  -120.00 USD\n"
            "  Assets:US:Transfers                70.00 USD\n"
            "  Expenses:Uncategorized\n"
            "\n"
            '2026-03-02 * "COFFEE CART" ""\n'
            "  Assets:US:TestBank:Checking9123  -4.50 USD\n"
            "  Expenses:Uncategorized\n")
        main3 = vault3 / "ledger" / "main.beancount"
        main3.write_text(main3.read_text() + 'include "2026.beancount"\n')
        r = run(vault3, "recategorize.py")
        check("M3: three-leg split stays an expense (residual-keyed fallback)",
              r.returncode == 0 and "Income:US:Other" not in r.stdout
              and "nothing to change" in r.stdout, r.stdout)
        r = run(vault3, "recategorize.py", "--target")
        check("M7: --target without a value exits with usage",
              r.returncode != 0 and "needs an account name" in (r.stderr + r.stdout),
              r.stderr + r.stdout)
        if has_venv:
            (vault3 / "rules.toml").write_text(
                (vault3 / "rules.toml").read_text()
                + '\n[[payee_rules]]\nmatch = "COFFEE CART"\n'
                  'account = "Expenses:DoesNotExist:Yet"\n')
            before = y3.read_text()
            r = run(vault3, "recategorize.py", "--write")
            check("M7: rule at an unopened account rolls back cleanly",
                  r.returncode != 0 and "rolled back" in (r.stderr + r.stdout)
                  and y3.read_text() == before, r.stderr + r.stdout)

        # --- M9: a commented ;include must not satisfy the include check ---
        vault4, _ = make_vault(tmp, "vault4")
        main4 = vault4 / "ledger" / "main.beancount"
        main4.write_text(main4.read_text() + ';include "2026.beancount"\n')
        r = run(vault4, "importers/ofx.py", str(FIX / "bank.qfx"), "--write")
        check("M9: commented ;include gets a real include added on --write",
              r.returncode == 0
              and re.search(r'^include "2026\.beancount"', main4.read_text(), re.M)
              is not None, main4.read_text())

        # --- M11: update_prices harvests only ACTIVE price: tags -----------
        (vault4 / "ledger" / "prices.beancount").write_text("")
        main4.write_text(main4.read_text()
                         + '\n; 2000-01-01 commodity FAKE\n;   price: "USD:yahoo/FAKE"\n')
        r = subprocess.run(["bash", str(TOOLS.parent / "scripts" / "update_prices.sh")],
                           capture_output=True, text=True,
                           env={**os.environ, "FINANCE_VAULT": str(vault4)})
        check("M11: update_prices ignores commented price: tags, exits informatively",
              r.returncode == 0 and "no price: metadata" in r.stdout,
              r.stdout + r.stderr)

        # escape() hardening: statement text can never break out of a
        # Beancount string (quotes, backslash-escape tricks, newline injection)
        sys.path.insert(0, str(TOOLS))
        os.environ["FINANCE_VAULT"] = str(vault)
        from importers.common import escape  # noqa: E402
        evil = 'EVIL"\n2026-01-01 open Assets:Oops\\'
        check("escape() neutralizes quote/newline/backslash injection",
              escape(evil) == "EVIL' 2026-01-01 open Assets:Oops/", escape(evil))

        # M12: amount() never reads non-USD units as dollars
        from vault import amount  # noqa: E402
        table = [(("1,234.56 USD",), 1234.56),
                 (("12.000 VTSAX",), 0.0),
                 (("1,234.56 USD, 12.000 VTSAX",), 1234.56),
                 (("-42.5",), -42.5),
                 (("123,456.78 USD.EQ",), 0.0),
                 (("123,456.78 USD.EQ", "USD.EQ"), 123456.78),
                 (("",), 0.0)]
        bad = [(a, amount(*a), want) for a, want in table if amount(*a) != want]
        check("M12: amount() USD-math table (units are never dollars)",
              not bad, bad)

        # calc.py: arithmetic through code — Decimal-exact, strict AST allowlist
        r = run(vault, "calc.py", "0.1 + 0.2")
        check("calc: 0.1 + 0.2 is exactly 0.3",
              r.returncode == 0
              and r.stdout.splitlines()[:2] == ["0.3", "money: $0.30"],
              r.stdout + r.stderr)
        r = run(vault, "calc.py", "123456789012345678901234567890 + 1")
        check("calc: big integers stay exact",
              r.returncode == 0
              and r.stdout.splitlines()[0] == "123456789012345678901234567891",
              r.stdout + r.stderr)
        r = run(vault, "calc.py", "7300.50 / 12")
        check("calc: decimal division (608.375, money $608.38)",
              r.returncode == 0
              and r.stdout.splitlines()[:2] == ["608.375", "money: $608.38"],
              r.stdout + r.stderr)
        r = run(vault, "calc.py", "round(2.5)")
        check("calc: round() is half-up (money convention)",
              r.returncode == 0 and r.stdout.splitlines()[0] == "3", r.stdout)
        for expr, why in (("__import__('os')", "imports"), ("a + 1", "names"),
                          ("(1).__class__", "dunder attributes"),
                          ("'x' * 3", "strings")):
            r = run(vault, "calc.py", expr)
            check(f"calc: rejects {why}",
                  r.returncode != 0 and "refused" in r.stderr
                  and "Traceback" not in r.stderr, r.stderr)
        r = run(vault, "calc.py", "1 / 0")
        check("calc: division by zero is a clean error",
              r.returncode != 0 and "division by zero" in r.stderr
              and "Traceback" not in r.stderr, r.stderr)

        # crosscheck.py: dual-computation gate — 4/4 agree on the scratch
        # ledger (the owners check runs ACTIVE: two opens carry owner
        # metadata, one is untagged, so the partition includes all three
        # buckets); a skewed independent path (test seam) must refuse loudly
        if has_venv:
            r = run(vault, "crosscheck.py")
            check("crosscheck: clean ledger, 4/4 agree",
                  r.returncode == 0 and "cross-checks: 4/4 agree" in r.stdout,
                  r.stdout + r.stderr)
            for name in ("liquid", "spend", "assets", "owners"):
                r = run(vault, "crosscheck.py",
                        extra_env={"FINANCE_CROSSCHECK_INJECT": f"{name}:123.45"})
                check(f"crosscheck: injected {name} skew refuses (exit 2)",
                      r.returncode == 2 and "DUAL-COMPUTATION MISMATCH" in r.stderr,
                      r.stdout + r.stderr)
            r = run(vault, "reports.py",
                    extra_env={"FINANCE_CROSSCHECK_INJECT": "liquid:0.02"})
            check("crosscheck: reports.py refuses to emit on a 2-cent mismatch",
                  r.returncode == 2 and "DUAL-COMPUTATION MISMATCH" in r.stderr
                  and not (vault / "reports" / "net-worth.md").exists(),
                  r.stdout + r.stderr)
            r = run(vault, "reports.py")
            check("crosscheck: clean reports.py run emits the 4/4 line",
                  r.returncode == 0 and "cross-checks: 4/4 agree" in r.stdout,
                  r.stdout + r.stderr)

            # the owner lens: --by-owner splits by open-directive metadata,
            # untagged accounts land in `unassigned`, and the two-person
            # 50/50 convenience line rides with its convention label
            # (jordan's card is settled to $0 by now, so only alex and the
            # untagged accounts hold balance — and with one person and no
            # joint slice the 50/50 convenience line must stay away)
            r = run(vault, "query.py", "networth", "--by-owner")
            check("owners: networth --by-owner splits alex vs unassigned, "
                  "no 50/50 line without joint",
                  r.returncode == 0 and "By owner" in r.stdout
                  and "alex" in r.stdout and "unassigned" in r.stdout
                  and "50/50-attributed" not in r.stdout,
                  r.stdout + r.stderr)
            r = run(vault, "query.py", "balances")
            check("owners: balances carries the owner column once metadata exists",
                  r.returncode == 0 and "alex" in r.stdout and "unassigned" in r.stdout,
                  r.stdout + r.stderr)

    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
