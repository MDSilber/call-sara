"""The three write actions — each a thin door onto existing gated machinery.

categorize  appends a [[payee_rules]] entry to rules.toml (validated, TOML-
            escaped, parse-verified with rollback), then runs the stock
            recategorize tool — atomic writes, bean-check, full rollback on
            a rejected rewrite. Nothing new touches the ledger.
set-goal    edits one allowlisted numeric/boolean key inside the yaml block
            of facts/goals/index.md, atomically, and re-reads it to confirm.
dismiss     records "quiet until <date>" for one finding in
            reports/dismissals.json; every needs-you surface reads it.
"""
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import date, datetime, timedelta
from typing import cast

from checks import goals as goals_config
from dismissals import DISMISSALS_FILE, load_dismissals
from importers.common import atomic_write
from vault import (
    RULES_FILE,
    VAULT,
    query,  # pyright: ignore[reportUnknownVariableType] — typed by _query below
    reset_rules_cache,
)

from . import TOOLS_DIR


def _query(sql: str) -> list[dict[str, str]]:
    """vault.query with its true type made explicit (the tools are untyped)."""
    return cast(list[dict[str, str]], query(sql))


def _goals() -> dict[str, object]:
    return cast(dict[str, object], goals_config())


def _dismissal_entries() -> dict[str, dict[str, object]]:
    return cast(dict[str, dict[str, object]], load_dismissals())

MAX_PATTERN_LEN = 120
MAX_TITLE_LEN = 200
MAX_DISMISS_DAYS = 366
GOAL_LIMITS = {"education_target": 100_000_000.0,
               "retirement_target": 1_000_000_000.0}
_RECAT_TOTAL = re.compile(r"(?:rewrote (\d+) postings|(\d+) postings would change)")


class ActionError(ValueError):
    """A refused action — the message is safe to show the user."""


def _open_accounts() -> set[str]:
    # the #accounts table lists every OPENED account, zero-posting ones
    # included — the teach picker (DB accounts dim) shows those too
    rows = _query("SELECT account FROM #accounts")
    return {r["account"] for r in rows}


# -------------------------------------------------------------- categorize
def categorize(payee_pattern: str, account: str,
               apply_history: bool) -> dict[str, object]:
    pattern = (payee_pattern or "").strip()
    if not pattern:
        raise ActionError("the payee pattern is empty")
    if len(pattern) > MAX_PATTERN_LEN:
        raise ActionError(f"pattern longer than {MAX_PATTERN_LEN} chars")
    try:
        re.compile(pattern)
    except re.error as e:
        raise ActionError(f"not a valid regex: {e}") from e
    account = (account or "").strip()
    if not account.startswith(("Expenses:", "Income:")):
        raise ActionError("rules may only point at Expenses:* or Income:* accounts")
    if account in ("Expenses:Uncategorized", "Expenses:FIXME"):
        raise ActionError("that IS the review bucket — pick a real category")
    if account not in _open_accounts():
        raise ActionError(f"no open ledger account named {account}")

    stamp = date.today().isoformat()
    block = (f"\n[[payee_rules]]\n"
             f"# taught in Sara App {stamp}\n"
             f"match = {json.dumps(pattern)}\n"
             f"account = {json.dumps(account)}\n")
    original = RULES_FILE.read_text() if RULES_FILE.exists() else ""
    new_text = original + ("" if original.endswith("\n") or not original
                           else "\n") + block
    try:
        tomllib.loads(new_text)
    except tomllib.TOMLDecodeError as e:
        raise ActionError(f"refusing to write rules.toml — the result would "
                          f"not parse: {e}") from e
    atomic_write(RULES_FILE, new_text)
    reset_rules_cache()  # the running process must see the new rule

    argv = [sys.executable, str(TOOLS_DIR / "recategorize.py")]
    if apply_history:
        argv.append("--write")
    proc = subprocess.run(argv, capture_output=True, text=True,
                          env=_env_with_vault(), cwd=str(VAULT))
    if proc.returncode != 0:
        # the rule stays taught (future imports use it); history rewrite failed
        detail = (proc.stderr or proc.stdout).strip()
        raise ActionError(f"rule saved, but recategorize refused: {detail}")
    m = _RECAT_TOTAL.search(proc.stdout)
    changed = int(m.group(1) or m.group(2)) if m else 0
    return {"rule": {"match": pattern, "account": account},
            "applied": apply_history, "changed": changed,
            "report": proc.stdout.strip()}


def _env_with_vault() -> dict[str, str]:
    env = dict(os.environ)
    env["FINANCE_VAULT"] = str(VAULT)
    return env


# ---------------------------------------------------------------- set-goal
GOALS_FILE_TEMPLATE = """---
type: goals
date: {today}
---
# Goals & thresholds (machine-read by tools/checks.py)

```yaml
{line}
```
"""


def set_goal(key: str, value: object) -> dict[str, object]:
    if key == "show_walkaway":
        if not isinstance(value, bool):
            raise ActionError("show_walkaway takes true or false")
        rendered = "true" if value else "false"
    elif key in GOAL_LIMITS:
        try:
            num = float(cast(float, value))
        except (TypeError, ValueError) as e:
            raise ActionError(f"{key} needs a number") from e
        if not (0 <= num <= GOAL_LIMITS[key]):
            raise ActionError(f"{key} must sit between 0 and "
                              f"{GOAL_LIMITS[key]:,.0f}")
        rendered = f"{num:.0f}" if num == int(num) else f"{num}"
    else:
        raise ActionError(f"'{key}' is not an app-editable goal "
                          f"(editable: education_target, retirement_target, "
                          f"show_walkaway)")

    previous = _goals().get(key)
    path = VAULT / "facts" / "goals" / "index.md"
    line = f"{key}: {rendered}"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, GOALS_FILE_TEMPLATE.format(
            today=date.today().isoformat(), line=line))
    else:
        text = path.read_text()
        block = re.search(r"```yaml\n(.*?)```", text, re.S)
        if not block:
            atomic_write(path, text.rstrip() + "\n\n```yaml\n" + line + "\n```\n")
        else:
            body = block.group(1)
            key_line = re.compile(rf"^({re.escape(key)}\s*:\s*)([^#\n]*?)(\s*#.*)?$",
                                  re.M)
            if key_line.search(body):
                new_body = key_line.sub(
                    lambda m: f"{m.group(1)}{rendered}{m.group(3) or ''}",
                    body, count=1)
            else:
                new_body = body.rstrip("\n") + f"\n{line}\n"
            atomic_write(path, text[:block.start(1)] + new_body
                         + text[block.end(1):])
    now_read = _goals().get(key)
    if key == "show_walkaway":
        ok = str(now_read).strip().lower() == rendered
    else:
        ok = (isinstance(now_read, float)
              and abs(now_read - float(rendered)) < 0.5)
    if not ok:
        raise ActionError(f"wrote {key} but re-reading facts/goals returned "
                          f"{now_read!r} — check the file by hand")
    return {"key": key, "value": now_read, "previous": previous}


# ----------------------------------------------------------------- dismiss
def dismiss(finding_id: str, until: str | None,
            title: str = "") -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{12}", finding_id or ""):
        raise ActionError("bad finding id")
    today = date.today()
    entries = _dismissal_entries()
    if until is None:
        removed = entries.pop(finding_id, None)
        _write_dismissals(entries, today)
        return {"id": finding_id, "until": None,
                "removed": removed is not None}
    try:
        until_d = date.fromisoformat(until)
    except ValueError as e:
        raise ActionError("until must be a YYYY-MM-DD date") from e
    if not (today <= until_d <= today + timedelta(days=MAX_DISMISS_DAYS)):
        raise ActionError(f"until must fall within the next "
                          f"{MAX_DISMISS_DAYS} days")
    entries[finding_id] = {"until": until_d.isoformat(),
                           "title": (title or "")[:MAX_TITLE_LEN],
                           "at": datetime.now().isoformat(timespec="seconds")}
    _write_dismissals(entries, today)
    return {"id": finding_id, "until": until_d.isoformat(), "removed": False}


def _write_dismissals(entries: dict[str, dict[str, object]],
                      today: date) -> None:
    def _alive(entry: dict[str, object]) -> bool:
        try:
            return date.fromisoformat(str(entry.get("until"))) >= today
        except ValueError:
            return False

    kept = {fid: e for fid, e in entries.items() if _alive(e)}
    DISMISSALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(DISMISSALS_FILE,
                 json.dumps({"version": 1, "dismissed": kept}, indent=1,
                            sort_keys=True) + "\n")
