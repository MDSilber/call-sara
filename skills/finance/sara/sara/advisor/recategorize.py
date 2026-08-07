#!/usr/bin/env python3
# pyright: strict
"""Re-run rules.toml over transactions already in the ledger.

The loop is: notice a wrong/missing category -> add a [[payee_rules]] entry ->
run this so history matches the new rule. Only postings currently booked to
Expenses:Uncategorized (or --target ACCOUNT) are candidates, and only their
counter-posting is rewritten — dates, payees, and the primary posting are
never touched.

Usage:
  recategorize.py                 dry run: list what would change
  recategorize.py --write         apply to ledger/*.beancount (atomic tmp+rename),
                                  then bean-check — a failed check rolls every
                                  file back
  recategorize.py --target Expenses:Personal --write   re-run rules over another bucket
"""
import re
from decimal import Decimal, InvalidOperation
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from sara.rules import categorize
from sara.vault import BEAN_CHECK, LEDGER, VAULT
from sara.ledger.writer import atomic_write

TXN_HEADER = re.compile(r'^(\d{4}-\d{2}-\d{2}) [*!] "([^"]*)"')
POSTING = re.compile(r"^(\s+)([A-Z][\w:-]+)(\s+(-?[\d.,]+) USD)?\s*$")
META = re.compile(r'^\s+(?:ofx-type|chase-type|type): "([^"]*)"')

Change = tuple[str, str]  # (payee, new counter-account)


@dataclass
class _Txn:
    """One buffered transaction: header payee, its type meta, its raw lines."""
    payee: str
    ofx_type: str = ""
    body: list[str] = field(default_factory=lambda: list[str]())


def _flush(block: _Txn, target: str, out: list[str], changes: list[Change]) -> None:
    """Rewrite the buffered transaction's bare target posting, if any.

    The fallback income-vs-expense call keys on the REWRITTEN leg's own
    residual: the bare leg interpolates to -(sum of the explicit legs), so
    the signed amount handed to categorize() — "from the primary account's
    point of view" — is that residual negated, i.e. the explicit sum itself.
    Keying on whichever explicit amount happened to be parsed LAST (the old
    behavior) flipped a three-leg expense split into Income:US:Other.
    """
    payee, ofx_type, body = block.payee, block.ofx_type, block.body
    explicit_sum = Decimal(0)
    primary_acct = ""
    target_at: int | None = None
    parse_ok = True
    for j, line in enumerate(body):
        p = POSTING.match(line)
        if not p:
            continue
        if p.group(4):
            try:
                explicit_sum += Decimal(p.group(4).replace(",", ""))
            except (ValueError, InvalidOperation):
                parse_ok = False  # unreadable amount: leave this txn alone
            if not primary_acct:
                primary_acct = p.group(2)
        elif p.group(2) == target and target_at is None:
            target_at = j
    if parse_ok and target_at is not None:
        residual = -explicit_sum  # what the bare target leg holds
        new = categorize(payee, ofx_type, -residual, primary_acct)
        if new != target:
            changes.append((payee, new))
            pm = POSTING.match(body[target_at])
            indent = pm.group(1) if pm else "  "
            body[target_at] = f"{indent}{new}\n"
    out.extend(body)


def rewrite(path: Path, target: str) -> tuple[str, list[Change]]:
    """-> (new_text, changes). Buffers each transaction WHOLE before deciding:
    the residual needs every explicit leg, and the bare counter-posting is
    not always last."""
    lines = path.read_text().splitlines(keepends=True)
    out: list[str] = []
    changes: list[Change] = []
    block: _Txn | None = None
    for line in lines:
        h = TXN_HEADER.match(line)
        indented = line[:1] in (" ", "\t") and bool(line.strip())
        if block is not None and not h and indented:
            mm = META.match(line)
            if mm:
                block.ofx_type = mm.group(1)
            block.body.append(line)
            continue
        if block is not None:
            _flush(block, target, out, changes)
            block = None
        if h:
            block = _Txn(payee=h.group(2), body=[line])
        else:
            out.append(line)
    if block is not None:
        _flush(block, target, out, changes)
    return "".join(out), changes


def main() -> None:
    args = sys.argv[1:]
    write = "--write" in args
    target = "Expenses:Uncategorized"
    if "--target" in args:
        i = args.index("--target")
        value = args[i + 1] if i + 1 < len(args) else ""
        if not value or value.startswith("--"):
            sys.exit("--target needs an account name\n\n" + (__doc__ or ""))
        target = value
    total = 0
    staged: list[tuple[Path, str, str]] = []
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        original = f.read_text()
        new_text, changes = rewrite(f, target)
        if changes:
            total += len(changes)
            staged.append((f, original, new_text))
            print(f"{f.name}: {len(changes)} {'rewritten' if write else 'would change'}")
            counts: dict[str, int] = {}
            for _payee, acct in changes:
                counts[acct] = counts.get(acct, 0) + 1
            def by_count(kv: tuple[str, int]) -> int:
                return -kv[1]
            for acct, n in sorted(counts.items(), key=by_count):
                print(f"    {n:4}  -> {acct}")
    if total == 0:
        print(f"nothing to change — every {target} posting is still unmatched by rules.toml")
        return
    if not write:
        print(f"\n{total} postings would change — re-run with --write to apply.")
        return
    # Apply atomically (tmp + rename per file), then validate the whole
    # ledger; a rejected rewrite rolls every file back — recategorize must
    # never leave the ledger broken (e.g. a rule pointing at an unopened
    # account).
    for f, _original, new_text in staged:
        atomic_write(f, new_text)
    if BEAN_CHECK.exists():
        out = subprocess.run([str(BEAN_CHECK), str(LEDGER)], capture_output=True, text=True, check=False)
        if out.returncode != 0:
            for f, original, _new_text in staged:
                atomic_write(f, original)
            raise SystemExit("bean-check rejected the recategorize — rolled back, "
                             "nothing changed:\n" + (out.stderr or out.stdout).strip())
        print(f"\nrewrote {total} postings (bean-check passed).")
    else:
        print(f"\nrewrote {total} postings — bean-check not found at {BEAN_CHECK}; "
              f"validate manually:  .venv/bin/bean-check ledger/main.beancount")


if __name__ == "__main__":
    main()
