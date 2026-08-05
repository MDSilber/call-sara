#!/usr/bin/env python3
"""Generate the vault's reports/ from the ledger + facts. Deterministic — no judgment.

Run:    tools/run reports.py
Writes: reports/net-worth.md, reports/spend-by-month.md, reports/upcoming.md
Arithmetic is code; the agent reads these files and reasons about them.
"""
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import (REPORTS, VAULT, amount, dated_bullets, illiquid_currency_regex,  # noqa: E402
                   money, query, shadow_currency)


def stamp(name):
    return f"_Generated {date.today().isoformat()} by tools/{name} — regenerable, do not hand-edit._\n"


# ---------------------------------------------------------------- net worth
def liquid_balances():
    """Per-account spendable USD, excluding illiquid commodities."""
    excl = illiquid_currency_regex()
    where = "account ~ '^(Assets|Liabilities)'" + (f" AND NOT currency ~ '{excl}'" if excl else "")
    rows = query(f"SELECT account, sum(convert(position, 'USD')) AS bal WHERE {where} "
                 f"GROUP BY account ORDER BY account")
    out, unpriced = [], []
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


def paper_value():
    """Illiquid holdings valued in the shadow currency, or 0 if none configured."""
    excl = illiquid_currency_regex()
    if not excl:
        return 0.0
    rows = query(f"SELECT sum(convert(position, '{shadow_currency()}')) AS v "
                 f"WHERE account ~ '^Assets' AND currency ~ '{excl}'")
    return amount(rows[0]["v"], shadow_currency()) if rows and rows[0].get("v") else 0.0


def net_worth():
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
def spend_by_month(months_back=13):
    rows = query("SELECT year, month, root(account, 2) AS category, "
                 "sum(convert(position, 'USD')) AS amt "
                 "WHERE account ~ '^Expenses' "
                 "GROUP BY year, month, category ORDER BY year, month, category")
    months = sorted({(int(r["year"]), int(r["month"])) for r in rows})[-months_back:]
    keep = set(months)
    cats = {}
    for r in rows:
        ym = (int(r["year"]), int(r["month"]))
        if ym not in keep:
            continue
        cat = r["category"].replace("Expenses:", "")
        cats.setdefault(cat, {})[ym] = amount(r["amt"])
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
def upcoming():
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
        chores = [l for l in cal.read_text().splitlines()
                  if re.match(r"^- (daily|weekly|monthly|quarterly|yearly)", l, re.I)]
        if chores:
            lines += ["\n## Recurring chores"] + chores
    (REPORTS / "upcoming.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    REPORTS.mkdir(exist_ok=True)
    for fn in (net_worth, spend_by_month, upcoming):
        try:
            fn()
            print(f"ok   {fn.__name__}")
        except (Exception, SystemExit) as e:  # one broken report must not block the rest
            print(f"FAIL {fn.__name__}: {e}", file=sys.stderr)
