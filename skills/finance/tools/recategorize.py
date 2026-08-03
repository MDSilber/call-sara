#!/usr/bin/env python3
"""Re-run rules.toml over transactions already in the ledger.

The loop is: notice a wrong/missing category -> add a [[payee_rules]] entry ->
run this so history matches the new rule. Only postings currently booked to
Expenses:Uncategorized (or --target ACCOUNT) are candidates, and only their
counter-posting is rewritten — dates, payees, and the primary posting are
never touched.

Usage:
  recategorize.py                 dry run: list what would change
  recategorize.py --write         apply to ledger/*.beancount, then run bean-check
  recategorize.py --target Expenses:Personal --write   re-run rules over another bucket
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rules import categorize  # noqa: E402
from vault import VAULT  # noqa: E402

TXN_HEADER = re.compile(r'^(\d{4}-\d{2}-\d{2}) [*!] "([^"]*)"')
DIRECTIVE = re.compile(r"^\S")  # any unindented line starts a new directive
POSTING = re.compile(r"^(\s+)([A-Z][\w:-]+)(\s+(-?[\d.,]+) USD)?\s*$")
META = re.compile(r'^\s+(?:ofx-type|chase-type|type): "([^"]*)"')


def rewrite(path, target, write):
    lines = path.read_text().splitlines(keepends=True)
    out, changes = [], []
    payee, ofx_type, primary_amt, primary_acct = None, "", 0.0, ""
    for line in lines:
        h = TXN_HEADER.match(line)
        if not h and DIRECTIVE.match(line) and line.strip():
            payee = None  # some other directive — no live transaction context
        if h:
            payee, ofx_type, primary_amt, primary_acct = h.group(2), "", 0.0, ""
            out.append(line)
            continue
        mm = META.match(line)
        if mm:
            ofx_type = mm.group(1)
            out.append(line)
            continue
        p = POSTING.match(line)
        if p and payee is not None:
            acct = p.group(2)
            if p.group(4):  # primary posting carries the amount
                primary_acct = acct
                try:
                    primary_amt = float(p.group(4).replace(",", ""))
                except ValueError:
                    primary_amt = 0.0
            elif acct == target:  # bare counter-posting in the target bucket
                new = categorize(payee, ofx_type, primary_amt, primary_acct)
                if new != target:
                    changes.append((payee, new))
                    line = f"{p.group(1)}{new}\n"
        out.append(line)
    if write and changes:
        path.write_text("".join(out))
    return changes


def main():
    args = sys.argv[1:]
    write = "--write" in args
    target = "Expenses:Uncategorized"
    if "--target" in args:
        target = args[args.index("--target") + 1]
    total = 0
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        changes = rewrite(f, target, write)
        if changes:
            total += len(changes)
            print(f"{f.name}: {len(changes)} {'rewritten' if write else 'would change'}")
            counts = {}
            for _payee, acct in changes:
                counts[acct] = counts.get(acct, 0) + 1
            for acct, n in sorted(counts.items(), key=lambda kv: -kv[1]):
                print(f"    {n:4}  -> {acct}")
    if total == 0:
        print(f"nothing to change — every {target} posting is still unmatched by rules.toml")
    elif write:
        print(f"\nrewrote {total} postings. Now:  .venv/bin/bean-check ledger/main.beancount")
    else:
        print(f"\n{total} postings would change — re-run with --write to apply.")


if __name__ == "__main__":
    main()
