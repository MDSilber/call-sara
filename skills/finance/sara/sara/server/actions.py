"""The three write actions — each a thin door onto existing gated machinery.

categorize  appends a [[payee_rules]] entry to rules.toml (validated, TOML-
            escaped, parse-verified with rollback), then runs the stock
            recategorize tool — atomic writes, bean-check, full rollback on
            a rejected rewrite. With `new_account` the target category is
            first OPENED: a dated `open` directive lands in the chart file
            through the same gated ledger writer (atomic, bean-check,
            rollback) before the rule is taught — so "what if it doesn't
            match one of these?" is answered without leaving the popover.
set-goal    edits one allowlisted numeric key inside the yaml block of
            facts/goals/index.md, atomically, and re-reads it to confirm.
dismiss     records "quiet until <date>" for one finding in
            reports/dismissals.json; every needs-you surface reads it.

All three are read-modify-write flows over small files, so one process-wide
lock serializes them, and every write lands via its own mkstemp temp file +
os.replace — two racing requests can neither interleave nor clobber.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast

import sara.vault
from checks import goals as goals_config
from dismissals import DISMISSALS_FILE, load_dismissals
from sara.ledger.queries import opened_accounts
from sara.ledger.writer import rewrite_ledger_files
from vault import (
    RULES_FILE,
    VAULT,
    query,  # pyright: ignore[reportUnknownVariableType] — typed by _query below
    reset_rules_cache,
)

from . import TOOLS_DIR

# One writer at a time: every action is a read-modify-write over a small
# file, and the lock is what makes the pair of requests last-writer-wins
# instead of lost-update. (Unique temp files alone would still interleave.)
_WRITE_LOCK = threading.Lock()


def _atomic_write(path: Path, text: str) -> None:
    """Durable atomic replace with a UNIQUE temp file (mkstemp in the
    destination directory), so concurrent writers can never truncate each
    other's in-flight temp — the server-side twin of the tools helper."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent),
                                    prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
GOAL_LIMITS = {"education_target": 100_000_000.0}
_RECAT_TOTAL = re.compile(r"(?:rewrote (\d+) postings|(\d+) postings would change)")


class ActionError(ValueError):
    """A refused action — the message is safe to show the user."""


def _open_accounts() -> set[str]:
    # the #accounts table lists every OPENED account, zero-posting ones
    # included — the teach picker (DB accounts dim) shows those too
    rows = _query("SELECT account FROM #accounts")
    return {r["account"] for r in rows}


# -------------------------------------------------------------- categorize
# Beancount's account grammar, narrowed to the two roots a rule may target:
# colon-separated segments, each starting with a capital letter or digit.
NEW_ACCOUNT_RE = re.compile(r"^(?:Expenses|Income)(?::[A-Z0-9][A-Za-z0-9-]*)+$")
REVIEW_BUCKETS = ("Expenses:Uncategorized", "Expenses:FIXME")


def _chart_file() -> Path:
    """The ledger file that holds the Expenses/Income chart — the one with
    the most such `open` directives (accounts.beancount in the template),
    falling back to main.beancount."""
    opens = re.compile(r"^\s*\d{4}-\d{2}-\d{2}\s+open\s+(?:Expenses|Income):", re.M)
    best, best_n = VAULT / "ledger" / "main.beancount", 0
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            n = len(opens.findall(f.read_text()))
        except OSError:
            continue
        if n > best_n:
            best, best_n = f, n
    return best


def _chart_epoch() -> date:
    """The earliest `open` date anywhere in the ledger. A new category must
    be open from the chart's own beginning — the whole point of teaching it
    is rewriting HISTORY into it, and beancount refuses postings that
    predate their account."""
    dates: list[date] = []
    for f in (VAULT / "ledger").glob("*.beancount"):
        try:
            text = f.read_text()
        except OSError:
            continue
        for m in re.finditer(r"^\s*(\d{4}-\d{2}-\d{2})\s+open\s", text, re.M):
            try:
                dates.append(date.fromisoformat(m.group(1)))
            except ValueError:
                continue
    return min(dates, default=date.today())


def _open_new_account(account: str) -> bool:
    """Append a dated `open` for `account` to the chart file through the
    gated ledger writer (atomic tmp+rename, bean-check, rollback). Returns
    False when the account is already open — the graceful-duplicate path."""
    if not NEW_ACCOUNT_RE.match(account):
        raise ActionError(
            f"{account!r} is not a valid category name — use Expenses:… or "
            f"Income:… with capitalized segments (letters, digits, dashes), "
            f'like "Expenses:Health:Acupuncture"')
    if account in REVIEW_BUCKETS:
        raise ActionError("that IS the review bucket — name a real category")
    if account in opened_accounts():
        return False
    chart = _chart_file()
    text = chart.read_text() if chart.exists() else ""
    line = f"{_chart_epoch().isoformat()} open {account}  USD\n"
    joined = text + ("" if text.endswith("\n") or not text else "\n") + line
    try:
        rewrite_ledger_files({chart: joined})
    except SystemExit as e:  # the writer's CLI-shaped refusal, made a 422
        raise ActionError(f"could not open {account}: {e}") from e
    return True


def categorize(payee_pattern: str, account: str | None,
               apply_history: bool,
               new_account: str | None = None) -> dict[str, object]:
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
    new_account = (new_account or "").strip()
    if account and new_account:
        raise ActionError("send either account or new_account, not both")
    if not account and not new_account:
        raise ActionError("pick a category (account) or name a new one (new_account)")
    if account:
        if not account.startswith(("Expenses:", "Income:")):
            raise ActionError("rules may only point at Expenses:* or Income:* accounts")
        if account in REVIEW_BUCKETS:
            raise ActionError("that IS the review bucket — pick a real category")
        if account not in _open_accounts():
            raise ActionError(f"no open ledger account named {account}")

    stamp = date.today().isoformat()
    opened = False
    with _WRITE_LOCK:
        if new_account:
            opened = _open_new_account(new_account)  # 422s before anything lands
            account = new_account
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
            raise ActionError(f"refusing to write rules.toml — the result "
                              f"would not parse: {e}") from e
        _atomic_write(RULES_FILE, new_text)
        _reset_rules_caches()  # the running process must see the new rule

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
            "account": account, "opened": opened,
            "applied": apply_history, "changed": changed,
            "report": proc.stdout.strip()}


def _reset_rules_caches() -> None:
    """Both rules.toml parse caches (the flat tools module AND sara.vault —
    two modules, one file) plus the suggest endpoint's per-payee cache, so
    a just-taught rule shows up in every surface immediately."""
    reset_rules_cache()
    sara.vault.reset_rules_cache()
    from . import suggest  # local: suggest imports classify (heavier module)
    suggest.reset_cache()


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
    if key not in GOAL_LIMITS:
        raise ActionError(f"'{key}' is not an app-editable goal "
                          f"(editable: education_target)")
    if isinstance(value, bool):
        raise ActionError(f"{key} needs a number")
    try:
        num = float(cast(float, value))
    except (TypeError, ValueError) as e:
        raise ActionError(f"{key} needs a number") from e
    if not (0 <= num <= GOAL_LIMITS[key]):
        raise ActionError(f"{key} must sit between 0 and "
                          f"{GOAL_LIMITS[key]:,.0f}")
    rendered = f"{num:.0f}" if num == int(num) else f"{num}"

    with _WRITE_LOCK:
        previous = _goals().get(key)
        path = VAULT / "facts" / "goals" / "index.md"
        line = f"{key}: {rendered}"
        if not path.exists():
            _atomic_write(path, GOALS_FILE_TEMPLATE.format(
                today=date.today().isoformat(), line=line))
        else:
            text = path.read_text()
            block = re.search(r"```yaml\n(.*?)```", text, re.S)
            if not block:
                _atomic_write(path,
                              text.rstrip() + "\n\n```yaml\n" + line + "\n```\n")
            else:
                body = block.group(1)
                key_line = re.compile(
                    rf"^({re.escape(key)}\s*:\s*)([^#\n]*?)(\s*#.*)?$", re.M)
                if key_line.search(body):
                    new_body = key_line.sub(
                        lambda m: f"{m.group(1)}{rendered}{m.group(3) or ''}",
                        body, count=1)
                else:
                    new_body = body.rstrip("\n") + f"\n{line}\n"
                _atomic_write(path, text[:block.start(1)] + new_body
                              + text[block.end(1):])
        now_read = _goals().get(key)
    if not (isinstance(now_read, float) and abs(now_read - num) < 0.5):
        raise ActionError(f"wrote {key} but re-reading facts/goals returned "
                          f"{now_read!r} — check the file by hand")
    return {"key": key, "value": now_read, "previous": previous}


# ----------------------------------------------------------------- dismiss
def dismiss(finding_id: str, until: str | None,
            title: str = "") -> dict[str, object]:
    if not re.fullmatch(r"[0-9a-f]{12}", finding_id or ""):
        raise ActionError("bad finding id")
    today = date.today()
    if until is None:
        with _WRITE_LOCK:
            entries = _dismissal_entries()
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
    with _WRITE_LOCK:
        entries = _dismissal_entries()
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
    _atomic_write(DISMISSALS_FILE,
                  json.dumps({"version": 1, "dismissed": kept}, indent=1,
                             sort_keys=True) + "\n")
