"""Locate the household vault and expose its paths, config, and query runner.

Every tool imports from here — one place that knows where the vault is
($FINANCE_VAULT, default ~/Finance) and how to talk to it.
"""
import csv
import io
import os
import re
import subprocess
import tomllib
from pathlib import Path

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
LEDGER = VAULT / "ledger" / "main.beancount"
FACTS = VAULT / "facts"
REPORTS = VAULT / "reports"
RULES_FILE = VAULT / "rules.toml"
BEAN_QUERY = VAULT / ".venv" / "bin" / "bean-query"
BEAN_CHECK = VAULT / ".venv" / "bin" / "bean-check"


def require_vault():
    if not LEDGER.exists():
        raise SystemExit(
            f"Vault not found at {VAULT} (no ledger/main.beancount). "
            "Set FINANCE_VAULT to the vault path, or run scripts/init_vault.sh.")


# ------------------------------------------------------------------ config
_rules_cache = None


def rules():
    """The parsed rules.toml (empty dict if the vault has none yet)."""
    global _rules_cache
    if _rules_cache is None:
        _rules_cache = tomllib.loads(RULES_FILE.read_text()) if RULES_FILE.exists() else {}
    return _rules_cache


def household(key, default=None):
    return rules().get("household", {}).get(key, default)


def shadow_currency():
    return household("shadow_currency", "USD.EQ")


def illiquid_currency_regex():
    """Regex matching illiquid commodity symbols, or None if none configured."""
    prefixes = household("illiquid_commodity_prefixes", []) or []
    if not prefixes:
        return None
    return "^(" + "|".join(re.escape(p) for p in prefixes) + ")"


# ------------------------------------------------------------------ ledger
def query(sql):
    """Run a bean-query statement, return rows as dicts."""
    require_vault()
    if not BEAN_QUERY.exists():
        raise SystemExit(f"bean-query not found at {BEAN_QUERY} — is the vault venv set up?")
    out = subprocess.run([str(BEAN_QUERY), "-f", "csv", str(LEDGER), sql],
                         capture_output=True, text=True, cwd=VAULT)
    if out.returncode != 0:
        raise RuntimeError(f"bean-query failed:\n{out.stderr.strip()}\n  query: {sql}")
    return list(csv.DictReader(io.StringIO(out.stdout)))


def amount(cell, currency="USD"):
    """Pull the numeric value out of a bean-query cell.

    '1,234.56 USD' -> 1234.56. A cell can hold a multi-currency inventory
    rendered as aligned columns (' , , 1,234.56 USD'), so prefer the number
    tagged with `currency`. A bare number (count(*), sum(number)) passes
    through unchanged. A cell holding ONLY other commodities ('12.000
    VTSAX') returns 0.0 for `currency` math — those are UNITS, and reading
    them as dollars is how an unpriced holding used to leak into USD sums
    (callers that want the shadow currency pass it explicitly).
    """
    cell = cell or ""
    m = re.search(rf"(-?\d[\d,]*(?:\.\d+)?)\s+{re.escape(currency)}(?![.\w])", cell)
    if m:
        return float(m.group(1).replace(",", ""))
    if re.search(r"-?\d[\d,.]*\s*[A-Z]", cell):
        return 0.0  # some commodity, none of it `currency` — never a dollar figure
    m = re.search(r"(-?\d[\d,]*(?:\.\d+)?)", cell)
    return float(m.group(1).replace(",", "")) if m else 0.0


def money(x):
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"


# ------------------------------------------------------------------ owners
# The household-lens convention: each account's `open` directive may carry
# `owner: "<name>"` metadata — household-chosen lowercase names (matching
# facts/people/ files) plus the shared name "joint". Accounts without the
# metadata roll up under OWNER_UNASSIGNED. See references/querying.md.
OWNER_JOINT = "joint"
OWNER_UNASSIGNED = "unassigned"
_owners_cache = None


def account_owners():
    """account -> owner from `owner:` metadata on open directives.

    Reads the #accounts table so zero-posting accounts still map. Names are
    household-authored DATA (lowercased, stripped) — never interpreted.
    Empty dict when the ledger declares no owners (the pre-owner vault
    shape); every owner surface stays dormant then.
    """
    global _owners_cache
    if _owners_cache is None:
        rows = query("SELECT account, getitem(open_meta(account), 'owner') AS owner "
                     "FROM #accounts")
        _owners_cache = {r["account"]: owner for r in rows
                         if (owner := (r["owner"] or "").strip().lower())}
    return _owners_cache


OWNER_TRANSIT = "transit"


def owner_label(owner):
    """Display form of an owner name: 'alex' -> 'Alex' (None passes through)."""
    if owner == OWNER_TRANSIT:
        return "in transit (between your own accounts)"
    return owner[:1].upper() + owner[1:] if owner else None


# ------------------------------------------------------------------- facts
DATED_BULLET = re.compile(r"^- (\d{4}-\d{2}-\d{2}) — (.+)$", re.M)


def dated_bullets():
    """Every `- YYYY-MM-DD — text` bullet across facts/, as (date, text, relpath)."""
    from datetime import datetime
    out = []
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


def reset_rules_cache():
    """Forget the parsed rules.toml — call after writing it so a long-lived
    process (Sara App's server) sees the new rules without a restart."""
    global _rules_cache
    _rules_cache = None
