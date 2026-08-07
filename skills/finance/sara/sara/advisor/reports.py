# pyright: strict
#!/usr/bin/env python3
"""Generate the vault's reports/ from the ledger + facts. Deterministic — no judgment.

Run:    tools/run reports.py
Writes: reports/net-worth.md, reports/spend-by-month.md, reports/upcoming.md,
        reports/home.html (via home.py — the one-viewport print glance),
        reports/summary.json (via summary.py — the machine-readable twin),
        reports/digest.{html,txt} (via digest.py — Sara's weekly letter),
        reports/analytics.duckdb + reports/exports/*.parquet (via
        sara.analytics — the disposable SQL shadow; skipped with a note
        when duckdb isn't installed)
Arithmetic is code; the agent reads these files and reasons about them.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Any

from sara.advisor.types import YM, Money
from sara.vault import (
    OWNER_JOINT,
    OWNER_UNASSIGNED,
    REPORTS,
    VAULT,
    account_owners,
    amount,
    dated_bullets,
    illiquid_currency_regex,
    money,
    query,
    shadow_currency,
)


def stamp(name: str) -> str:
    return f"_Generated {date.today().isoformat()} by tools/{name} — regenerable, do not hand-edit._\n"


# ---------------------------------------------------------------- net worth
def liquid_balances() -> tuple[list[tuple[str, Money]], list[tuple[str, str]]]:
    """Per-account spendable USD, excluding illiquid commodities."""
    excl = illiquid_currency_regex()
    where = "account ~ '^(Assets|Liabilities)'" + (f" AND NOT currency ~ '{excl}'" if excl else "")
    rows = query(f"SELECT account, sum(convert(position, 'USD')) AS bal WHERE {where} "
                 f"GROUP BY account ORDER BY account")
    out: list[tuple[str, Money]] = []
    unpriced: list[tuple[str, str]] = []
    for r in rows:
        cell = r["bal"] or ""
        if not cell.strip():
            continue
        v = amount(cell, "USD") if "USD" in cell else 0.0
        # letters other than USD left in the cell = a holding with no USD price;
        # count the priced part, and flag the leftover so it isn't silently lost
        if re.search(r"[A-Z]", cell.replace("USD", "").replace("-", "")):
            unpriced.append((r["account"], " ".join(cell.split())))
        if abs(v) >= 0.005:
            out.append((r["account"], v))
    return out, unpriced


def owner_rollup(balances: list[tuple[str, Money]]) -> list[tuple[str, Money, int]]:
    """Per-owner liquid totals from the SAME balances rows as the headline:
    [(owner, usd, n_accounts)] — people alphabetically, then 'joint', then
    'unassigned' (accounts with no owner metadata). Empty list when the
    ledger declares no owners at all, so pre-owner vaults change nothing.
    The crosscheck gate holds the slice sum to the headline to the cent."""
    owners = account_owners()
    if not owners:
        return []
    sums: dict[str, list[Any]] = {}  # owner -> [Money, n_accounts], a mutable pair
    for acct, v in balances:
        who = owners.get(acct, OWNER_UNASSIGNED)
        row = sums.setdefault(who, [0.0, 0])
        row[0] += v
        row[1] += 1

    def rank(who: str) -> tuple[int, str]:
        return (2 if who == OWNER_UNASSIGNED else 1 if who == OWNER_JOINT else 0, who)

    return [(who, val, n) for who, (val, n) in sorted(sums.items(), key=lambda kv: rank(kv[0]))]


def paper_value() -> Money:
    """Illiquid holdings valued in the shadow currency, or 0 if none configured."""
    excl = illiquid_currency_regex()
    if not excl:
        return 0.0
    rows = query(f"SELECT sum(convert(position, '{shadow_currency()}')) AS v "
                 f"WHERE account ~ '^Assets' AND currency ~ '{excl}'")
    return amount(rows[0]["v"], shadow_currency()) if rows and rows[0].get("v") else 0.0


def net_worth() -> None:
    balances, unpriced = liquid_balances()
    assets = sum(v for a, v in balances if a.startswith("Assets"))
    liabilities = sum(v for a, v in balances if a.startswith("Liabilities"))
    liquid = assets + liabilities  # liabilities are negative
    paper = paper_value()
    lines = ["# Net worth\n", stamp("reports.py"),
             "## Headline\n",
             f"- **Liquid net worth** (real, spendable USD): **{money(liquid)}**",
             f"- Assets {money(assets)} · Liabilities {money(liabilities)}"]
    if paper:
        lines += [f"- **Illiquid paper wealth** (NOT counted above): {money(paper)}",
                  f"- Combined paper picture: {money(liquid + paper)}"]
    lines += ["\n## Accounts (USD)\n", "| Account | Balance |", "|---|---|"]
    lines += [f"| `{a}` | {money(v)} |" for a, v in balances]
    if unpriced:
        lines += ["\n## ⚠️ Not counted — no USD price on file",
                  "These holdings have no price directive, so they're excluded rather than guessed.",
                  "Add a `price` line to ledger/prices.beancount to count them.\n"]
        lines += [f"- `{a}` — {cell}" for a, cell in unpriced]
    (REPORTS / "net-worth.md").write_text("\n".join(lines) + "\n")


# ------------------------------------------------------------- spend by mo
def spend_matrix(months_back: int = 13) -> tuple[list[YM], dict[str, dict[YM, Money]]]:
    """(months, {category: {ym: amt}}) over the last `months_back` months
    with expense activity — the data behind spend-by-month.md; summary.py
    serializes the same matrix."""
    rows = query("SELECT year, month, root(account, 2) AS category, "
                 "sum(convert(position, 'USD')) AS amt "
                 "WHERE account ~ '^Expenses' "
                 "GROUP BY year, month, category ORDER BY year, month, category")
    months = sorted({(int(r["year"]), int(r["month"])) for r in rows})[-months_back:]
    keep = set(months)
    cats: dict[str, dict[YM, Money]] = {}
    for r in rows:
        ym = (int(r["year"]), int(r["month"]))
        if ym not in keep:
            continue
        cat = r["category"].replace("Expenses:", "")
        cats.setdefault(cat, {})[ym] = amount(r["amt"])
    return months, cats


def spend_by_month(months_back: int = 13) -> None:
    months, cats = spend_matrix(months_back)
    header = "| Category | " + " | ".join(f"{y}-{m:02d}" for y, m in months) + " | Total |"
    sep = "|---" * (len(months) + 2) + "|"
    lines = ["# Spend by month\n", stamp("reports.py"),
             "Expenses only — transfers and card payments never touch Expenses. "
             "Uncategorized is the review queue; drive it to zero.\n", header, sep]
    for cat in sorted(cats, key=lambda c: -sum(cats[c].values())):
        vals = [cats[cat].get(m, 0.0) for m in months]
        lines.append(f"| {cat} | " + " | ".join(money(v) if abs(v) >= 1 else "" for v in vals)
                     + f" | **{money(sum(vals))}** |")
    tot = [sum(cats[c].get(m, 0.0) for c in cats) for m in months]
    lines.append("| **Total** | " + " | ".join(f"**{money(v)}**" for v in tot)
                 + f" | **{money(sum(tot))}** |")
    (REPORTS / "spend-by-month.md").write_text("\n".join(lines) + "\n")


# ----------------------------------------------------------------- upcoming
def upcoming() -> None:
    today = date.today()
    lines = ["# Upcoming\n", stamp("reports.py"),
             "Every `- YYYY-MM-DD — …` bullet across facts/, soonest first. "
             "Recurring chores live in facts/household/calendar.md.\n"]
    future = [(d, t, f) for d, t, f in dated_bullets() if d >= today]
    if future:
        lines += ["## Dated"]
        lines += [f"- **{d.isoformat()}** — {t}  _({f})_" for d, t, f in future]
    past = [(d, t, f) for d, t, f in dated_bullets() if d < today]
    if past:
        lines += ["\n## Recently passed (clean these up or roll them forward)"]
        lines += [f"- {d.isoformat()} — {t}  _({f})_" for d, t, f in past[-5:]]
    cal = VAULT / "facts" / "household" / "calendar.md"
    if cal.exists():
        chores = [ln for ln in cal.read_text().splitlines()
                  if re.match(r"^- (daily|weekly|monthly|quarterly|yearly)", ln, re.I)]
        if chores:
            lines += ["\n## Recurring chores", *chores]
    (REPORTS / "upcoming.md").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------- analytics
def analytics() -> None:
    """Rebuild reports/analytics.duckdb + exports/ — the ledger's SQL shadow
    (a regenerated cache, never an archive; sara.analytics owns the build
    and refuses to emit unless its own cross-checks agree)."""
    try:
        from sara.analytics import build  # lazy: duckdb is the optional [analytics] extra
    except ModuleNotFoundError as e:
        sara_dir = Path(__file__).resolve().parent.parent / "sara"
        print(f"skip analytics: {e.name} not installed "
              f"(vault venv: pip install -e '{sara_dir}[analytics]')")
        return
    build()


if __name__ == "__main__":
    from sara.advisor.crosscheck import ensure_crosschecks  # the dual-computation gate
    ensure_crosschecks()  # refuses (exit 2) before anything is written
    REPORTS.mkdir(exist_ok=True)
    # the static dashboard retired 2026-08-07 — clear a stale artifact so an
    # old page can never be mistaken for a fresh one
    (REPORTS / "dashboard.html").unlink(missing_ok=True)
    from sara.advisor.digest import digest  # same: the weekly letter reuses the builders
    from sara.advisor.home import home  # imported lazily: home reuses this module's math
    from sara.advisor.summary import summary  # same: the machine-readable twin
    for fn in (net_worth, spend_by_month, upcoming, home, summary, digest,
               analytics):
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except (Exception, SystemExit) as e:  # one broken report must not block the rest
            print(f"FAIL {fn.__name__}: {e}", file=sys.stderr)
