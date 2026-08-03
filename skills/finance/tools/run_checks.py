#!/usr/bin/env python3
"""Run all checks and write reports/findings.md — the planner's standing to-do list."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checks import run_all  # noqa: E402
from vault import REPORTS  # noqa: E402

ORDER = {"alert": 0, "watch": 1, "info": 2}
ICON = {"alert": "🔴", "watch": "🟡", "info": "⚪"}


def main():
    findings, errors = run_all()
    findings.sort(key=lambda f: ORDER.get(f["severity"], 9))
    counts = {s: sum(1 for f in findings if f["severity"] == s) for s in ORDER}
    lines = ["# Findings\n",
             f"_Generated {date.today().isoformat()} by tools/run_checks.py — regenerable, do not hand-edit._\n",
             f"**{counts['alert']} alerts · {counts['watch']} watch · {counts['info']} info**\n"]
    for f in findings:
        lines.append(f"### {ICON.get(f['severity'], '')} {f['title']}")
        lines.append(f"_{f['check']} · {f['severity']}_  ")
        lines.append(f"{f['detail']}\n")
    if errors:
        lines.append("## Check errors")
        lines += [f"- {e}" for e in errors]
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "findings.md").write_text("\n".join(lines) + "\n")
    for f in findings:
        print(f"[{f['severity'].upper():5}] {f['title']}")
    if errors:
        print("ERRORS:", *errors, sep="\n  ", file=sys.stderr)


if __name__ == "__main__":
    main()
