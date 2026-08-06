"""Dismissed findings — "stop showing me this until <date>".

Sara App's dismiss action writes reports/dismissals.json; every needs-you
surface (Sara Home's cards + Next line, the digest, the app itself) filters
through here so a dismissal holds everywhere at once. The id is a hash of
the finding's check + title: when a finding's title changes (a count moved,
a new account joined), the id changes with it and the finding RESURFACES —
a dismissal silences a specific statement, never a whole category.
"""
import hashlib
import json
from datetime import date, datetime

from vault import REPORTS

DISMISSALS_FILE = REPORTS / "dismissals.json"


def finding_id(check, title):
    """Stable 12-hex id for one finding statement."""
    return hashlib.sha1(f"{check}\n{title}".encode()).hexdigest()[:12]


def load_dismissals():
    """{finding_id: entry} from reports/dismissals.json ({} on any trouble —
    a mangled file must never take the page down)."""
    if not DISMISSALS_FILE.exists():
        return {}
    try:
        raw = json.loads(DISMISSALS_FILE.read_text())
        entries = raw.get("dismissed", {})
        return entries if isinstance(entries, dict) else {}
    except (ValueError, OSError, AttributeError):
        return {}


def active_ids(today=None):
    """The ids currently silenced: their `until` date is today or later."""
    today = today or date.today()
    out = set()
    for fid, entry in load_dismissals().items():
        try:
            until = datetime.strptime(str(entry.get("until")), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        if until >= today:
            out.add(fid)
    return out


def filter_findings(findings, today=None):
    """Drop findings whose (check, title) id is actively dismissed."""
    silenced = active_ids(today)
    if not silenced:
        return findings
    return [f for f in findings
            if finding_id(f.get("check", ""), f.get("title", "")) not in silenced]
