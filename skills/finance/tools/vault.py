"""Re-export shim: the one vault resolver lives in sara.vault.

Kept so `from vault import ...` in tools/ entrypoints keeps working while
the advisor layer migrates into the sara package.
"""
from sara.vault import (  # noqa: F401
    BEAN_CHECK,
    BEAN_QUERY,
    DATED_BULLET,
    FACTS,
    LEDGER,
    OWNER_JOINT,
    OWNER_TRANSIT,
    OWNER_UNASSIGNED,
    REPORTS,
    RULES_FILE,
    VAULT,
    account_owners,
    amount,
    dated_bullets,
    household,
    illiquid_currency_regex,
    money,
    owner_label,
    query,
    require_vault,
    rules,
    shadow_currency,
)
