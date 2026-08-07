"""Locate the household vault and expose its paths and rules.toml config.

Every sara module gets vault knowledge from here — one place that knows
where the vault is ($FINANCE_VAULT, then the ~/.finance-vault pointer,
then ~/Finance) and how its configuration is shaped. Secrets (Plaid keys,
sync cursors) live under $VAULT/.secrets/, owner-read-only and gitignored.
"""

from __future__ import annotations

import csv
import io
import os
import re
import stat
import subprocess
import tomllib
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sara.typed import as_dicts, as_list


def _resolve_vault() -> Path:
    """$FINANCE_VAULT wins; else the ~/.finance-vault pointer file (written by
    init_vault.sh for custom locations, so the path survives shell-profile
    loss); else ~/Finance."""
    env = os.environ.get("FINANCE_VAULT")
    if env:
        return Path(env).expanduser().resolve()
    pointer = Path.home() / ".finance-vault"
    if pointer.is_file():
        lines = pointer.read_text().splitlines()
        target = Path(lines[0].strip()).expanduser() if lines and lines[0].strip() else None
        if target is not None and _trusted_vault_dir(target):
            return target
    return Path.home() / "Finance"


def _trusted_vault_dir(target: Path) -> bool:
    """Trust a pointer target only if it is a vault THIS user owns and nobody
    else can rewrite — the same test tools/run applies in bash (guards a
    planted or hijacked ~/.finance-vault)."""
    try:
        st = target.stat()
    except OSError:
        return False
    return (target.is_dir()
            and st.st_uid == os.getuid()
            and not (stat.S_IMODE(st.st_mode) & stat.S_IWOTH)
            and (target / "ledger" / "main.beancount").is_file())


VAULT = _resolve_vault()
LEDGER_DIR = VAULT / "ledger"
LEDGER = LEDGER_DIR / "main.beancount"
FACTS = VAULT / "facts"
REPORTS = VAULT / "reports"
RULES_FILE = VAULT / "rules.toml"
SECRETS_DIR = VAULT / ".secrets"
PLAID_ENV_FILE = SECRETS_DIR / "plaid.env"
PLAID_CURSORS_FILE = SECRETS_DIR / "plaid-cursors.json"
BEAN_QUERY = VAULT / ".venv" / "bin" / "bean-query"
BEAN_CHECK = VAULT / ".venv" / "bin" / "bean-check"

Rules = dict[str, Any]


def require_vault() -> None:
    if not LEDGER.exists():
        raise SystemExit(
            f"Vault not found at {VAULT} (no ledger/main.beancount). "
            "Set FINANCE_VAULT to the vault path, or run scripts/init_vault.sh.")


def rules() -> Rules:
    """The parsed rules.toml (empty dict if the vault has none yet).

    Read fresh on every call — a sub-millisecond parse — so a long-lived
    server can never serve stale config. No cache, nothing to reset.
    """
    return tomllib.loads(RULES_FILE.read_text()) if RULES_FILE.exists() else {}


def household(key: str, default: Any = None) -> Any:
    house: dict[str, Any] = rules().get("household") or {}
    return house.get(key, default)


def shadow_currency() -> str:
    return str(household("shadow_currency", "USD.EQ"))


def illiquid_currency_regex() -> str | None:
    """Regex matching illiquid commodity symbols, or None if none configured."""
    raw: object = household("illiquid_commodity_prefixes", [])
    prefixes: list[str] = [str(x) for x in as_list(raw)]
    if not prefixes:
        return None
    return "^(" + "|".join(re.escape(p) for p in prefixes) + ")"


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


# ------------------------------------------------------------------ ledger
def query(sql: str) -> list[dict[str, str]]:
    """Run a bean-query statement, return rows as dicts."""
    require_vault()
    if not BEAN_QUERY.exists():
        raise SystemExit(f"bean-query not found at {BEAN_QUERY} — is the vault venv set up?")
    out = subprocess.run([str(BEAN_QUERY), "-f", "csv", str(LEDGER), sql],
                         capture_output=True, text=True, cwd=VAULT, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"bean-query failed:\n{out.stderr.strip()}\n  query: {sql}")
    return list(csv.DictReader(io.StringIO(out.stdout)))


_TAGGED_NUM = r"(-?\d[\d,]*(?:\.\d+)?)"


def amount(cell: str | None, currency: str = "USD") -> float:
    """Pull the numeric value out of a bean-query cell.

    '1,234.56 USD' -> 1234.56. A cell can hold a multi-currency inventory
    rendered as aligned columns, so prefer the number tagged with `currency`.
    A bare number (count(*), sum(number)) passes through unchanged. A cell
    holding ONLY other commodities ('12.000 VTSAX') returns 0.0 for
    `currency` math — those are UNITS, never a dollar figure.
    """
    cell = cell or ""
    m = re.search(_TAGGED_NUM + rf"\s+{re.escape(currency)}(?![.\w])", cell)
    if m:
        return float(m.group(1).replace(",", ""))
    if re.search(r"-?\d[\d,.]*\s*[A-Z]", cell):
        return 0.0
    m = re.search(_TAGGED_NUM, cell)
    return float(m.group(1).replace(",", "")) if m else 0.0


def money(x: float) -> str:
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


# ------------------------------------------------------------------ owners
# Each account's `open` directive may carry `owner: "<name>"` metadata —
# household-chosen lowercase names (matching facts/people/ files) plus the
# shared name "joint". Accounts without it roll up under OWNER_UNASSIGNED.
OWNER_JOINT = "joint"
OWNER_UNASSIGNED = "unassigned"
OWNER_TRANSIT = "transit"


def account_owners() -> dict[str, str]:
    """account -> owner from `owner:` metadata on open directives.

    Reads the #accounts table so zero-posting accounts still map. Fresh on
    every call (one bean-query); empty when the ledger declares no owners.
    """
    rows = query("SELECT account, getitem(open_meta(account), 'owner') AS owner "
                 "FROM #accounts")
    return {r["account"]: owner for r in rows
            if (owner := (r.get("owner") or "").strip().lower())}


def owner_label(owner: str | None) -> str | None:
    """Display form of an owner name: 'alex' -> 'Alex' (None passes through)."""
    if owner == OWNER_TRANSIT:
        return "in transit (between your own accounts)"
    return owner[:1].upper() + owner[1:] if owner else None


# ------------------------------------------------------------------- facts
DATED_BULLET = re.compile(r"^- (\d{4}-\d{2}-\d{2}) — (.+)$", re.M)


def dated_bullets() -> list[tuple[date, str, Path]]:
    """Every `- YYYY-MM-DD — text` bullet across facts/, as (date, text, relpath)."""
    out: list[tuple[date, str, Path]] = []
    if not FACTS.exists():
        return out
    for f in sorted(FACTS.rglob("*.md")):
        try:
            txt = f.read_text()
        except OSError:
            continue
        for m in DATED_BULLET.finditer(txt):
            try:
                d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            out.append((d, m.group(2).strip(), f.relative_to(VAULT)))
    return sorted(out)
