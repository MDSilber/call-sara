"""Re-export shim: the one rules reader lives in sara.rules.

Kept so `from rules import ...` in tools/ entrypoints keeps working while
the advisor layer migrates into the sara package. `categorize` accepts a
float amount from legacy callers and hands sara.rules a Decimal.
"""
from decimal import Decimal

from sara.rules import (  # noqa: F401
    EXPENSE_DEFAULT,
    INCOME_DEFAULT,
    chase_category,
    entry_by_acctid,
    match_rule,
    payee_rules,
    route_by_acctid,
)
from sara.rules import categorize as _categorize


def categorize(payee, ofx_type="", amount=0.0, primary_account=""):
    return _categorize(payee, ofx_type, Decimal(str(amount)), primary_account)
