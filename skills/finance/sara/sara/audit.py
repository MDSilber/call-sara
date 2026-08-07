"""One-shot audit: cross-source cash duplicates already IN the ledger.

Usage:
  python -m sara.audit [--write] [--min-amount 1.00]
  (or: tools/run audit.py — same flags)

The live pipelines now catch cross-source duplicates on the way in (the
cash-xsrc tier); this tool finds the ones that landed BEFORE that tier
existed — the same purchase booked once by the Chase-CSV/OFX era and again
by the Plaid feed, with different payee text and drifted dates.

Matcher (identical to the live tier): same account + same SIGNED amount to
the cent + dates within ±3 days + DIFFERENT source families (machine rows
only; hand/legacy entries are never touched). Pairs are formed greedily by
closest date, each entry consumed at most once, and only pairs at or above
--min-amount (default $1.00) are considered.

REPORT-ONLY BY DEFAULT: the full pair list prints with a keep/drop verdict
per pair, the planned derivation-seed adjustments, and dollar impact by
month — nothing is modified. The keep rule: the plaid-id row survives (it
is the ongoing feed's provenance; future syncs dedupe against it by id),
the csv/ofx twin is dropped. A pair with NO plaid side (csv vs ofx) is
listed as REVIEW and never auto-deleted.

DERIVATION SEEDS: vaults whose "Opening balance" seed entries were trued
against live balances WHILE the duplicates existed absorbed the duplicate
sums — deleting drop-sides alone would break the (correct) balance
anchors. So --write, in the SAME atomic transaction, adjusts each affected
account's seed posting by exactly the removed sum (sign-aware) and appends
the adjustment to the seed's trailing comment. An affected account with NO
seed entry is listed loudly and its pairs are excluded from --write
(nothing deleted there) unless --force-no-seed; an account with several
seed entries is ambiguous and always refuses. Everything lands through the
standard gates: atomic rewrite, bean-check, full rollback on any failure.
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import NamedTuple

from sara.cli.shared import err, reject_unknown_flags
from sara.ledger.writer import (
    CASH_XSRC_WINDOW_DAYS,
    FAMILY_PLAID,
    FIRST_POSTING,
    TXN_BLOCK,
    apply_edits,
    entry_family,
)
from sara.vault import VAULT, require_vault

FLAGS = frozenset({"--write", "--force-no-seed"})
DEFAULT_MIN_AMOUNT = Decimal("1.00")
SEED_NARRATION = "Opening balance"  # the derivation-seed naming convention

_HEADER_PAYEE = re.compile(r'^\d{4}-\d{2}-\d{2}\s+[*!]\s+"([^"]*)"')
_HEADER_NARRATION = re.compile(r'^\d{4}-\d{2}-\d{2}\s+[*!]\s+"[^"]*"\s+"([^"]*)"')
_POSTING_LINE = re.compile(
    r"^[ \t]+([A-Z][A-Za-z0-9:_-]*)[ \t]+(-?[\d,.]+)[ \t]+([A-Z][A-Z0-9'._-]*)(?=[ \t;]|$)",
    re.M)


class CashEntry(NamedTuple):
    file: Path
    entry_text: str
    account: str
    when: date
    amount: Decimal  # signed net USD on the account
    family: str
    payee: str


class Pair(NamedTuple):
    keep: CashEntry
    drop: CashEntry
    auto: bool  # False = REVIEW (no plaid side) — never auto-deleted

    @property
    def dollars(self) -> Decimal:
        return abs(self.drop.amount)


class SeedPlan(NamedTuple):
    """One derivation seed to compensate, ready for the atomic write."""

    account: str
    file: Path
    entry_text: str
    new_entry_text: str
    old_amount: Decimal
    delta: Decimal  # the removed sum — what the seed absorbs back

    @property
    def new_amount(self) -> Decimal:
        return self.old_amount + self.delta


def _seed_posting_re(account: str) -> re.Pattern[str]:
    return re.compile(rf"^([ \t]+{re.escape(account)}[ \t]+)(-?[\d,.]+)( USD)(.*)$", re.M)


def find_seed_entries(account: str) -> list[tuple[Path, str]]:
    """Every "Opening balance" derivation-seed entry posting to `account`."""
    out: list[tuple[Path, str]] = []
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            text = f.read_text()
        except OSError:
            continue
        for m in TXN_BLOCK.finditer(text):
            nm = _HEADER_NARRATION.match(m.group(0))
            if not nm or SEED_NARRATION not in nm.group(1):
                continue
            if _seed_posting_re(account).search(m.group(1)):
                out.append((f, m.group(0)))
    return out


def plan_seed_adjustment(account: str, delta: Decimal,
                         today: date) -> SeedPlan | str:
    """A ready-to-apply seed compensation for one account, or the refusal
    reason ("no seed" / "ambiguous seeds")."""
    seeds = find_seed_entries(account)
    if not seeds:
        return "no seed"
    if len(seeds) > 1:
        return "ambiguous seeds"
    f, entry_text = seeds[0]
    pm = _seed_posting_re(account).search(entry_text)
    assert pm is not None  # find_seed_entries matched on this very regex
    old_amount = _dec(pm.group(2))
    if old_amount is None:
        return "unparseable seed amount"
    note = f"and {delta:+,.2f} on {today.isoformat()} when cross-source duplicates were removed"
    tail = pm.group(4).rstrip()
    new_tail = f"{tail}; {note}" if tail.strip().startswith(";") else f"{tail}  ; {note}"
    new_line = f"{pm.group(1)}{old_amount + delta:.2f}{pm.group(3)}{new_tail}"
    new_entry = entry_text.replace(pm.group(0), new_line, 1)
    return SeedPlan(account, f, entry_text, new_entry, old_amount, delta)


def plan_seeds(pairs: list[Pair], today: date) -> tuple[list[SeedPlan], dict[str, str]]:
    """Per-account compensation plans for every auto pair's account, plus
    the accounts that refuse ({account: reason})."""
    removed: dict[str, Decimal] = {}
    for p in pairs:
        if p.auto:
            removed[p.drop.account] = removed.get(p.drop.account, Decimal(0)) + p.drop.amount
    plans: list[SeedPlan] = []
    refused: dict[str, str] = {}
    for account in sorted(removed):
        if removed[account] == 0:
            continue  # deletions net to zero — the anchors never noticed
        plan = plan_seed_adjustment(account, removed[account], today)
        if isinstance(plan, str):
            refused[account] = plan
        else:
            plans.append(plan)
    return plans, refused


def _dec(text: str) -> Decimal | None:
    try:
        d = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    return d if d.is_finite() else None


def machine_cash_entries(min_amount: Decimal) -> list[CashEntry]:
    """Every machine-written PURE-cash entry ledger-wide (no units postings
    on its own account), at or above the floor — the audit corpus."""
    out: list[CashEntry] = []
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            text = f.read_text()
        except OSError:
            continue
        for m in TXN_BLOCK.finditer(text):
            body = m.group(1)
            family = entry_family(body)
            if not family:
                continue  # hand/legacy rows are never the audit's business
            pm = FIRST_POSTING.search(body)
            if not pm:
                continue
            account = pm.group(1)
            try:
                when = datetime.strptime(m.group(0)[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            usd = Decimal(0)
            has_units = False
            for post in _POSTING_LINE.finditer(body):
                if post.group(1) != account:
                    continue
                number = _dec(post.group(2))
                if number is None:
                    continue
                if post.group(3) == "USD":
                    usd += number
                else:
                    has_units = True
            if has_units or usd == 0 or abs(usd) < min_amount:
                continue
            hm = _HEADER_PAYEE.match(m.group(0))
            out.append(CashEntry(f, m.group(0), account, when, usd, family,
                                 hm.group(1) if hm else ""))
    return out


def find_pairs(entries: list[CashEntry],
               window: int = CASH_XSRC_WINDOW_DAYS) -> list[Pair]:
    """Greedy closest-date pairing of cross-family same-signed-cents entries,
    per account, each entry consumed at most once."""
    by_account: dict[str, list[CashEntry]] = {}
    for e in entries:
        by_account.setdefault(e.account, []).append(e)
    pairs: list[Pair] = []
    for rows in by_account.values():
        candidates: list[tuple[int, date, int, int]] = []
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                a, b = rows[i], rows[j]
                if a.family == b.family or a.amount != b.amount:
                    continue
                days = abs((a.when - b.when).days)
                if days > window:
                    continue
                candidates.append((days, min(a.when, b.when), i, j))
        claimed: set[int] = set()
        for _days, _when, i, j in sorted(candidates):
            if i in claimed or j in claimed:
                continue
            claimed.update((i, j))
            a, b = rows[i], rows[j]
            if a.family == FAMILY_PLAID:
                pairs.append(Pair(keep=a, drop=b, auto=True))
            elif b.family == FAMILY_PLAID:
                pairs.append(Pair(keep=b, drop=a, auto=True))
            else:  # csv vs ofx — no ongoing-feed side to prefer; a human picks
                pairs.append(Pair(keep=a, drop=b, auto=False))
    return sorted(pairs, key=lambda p: (p.keep.account, p.keep.when))


def print_report(pairs: list[Pair], plans: list[SeedPlan],
                 refused: dict[str, str], write: bool, force: bool) -> None:
    mode = "WRITE" if write else "REPORT ONLY (re-run with --write to remove the drop side)"
    print(f"== sara cash cross-source audit — {mode} ==")
    if not pairs:
        print("\nno cross-source duplicate pairs found.")
        return
    account = None
    for p in pairs:
        if p.keep.account != account:
            account = p.keep.account
            print(f"\n{account}")
        verdict = "drop" if p.auto else "REVIEW (no plaid side — pick by hand)"
        print(f"  PAIR {p.keep.amount:>10,.2f}  keep  {p.keep.when} "
              f"{p.keep.payee!r} [{p.keep.family}]")
        print(f"       {'':>10}  {verdict:<4}  {p.drop.when} "
              f"{p.drop.payee!r} [{p.drop.family}]")
    if plans or refused:
        print("\nderivation-seed compensation (the seeds were trued while the "
              "duplicates existed — deleting must give the sums back):")
        for plan in plans:
            print(f"  {plan.account}: seed {plan.old_amount:,.2f} -> "
                  f"{plan.new_amount:,.2f} ({plan.delta:+,.2f}) [{plan.file.name}]")
        for acct, reason in sorted(refused.items()):
            if force and reason == "no seed":
                print(f"  {acct}: {reason} — deleting WITHOUT compensation "
                      f"(--force-no-seed); expect anchors to move")
            else:
                action = ("--force-no-seed deletes anyway"
                          if reason == "no seed" else "never auto-deleted")
                print(f"  {acct}: {reason.upper()} — its pairs are EXCLUDED from "
                      f"--write ({action})")
    print("\nby month (|$| of duplicated rows):")
    by_month: dict[str, tuple[int, Decimal]] = {}
    for p in pairs:
        key = p.drop.when.strftime("%Y-%m")
        n, total = by_month.get(key, (0, Decimal(0)))
        by_month[key] = (n + 1, total + p.dollars)
    for month in sorted(by_month):
        n, total = by_month[month]
        print(f"  {month}: {n} pair{'s' if n != 1 else ''}, ${total:,.2f}")
    auto = [p for p in pairs if p.auto]
    review = [p for p in pairs if not p.auto]
    print(f"\ntotal: {len(pairs)} pairs, ${sum((p.dollars for p in pairs), Decimal(0)):,.2f} "
          f"duplicated — {len(auto)} auto-removable (keep plaid), "
          f"{len(review)} need review")


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = __doc__ or ""
    min_amount = DEFAULT_MIN_AMOUNT
    if "--min-amount" in argv:
        i = argv.index("--min-amount")
        raw = argv[i + 1] if i + 1 < len(argv) else ""
        value = _dec(raw)
        if value is None or value < 0:
            raise SystemExit(f"--min-amount needs a dollar figure (got {raw or 'nothing'})\n\n{usage}")
        min_amount = value
        argv = argv[:i] + argv[i + 2:]
    reject_unknown_flags(argv, FLAGS, usage)
    write = "--write" in argv
    force = "--force-no-seed" in argv
    require_vault()
    pairs = find_pairs(machine_cash_entries(min_amount))
    plans, refused = plan_seeds(pairs, date.today())
    print_report(pairs, plans, refused, write, force)
    if not write:
        return
    excluded = {acct for acct, reason in refused.items()
                if not (force and reason == "no seed")}
    auto = [p for p in pairs if p.auto and p.drop.account not in excluded]
    if not auto:
        print("\nnothing auto-removable — no writes.")
        return
    paths = apply_edits(
        deletions=[(p.drop.file, p.drop.entry_text) for p in auto],
        replacements=[(plan.file, plan.entry_text, plan.new_entry_text)
                      for plan in plans],
    )
    print(f"\nremoved {len(auto)} duplicate entr{'y' if len(auto) == 1 else 'ies'} "
          f"and adjusted {len(plans)} derivation seed{'s' if len(plans) != 1 else ''} "
          f"in {', '.join(sorted(set(paths)))} (bean-check passed); "
          f"kept every plaid-id row. REVIEW pairs were not touched.")
    err("; tip: re-run the audit — it should now find only REVIEW pairs, if any")


if __name__ == "__main__":
    main()
