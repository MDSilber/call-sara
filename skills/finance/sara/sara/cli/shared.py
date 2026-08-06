"""Shared CLI plumbing for the importer pipelines."""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable


def since_from_argv(argv: list[str], usage: str) -> tuple[str | None, list[str]]:
    """Pull '--since YYYY-MM-DD' out of argv -> (since, remaining argv).

    Exits with usage on a missing or malformed value: rows are dropped by
    STRING comparison against this date, so '2026-6-1' would silently drop
    nothing (or everything), and '--since --write' would swallow the write
    flag AND import everything. Both are money bugs, not conveniences.
    """
    if "--since" not in argv:
        return None, argv
    i = argv.index("--since")
    value = argv[i + 1] if i + 1 < len(argv) else ""
    if value.startswith("--") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise SystemExit(f"--since needs a YYYY-MM-DD date "
                         f"(got {value or 'nothing'})\n\n{usage}")
    return value, argv[:i] + argv[i + 2:]


def reject_unknown_flags(argv: Iterable[str], known: frozenset[str], usage: str) -> None:
    unknown = {a for a in argv if a.startswith("--")} - known
    if unknown:
        raise SystemExit(f"unknown flag(s): {', '.join(sorted(unknown))}\n\n{usage}")


def err(line: str) -> None:
    print(line, file=sys.stderr)
