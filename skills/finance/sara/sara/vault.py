"""Locate the household vault and expose its paths and rules.toml config.

Every sara module gets vault knowledge from here — one place that knows
where the vault is ($FINANCE_VAULT, then the ~/.finance-vault pointer,
then ~/Finance) and how its configuration is shaped. Secrets (Plaid keys,
sync cursors) live under $VAULT/.secrets/, owner-read-only and gitignored.
"""

from __future__ import annotations

import os
import re
import stat
import tomllib
from pathlib import Path
from typing import Any

from sara.typed import as_dicts


def _resolve_vault() -> Path:
    """$FINANCE_VAULT wins; else the ~/.finance-vault pointer file (written by
    init_vault.sh for custom locations, so the path survives shell-profile
    loss); else ~/Finance."""
    env = os.environ.get("FINANCE_VAULT")
    if env:
        return Path(env).expanduser()
    pointer = Path.home() / ".finance-vault"
    if pointer.is_file():
        target = pointer.read_text().strip()
        if target and (Path(target).expanduser() / "ledger").is_dir():
            return Path(target).expanduser()
    return Path.home() / "Finance"


VAULT = _resolve_vault()
LEDGER_DIR = VAULT / "ledger"
LEDGER = LEDGER_DIR / "main.beancount"
RULES_FILE = VAULT / "rules.toml"
SECRETS_DIR = VAULT / ".secrets"
PLAID_ENV_FILE = SECRETS_DIR / "plaid.env"
PLAID_CURSORS_FILE = SECRETS_DIR / "plaid-cursors.json"
BEAN_QUERY = VAULT / ".venv" / "bin" / "bean-query"
BEAN_CHECK = VAULT / ".venv" / "bin" / "bean-check"

Rules = dict[str, Any]
_rules_cache: Rules | None = None


def require_vault() -> None:
    if not LEDGER.exists():
        raise SystemExit(
            f"Vault not found at {VAULT} (no ledger/main.beancount). "
            "Set FINANCE_VAULT to the vault path, or run scripts/init_vault.sh.")


def rules() -> Rules:
    """The parsed rules.toml (empty dict if the vault has none yet)."""
    global _rules_cache
    if _rules_cache is None:
        _rules_cache = tomllib.loads(RULES_FILE.read_text()) if RULES_FILE.exists() else {}
    return _rules_cache


def account_entries() -> list[dict[str, Any]]:
    """The [[accounts]] routing table, entries that name a ledger_account."""
    return [a for a in as_dicts(rules().get("accounts")) if a.get("ledger_account")]


def routing_help() -> str:
    """The configured [[accounts]] routing table, one `last4 -> ledger_account`
    line per entry, as `;`-comment text. Printed beside any ACCTID routing
    miss so a typo'd or missing rules.toml entry is instantly visible next to
    what IS configured — the fix is usually one glance away."""
    routes = [(re.sub(r"\D", "", str(a.get("last4", ""))) or "????",
               str(a["ledger_account"]))
              for a in account_entries()]
    if not routes:
        return ";   rules.toml has no [[accounts]] entries yet"
    return "\n".join([";   configured [[accounts]] routing (last4 -> ledger_account):"]
                     + [f";     {l4} -> {acct}" for l4, acct in routes])


# ---------------------------------------------------------------- secrets
def ensure_secrets_dir() -> Path:
    """Create $VAULT/.secrets (0700) if missing and return it."""
    SECRETS_DIR.mkdir(mode=0o700, exist_ok=True)
    return SECRETS_DIR


def write_secret_file(path: Path, text: str) -> None:
    """Write a secrets file owner-read-only (0600), creating .secrets/ 0700."""
    ensure_secrets_dir()
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(text)


def load_env_file(path: Path) -> dict[str, str]:
    """Parse a KEY=value env file (comments and blank lines ignored)."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip("'\"")
    return out


def check_secret_permissions(path: Path) -> str | None:
    """A warning string when a secrets file is readable beyond its owner."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None
    if mode & 0o077:
        return (f"{path} is mode {mode:o} — it holds credentials and should be 0600 "
                f"(fix: chmod 600 {path})")
    return None
