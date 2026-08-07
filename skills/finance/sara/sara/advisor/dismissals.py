"""Dismissed and decided findings — the two ways a finding goes quiet.

DISMISSED — "stop showing me this until <date>". Sara App's dismiss action
writes reports/dismissals.json; every needs-you surface (Sara Home's cards
+ Next line, the digest, the app itself) filters through here so a
dismissal holds everywhere at once.

DECIDED — "we settled this; never raise it again". Permanent, and kept in
the vault itself (facts/goals/index.md yaml, key `decided:`) because a
decision is a fact about the household, not view state. mark_decided()
writes it; the app's Decided button calls the same helper.

Both use the same id: a hash of the finding's check + title. When a
finding's title changes (a count moved, a new account joined), the id
changes with it and the finding RESURFACES — dismissal and decision alike
silence a specific statement, never a whole category.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from typing import Any

from sara.advisor.types import Finding
from sara.typed import as_dict
from sara.vault import REPORTS, VAULT

DISMISSALS_FILE = REPORTS / "dismissals.json"
GOALS_FILE = VAULT / "facts" / "goals" / "index.md"
DECIDED_KEY = "decided"
_DECIDED_LINE = re.compile(rf"(?m)^(\s*{DECIDED_KEY}:\s*)\[([^\]]*)\]")


def finding_id(check: str, title: str) -> str:
    """Stable 12-hex id for one finding statement."""
    return hashlib.sha1(f"{check}\n{title}".encode()).hexdigest()[:12]


def load_dismissals() -> dict[str, dict[str, Any]]:
    """{finding_id: entry} from reports/dismissals.json ({} on any trouble —
    a mangled file must never take the page down)."""
    if not DISMISSALS_FILE.exists():
        return {}
    try:
        raw = as_dict(json.loads(DISMISSALS_FILE.read_text()))
    except (ValueError, OSError):
        return {}
    return {str(k): as_dict(v) for k, v in as_dict(raw.get("dismissed")).items()}


def active_ids(today: date | None = None) -> set[str]:
    """The ids currently silenced: their `until` date is today or later."""
    today = today or date.today()
    out: set[str] = set()
    for fid, entry in load_dismissals().items():
        try:
            until = datetime.strptime(str(entry.get("until")), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if until >= today:
            out.add(fid)
    return out


def decided_ids() -> set[str]:
    """The permanently-settled ids: facts/goals/index.md's `decided:` inline
    yaml list. Read tolerantly — a hand-mangled list yields the ids it can."""
    if not GOALS_FILE.exists():
        return set[str]()
    try:
        m = _DECIDED_LINE.search(GOALS_FILE.read_text())
    except OSError:
        return set[str]()
    if not m:
        return set[str]()
    return {t for t in re.findall(r"[0-9a-f]{12}", m.group(2))}


def mark_decided(check: str, title: str) -> str:
    """Record one finding as decided — it never emits again on any surface.

    Appends finding_id(check, title) to the `decided:` list in the
    facts/goals yaml block (creating key or file if missing), following the
    same edit-the-yaml-block convention as the app's set-goal action, then
    verifies by re-reading. Returns the id."""
    fid = finding_id(check, title)
    if not GOALS_FILE.exists():
        GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        GOALS_FILE.write_text(
            f"---\ntype: goals\ndate: {date.today().isoformat()}\n---\n"
            f"# Goals & thresholds (machine-read by tools/checks.py)\n\n"
            f"```yaml\n{DECIDED_KEY}: [{fid}]\n```\n")
    else:
        text = GOALS_FILE.read_text()
        if fid in decided_ids():
            return fid
        def _append(m: re.Match[str]) -> str:
            ids: list[str] = [*re.findall(r"[0-9a-f]{12}", m.group(2)), fid]
            return m.group(1) + "[" + ", ".join(ids) + "]"
        new, n = _DECIDED_LINE.subn(_append, text, count=1)
        if n == 0:
            line = f"{DECIDED_KEY}: [{fid}]"
            block = re.search(r"```yaml\n(.*?)```", text, re.S)
            if block:
                new = (text[:block.end(1)].rstrip("\n")
                       + f"\n{line}\n" + text[block.end(1):])
            else:
                new = text.rstrip() + f"\n\n```yaml\n{line}\n```\n"
        GOALS_FILE.write_text(new)
    if fid not in decided_ids():
        raise RuntimeError(f"wrote decided id {fid} but re-reading "
                           f"facts/goals did not return it")
    return fid


def filter_findings(findings: list[Finding], today: date | None = None) -> list[Finding]:
    """Drop findings whose (check, title) id is actively dismissed or was
    marked decided — the one chokepoint every needs-you surface shares."""
    silenced = active_ids(today) | decided_ids()
    if not silenced:
        return findings
    return [f for f in findings
            if finding_id(str(f.get("check", "")), str(f.get("title", ""))) not in silenced]
