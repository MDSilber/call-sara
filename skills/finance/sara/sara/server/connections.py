"""The Connections surface: every Plaid item's health, and its three doors.

Reads: rules.toml ``[sources.plaid.items]`` (the routing config), the sync
cursor stamps in ``.secrets/plaid-cursors.json``, plaid.env token presence,
and the ``plaid_freshness`` findings — all files, never the network.

Doors (each one whitelisted, alias-validated, and riding the existing gated
machinery — nothing new touches Plaid or the ledger):

- sync      ``python -m sara.ingest --item <alias> --write`` streamed line by
            line; the pipeline's own verification report IS the stream, and
            its regenerate step refreshes the snapshot/DB for the watchers.
- repair    a link_token minted in UPDATE mode via sara.link's machinery —
            the browser finishes Link on the Connections page; no slot spent,
            no token exchange needed (the access token is unchanged).
- disable   the item's rules.toml block commented out with a dated note.
            NEVER ``/item/remove``: Plaid Trial slots are lifetime, so the
            token line stays in plaid.env and the slot is preserved.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from sara.link import slots_used, token_var
from sara.plaid_api import PlaidCreds, api_error_summary, create_link_token, make_client
from sara.typed import as_dict, as_list
from sara.vault import (
    PLAID_CURSORS_FILE,
    PLAID_ENV_FILE,
    RULES_FILE,
    VAULT,
    load_env_file,
    rules,
)

from .actions import ActionError

LIFETIME_ITEMS = 10
WATCH_DAYS = 3   # matches tools/checks.py plaid_freshness
ALERT_DAYS = 7
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _digits_tail(ledger_account: str) -> str:
    """'Assets:US:Chase:Checking4321' -> '4321' ('' when the leaf has none)."""
    digits = re.sub(r"\D", "", ledger_account.rsplit(":", 1)[-1])
    return digits[-4:]


def _items() -> dict[str, dict[str, Any]]:
    sources = as_dict(rules().get("sources"))
    plaid = as_dict(sources.get("plaid"))
    return {alias: as_dict(cfg) for alias, cfg in as_dict(plaid.get("items")).items()}


def _cursor_stamps() -> dict[str, dict[str, Any]]:
    if not PLAID_CURSORS_FILE.is_file():
        return {}
    try:
        parsed: object = json.loads(PLAID_CURSORS_FILE.read_text())
    except ValueError:
        return {}
    items: object = as_dict(parsed).get("items")
    return {str(k): as_dict(v) for k, v in as_dict(items).items()}


def require_alias(alias: str) -> dict[str, Any]:
    """The configured item for `alias`, or a refusal — every door validates
    through here so no request-supplied string reaches a subprocess or file."""
    if not _ALIAS_RE.fullmatch(alias or ""):
        raise ActionError("bad item alias")
    cfg = _items().get(alias)
    if cfg is None:
        raise ActionError(f"no [sources.plaid.items.{alias}] in rules.toml")
    return cfg


def payload(today: date | None = None) -> dict[str, object]:
    today = today or date.today()
    items = _items()
    stamps = _cursor_stamps()
    env = load_env_file(PLAID_ENV_FILE)
    used = slots_used(env)
    out: list[dict[str, object]] = []
    for alias in sorted(items):
        cfg = items[alias]
        var_name = str(cfg.get("access_token_env") or token_var(alias))
        accounts = [{"tail": _digits_tail(str(acct)),
                     "ledger_account": str(acct)}
                    for acct in as_dict(cfg.get("accounts")).values()]
        stamp = stamps.get(alias, {})
        synced_raw = str(stamp.get("last_synced") or "")
        status, silent_days, synced_lbl = "never", None, None
        if synced_raw:
            try:
                synced = datetime.fromisoformat(synced_raw)
                silent_days = (today - synced.date()).days
                synced_lbl = synced.strftime("%b %-d, %H:%M")
                status = ("fresh" if silent_days <= WATCH_DAYS
                          else "stale" if silent_days <= ALERT_DAYS else "dead")
            except ValueError:
                pass
        if var_name not in env:
            status = "no-token"
        products = [str(p) for p in as_list(cfg.get("products"))]
        out.append({
            "alias": alias,
            "products": products or ["transactions"],
            "token_var": var_name,
            "token_present": var_name in env,
            "accounts": accounts,
            "status": status,
            "last_synced": synced_raw or None,
            "last_synced_lbl": synced_lbl,
            "silent_days": silent_days,
        })
    return {
        "configured": bool(items),
        "items": out,
        "slots": {
            "used": used,
            "total": LIFETIME_ITEMS,
            "line": (f"Only linking a brand-new bank uses one of the "
                     f"{LIFETIME_ITEMS} lifetime links — {used} used, "
                     f"{LIFETIME_ITEMS - used} left. Repairs are free."),
        },
        "keys_present": bool(env.get("PLAID_CLIENT_ID") and env.get("PLAID_SECRET")),
        "fixture": bool(os.environ.get("SARA_PLAID_FIXTURE")),
    }


# ----------------------------------------------------------------- sync now
def sync_stream(alias: str) -> Iterator[str]:
    """Run the gated ingest for one item, --write, yielding its report."""
    require_alias(alias)
    argv = [sys.executable, "-m", "sara.ingest", "--item", alias, "--write"]
    yield f"$ sara.ingest --item {alias} --write\n"
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1, cwd=str(VAULT),
        env={**os.environ, "FINANCE_VAULT": str(VAULT), "PYTHONUNBUFFERED": "1"})
    assert proc.stdout is not None
    yield from proc.stdout
    code = proc.wait()
    yield ("\n✓ sync complete — reports regenerated, numbers refresh on their own\n"
           if code == 0 else f"\n✗ sync exited {code} — nothing was written\n")


# ------------------------------------------------------------------ repair
def link_update_token(alias: str) -> dict[str, object]:
    """Mint a Link UPDATE-mode token for an existing item (free repair)."""
    cfg = require_alias(alias)
    env = load_env_file(PLAID_ENV_FILE)
    if not env.get("PLAID_CLIENT_ID") or not env.get("PLAID_SECRET"):
        raise ActionError("no Plaid keys in .secrets/plaid.env — link from a "
                          "terminal first (python -m sara.link)")
    var_name = str(cfg.get("access_token_env") or token_var(alias))
    access_token = env.get(var_name, "")
    if not access_token:
        raise ActionError(f"{var_name} is not in plaid.env — nothing to repair; "
                          f"link it first: python -m sara.link {alias}")
    creds = PlaidCreds(client_id=env["PLAID_CLIENT_ID"], secret=env["PLAID_SECRET"],
                       environment=env.get("PLAID_ENV", "production"))
    try:
        token = create_link_token(
            make_client(creds),
            client_name="Call Sara (personal finance vault)",
            user_id=f"sara-{VAULT.name}", products=[],
            redirect_uri=env.get("PLAID_REDIRECT_URI", ""),
            access_token=access_token)
    except Exception as e:  # plaid-python raises generated exception types
        raise ActionError(f"link_token creation failed — {api_error_summary(e)}") from e
    return {"alias": alias, "link_token": token, "mode": "update"}


# ----------------------------------------------------------------- disable
def disable(alias: str) -> dict[str, object]:
    """Comment the item's rules.toml block out (with a dated note), verified
    by re-parsing. The plaid.env token line stays — the slot is preserved."""
    require_alias(alias)
    original = RULES_FILE.read_text()
    lines = original.splitlines(keepends=True)
    header = f"[sources.plaid.items.{alias}]"
    sub_prefix = f"[sources.plaid.items.{alias}."
    out: list[str] = []
    in_block = False
    touched = 0
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_block = stripped.startswith((header, sub_prefix))
            if in_block and stripped.startswith(header):
                out.append(f"# disabled in Sara App {date.today().isoformat()} — "
                           f"slot preserved; uncomment to re-enable (repair is free)\n")
        if in_block and stripped and not stripped.startswith("#"):
            out.append("# " + line)
            touched += 1
        else:
            out.append(line)
    if not touched:
        raise ActionError(f"nothing to disable — [sources.plaid.items.{alias}] "
                          f"has no active lines")
    new_text = "".join(out)
    try:
        parsed = tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        raise ActionError(f"refusing to write rules.toml — the result would not "
                          f"parse: {e}") from e
    still = as_dict(as_dict(as_dict(parsed.get("sources")).get("plaid")).get("items"))
    if alias in still:
        raise ActionError(f"refusing to write rules.toml — {alias} would still "
                          f"be active after the edit")
    from importers.common import atomic_write  # tools module, path set by __init__
    atomic_write(RULES_FILE, new_text)  # rules() reads fresh — nothing to reset
    return {"alias": alias, "disabled": True, "lines_commented": touched,
            "note": "config commented out; the Plaid slot and token are preserved"}
