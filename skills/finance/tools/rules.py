"""Categorization + routing engine, driven entirely by the vault's rules.toml.

No household strings live in code. To teach the system a new merchant or
payee, add a [[payee_rules]] block to $VAULT/rules.toml — the importers and
recategorize.py pick it up on the next run.
"""
import re
import sys

from vault import rules

INCOME_DEFAULT = "Income:US:Other"
EXPENSE_DEFAULT = "Expenses:Uncategorized"
TRANSFER_ACCOUNT = "Assets:US:Transfers"
PREDICATES = ("match", "ofx_type", "min_amount", "max_amount")


def match_rule(payee, ofx_type="", amount=0.0):
    """The account of the first matching [[payee_rules]] entry, else None.

    A rule with none of the recognized predicates (match / ofx_type /
    min_amount / max_amount) is ignored rather than matching everything —
    almost always a typo'd key.
    """
    text = payee or ""
    ttype = (ofx_type or "").upper()
    mag = abs(amount)
    for r in rules().get("payee_rules", []):
        if not any(k in r for k in PREDICATES):
            print(f"; warning: rules.toml payee_rule with no predicate ignored: {r}",
                  file=sys.stderr)
            continue
        if "ofx_type" in r and str(r["ofx_type"]).upper() != ttype:
            continue
        if "match" in r and not re.search(r["match"], text, re.I):
            continue
        if "min_amount" in r and mag < float(r["min_amount"]):
            continue
        if "max_amount" in r and mag > float(r["max_amount"]):
            continue
        return r["account"]
    return None


def categorize(payee, ofx_type="", amount=0.0, primary_account=""):
    """Return the counter-account for a transaction. First matching rule wins.

    `amount` is signed from the account's point of view (deposit/refund > 0,
    charge/debit < 0). Fallbacks when nothing matches: a debit is
    Expenses:Uncategorized (the review queue); a credit to a CARD (a
    Liabilities account) is a refund that still needs its merchant's category,
    so it stays Uncategorized too; a credit anywhere else is Income:US:Other.
    """
    rule = match_rule(payee, ofx_type, amount)
    if rule:
        return rule
    if amount > 0 and not primary_account.startswith("Liabilities"):
        return INCOME_DEFAULT
    return EXPENSE_DEFAULT


def chase_category(chase_cat):
    """Map a Chase CSV 'Category' cell to an account; None if unmapped."""
    return rules().get("chase_categories", {}).get((chase_cat or "").strip())


def entry_by_acctid(acctid):
    """The full [[accounts]] entry for an OFX <ACCTID>, matched on trailing
    digits (last4) — None if no entry matches. inbox.py reads institution
    and owner off the entry to build a documents/ filing path."""
    tail = re.sub(r"\D", "", acctid or "")
    for a in rules().get("accounts", []):
        last4 = re.sub(r"\D", "", str(a.get("last4", "")))
        if last4 and tail.endswith(last4):
            return a
    return None


def route_by_acctid(acctid):
    """Ledger account for an OFX <ACCTID>, matched on trailing digits (last4).

    Returns None if no [[accounts]] entry matches — caller must then take the
    account from the command line.
    """
    entry = entry_by_acctid(acctid)
    return entry["ledger_account"] if entry else None
