"""Test harness: a scratch vault that exists BEFORE sara is imported.

sara.vault binds VAULT at import (the CLIs are one-shot processes), so the
suite creates one throwaway vault and points FINANCE_VAULT at it here, at
collection time. Tests that need a clean ledger use `fresh_vault`, which
rewrites the ledger directory in place.

Set FINANCE_TEST_VENV to any vault's .venv (init_vault.sh makes one) to
also run the bean-check/bean-query-backed paths — rollback, continuity
VERIFIED, the legacy fuzzy tier; without it those tests skip, exactly like
the legacy importer suite.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

PKG_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PKG_ROOT))

_VAULT_TMP = Path(tempfile.mkdtemp(prefix="sara-test-vault-"))
os.environ["FINANCE_VAULT"] = str(_VAULT_TMP / "vault")
atexit.register(shutil.rmtree, _VAULT_TMP, ignore_errors=True)

MAIN_BEANCOUNT = """\
option "title" "Scratch Test Ledger"
option "operating_currency" "USD"

2000-01-01 open Equity:Opening-Balances
2000-01-01 open Assets:US:Transfers               USD
2000-01-01 open Assets:US:Demo:Checking0766       USD
2000-01-01 open Liabilities:US:Demo:Card3333      USD
2000-01-01 open Assets:US:Demo:Brokerage8642  "FIFO"  ; units + cash; FIFO so {} sells book
2000-01-01 open Income:US:Dividends               USD
2000-01-01 open Income:US:CapGainsDistributions   USD
2000-01-01 open Income:US:Gains                   USD
2000-01-01 open Income:US:Other                   USD
2000-01-01 open Income:US:Interest                USD
2000-01-01 open Expenses:Uncategorized            USD
2000-01-01 open Expenses:Food:Dining              USD
2000-01-01 open Expenses:Food:Groceries           USD
"""

RULES_TOML = """\
[[accounts]]
last4 = "0766"
ledger_account = "Assets:US:Demo:Checking0766"

[[payee_rules]]
match = "WHOLE FOODS"
account = "Expenses:Food:Groceries"

[[payee_rules]]
match = "BLUE BOTTLE"
account = "Expenses:Food:Dining"

[[payee_rules]]
match = "Payment Thank You|ELECTRONIC TRANSFER|ACH IN"
account = "Assets:US:Transfers"

[sources.plaid.items.demo]
access_token_env = "PLAID_DEMO_ACCESS_TOKEN"
products = ["transactions"]
[sources.plaid.items.demo.accounts]
"plaid-acct-checking" = "Assets:US:Demo:Checking0766"
"plaid-acct-card" = "Liabilities:US:Demo:Card3333"

[sources.plaid.items.vg]
access_token_env = "PLAID_VG_ACCESS_TOKEN"
products = ["transactions", "investments"]
[sources.plaid.items.vg.accounts]
"plaid-acct-brokerage" = "Assets:US:Demo:Brokerage8642"
"""

# Ledger seeded so the fixtures' Plaid balances reconcile to the cent:
#   checking: 1765.73 + (2500.00 - 23.40) = 4242.33 (Plaid current)
#   card:    -1030.85 + (500.00 - 11.25) = -542.10 (Plaid current 542.10, credit)
OPENING = """\
2026-06-01 * "Opening balances (test seed)" ""
  Assets:US:Demo:Checking0766   1765.73 USD
  Liabilities:US:Demo:Card3333  -1030.85 USD
  Equity:Opening-Balances
"""


def build_vault(root: Path, seed_openings: bool = True) -> Path:
    if root.exists():
        shutil.rmtree(root)
    (root / "ledger").mkdir(parents=True)
    (root / "ledger" / "main.beancount").write_text(MAIN_BEANCOUNT)
    (root / "rules.toml").write_text(RULES_TOML)
    if seed_openings:
        (root / "ledger" / "2026.beancount").write_text(OPENING)
        with (root / "ledger" / "main.beancount").open("a") as fh:
            fh.write('include "2026.beancount"\n')
    venv = os.environ.get("FINANCE_TEST_VENV")
    if venv and (Path(venv) / "bin" / "bean-check").exists():
        link = root / ".venv"
        link.symlink_to(Path(venv).resolve())
    return root


def has_venv() -> bool:
    venv = os.environ.get("FINANCE_TEST_VENV")
    return bool(venv and (Path(venv) / "bin" / "bean-check").exists())


needs_venv = pytest.mark.skipif(
    not has_venv(), reason="FINANCE_TEST_VENV not set (bean-check/bean-query paths)")


@pytest.fixture()
def fresh_vault() -> Path:
    """The suite vault, rebuilt clean (openings seeded, venv linked if any)."""
    return build_vault(Path(os.environ["FINANCE_VAULT"]))


@pytest.fixture()
def bare_vault() -> Path:
    """The suite vault rebuilt with an EMPTY ledger (no opening balances)."""
    return build_vault(Path(os.environ["FINANCE_VAULT"]), seed_openings=False)
