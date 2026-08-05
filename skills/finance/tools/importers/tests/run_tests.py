#!/usr/bin/env python3
"""Golden-file regression tests for the importers (beangulp-style).

Run:  python3 skills/finance/tools/importers/tests/run_tests.py
      Plain python3 3.11+, nothing else — a scratch vault is built in a temp
      dir, so no real vault is ever touched. Exit 0 = all pass.

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
2000-01-01 open Liabilities:US:TestBank:Card7777  USD
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
match = "BLUE BOTTLE"
account = "Expenses:Food:Dining"

[chase_categories]
"Groceries" = "Expenses:Food:Groceries"
"""


def make_vault(tmp):
    vault = Path(tmp) / "vault"
    (vault / "ledger").mkdir(parents=True)
    (vault / "ledger" / "main.beancount").write_text(MAIN_BEANCOUNT)
    (vault / "rules.toml").write_text(RULES_TOML)
    venv = os.environ.get("FINANCE_TEST_VENV")
    if venv and (Path(venv) / "bin" / "bean-query").exists():
        (vault / ".venv").symlink_to(Path(venv).resolve())
        return vault, True
    return vault, False


def run(vault, tool, *args):
    env = {**os.environ, "FINANCE_VAULT": str(vault)}
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

        # 7. re-import: hash-exact dedupe, zero new
        r = run(vault, "importers/ofx.py", str(FIX / "bank.qfx"))
        check("re-import dedupes to zero (hash-exact)",
              r.returncode == 0 and "imported 0 transactions" in r.stderr
              and r.stderr.count("(hash)") == 4 and normalize(r.stdout) == "", r.stderr)

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

            # 11. checks surface the review-queue debt
            r = run(vault, "run_checks.py")
            check("run_checks reports uncategorized/FIXME debt",
                  r.returncode == 0 and "uncategorized transactions" in r.stdout, r.stdout)
        else:
            print("\n(skipped 3 venv-only tests: fuzzy fallback, anchored discrepancy, "
                  "run_checks — set FINANCE_TEST_VENV to run them)")

        # escape() hardening: statement text can never break out of a
        # Beancount string (quotes, backslash-escape tricks, newline injection)
        sys.path.insert(0, str(TOOLS))
        os.environ["FINANCE_VAULT"] = str(vault)
        from importers.common import escape  # noqa: E402
        evil = 'EVIL"\n2026-01-01 open Assets:Oops\\'
        check("escape() neutralizes quote/newline/backslash injection",
              escape(evil) == "EVIL' 2026-01-01 open Assets:Oops/", escape(evil))

    print(f"\n{'ALL PASS' if not FAILS else f'{len(FAILS)} FAILED: ' + ', '.join(FAILS)}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
