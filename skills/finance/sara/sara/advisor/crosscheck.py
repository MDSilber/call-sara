# pyright: strict
#!/usr/bin/env python3
"""Dual-computation gate — key report figures must agree across two
independent code paths before any report is emitted.

Run:    tools/run crosscheck.py    (also runs inside reports.py / summary.py)

Four checks, each computed once by the builders the reports actually use
and once by a direct bean-query aggregate that shares no aggregation logic
with them (beanquery does the summing; the builders sum parsed per-row
floats in Python — the only shared plumbing is the subprocess runner and
the cell parser):

  liquid  liquid net worth: reports.liquid_balances per-account sum
          vs the root-level aggregate query.py's `networth` runs
  spend   the spend matrix's latest month-column total
          vs a single-month Expenses total straight from bean-query
  assets  the per-account Assets balances sum
          vs the root-level Assets aggregate, to the cent
  owners  the owner slices (reports.owner_rollup — the partition the
          owners surfaces show) summed, vs the same independent root-level
          aggregate: the per-owner split must re-add to liquid net worth
          to the cent, so no dollar is dropped or double-owned. A ledger
          with no owner metadata compares 0 vs 0 and says so.

Any gap over $0.01 prints DUAL-COMPUTATION MISMATCH naming both values and
their paths, and exits 2 — a figure that can't be independently reproduced
is never emitted. run_checks.py is unaffected.

Test seam: FINANCE_CROSSCHECK_INJECT="<check>:<delta>" (check one of
liquid|spend|assets|owners) skews the independent path by <delta> dollars
so the refusal can be proven against a scratch vault without corrupting a
ledger.
"""
import os
import sys
from dataclasses import dataclass

from sara.vault import amount, illiquid_currency_regex, query
from sara.advisor.reports import liquid_balances, owner_rollup, spend_matrix

TOLERANCE = 0.01
INJECT_ENV = "FINANCE_CROSSCHECK_INJECT"
CHECK_NAMES = ("liquid", "spend", "assets", "owners")


@dataclass(frozen=True)
class Check:
    name: str       # inject key: liquid | spend | assets
    label: str      # human name shown on mismatch
    path_a: str     # the builder path the reports use
    value_a: float
    path_b: str     # the independent recomputation
    value_b: float

    @property
    def gap(self) -> float:
        return abs(self.value_a - self.value_b)

    @property
    def ok(self) -> bool:
        return self.gap <= TOLERANCE


def _usd(v: float) -> str:
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def _inject(check: str) -> float:
    """Test-only skew for the independent path of one named check."""
    raw = os.environ.get(INJECT_ENV, "")
    if not raw:
        return 0.0
    name, sep, delta = raw.partition(":")
    if not sep or name not in CHECK_NAMES:
        raise SystemExit(f"{INJECT_ENV} must be '<{'|'.join(CHECK_NAMES)}>:<dollars>', got {raw!r}")
    if name != check:
        return 0.0
    try:
        return float(delta)
    except ValueError:
        raise SystemExit(f"{INJECT_ENV} delta must be a number, got {delta!r}") from None


def _independent_networth() -> tuple[float, float]:
    """(assets, liabilities) from ONE root-level aggregate — the exact query
    query.py's `networth` command runs, reimplemented here so this path
    shares no aggregation code with reports.liquid_balances."""
    excl = illiquid_currency_regex()
    where = "account ~ '^(Assets|Liabilities)'" + (f" AND NOT currency ~ '{excl}'" if excl else "")
    rows = query(f"SELECT root(account,1) AS r, sum(convert(position,'USD')) AS v "
                 f"WHERE {where} GROUP BY r")
    vals = {r["r"]: amount(r["v"]) for r in rows}
    return vals.get("Assets", 0.0), vals.get("Liabilities", 0.0)


def run_crosschecks() -> list[Check]:
    balances, _unpriced = liquid_balances()
    a_assets = sum(v for acct, v in balances if acct.startswith("Assets"))
    a_liab = sum(v for acct, v in balances if acct.startswith("Liabilities"))
    b_assets, b_liab = _independent_networth()

    months, cats = spend_matrix()
    if months:
        y, m = months[-1]
        a_spend = sum(cats[c].get((y, m), 0.0) for c in cats)
        rows = query(f"SELECT sum(convert(position,'USD')) AS v "
                     f"WHERE account ~ '^Expenses' AND year = {y} AND month = {m}")
        b_spend = amount(rows[0]["v"]) if rows else 0.0
        spend_label = f"spend total, {y}-{m:02d}"
    else:
        a_spend = b_spend = 0.0
        spend_label = "spend total (no expense months yet)"

    slices = owner_rollup(balances)
    if slices:
        a_owners = sum(v for _who, v, _n in slices)
        b_owners = b_assets + b_liab
        owners_label = f"owner slices re-add to liquid net worth ({len(slices)} slices)"
    else:
        a_owners = b_owners = 0.0
        owners_label = "owner slices (no owner metadata yet)"

    return [
        Check("liquid", "liquid net worth",
              "reports.liquid_balances per-account sum", a_assets + a_liab,
              "bean-query root-level aggregate (query.py networth)",
              b_assets + b_liab + _inject("liquid")),
        Check("spend", spend_label,
              "reports.spend_matrix latest month-column total", a_spend,
              "bean-query single-month Expenses total", b_spend + _inject("spend")),
        Check("assets", "asset balances sum",
              "reports.liquid_balances Assets rows sum", a_assets,
              "bean-query root-level Assets aggregate", b_assets + _inject("assets")),
        Check("owners", owners_label,
              "reports.owner_rollup slice sum (owner-metadata partition)", a_owners,
              "bean-query root-level aggregate (query.py networth)",
              b_owners + _inject("owners")),
    ]


_verified = False


def ensure_crosschecks() -> None:
    """Refuse (exit 2) unless every figure agrees across both paths.

    Runs the queries once per process: reports.py gates before its fan-out
    and summary.py's build re-asserts for standalone runs — the cached flag
    keeps the shared invocation from paying twice.
    """
    global _verified
    if _verified:
        return
    checks = run_crosschecks()
    bad = [c for c in checks if not c.ok]
    if bad:
        lines = ["DUAL-COMPUTATION MISMATCH — refusing to emit."]
        for c in bad:
            lines += [f"  check : {c.label}",
                      f"  path A: {c.path_a} = {_usd(c.value_a)}",
                      f"  path B: {c.path_b} = {_usd(c.value_b)}",
                      f"  gap   : {_usd(c.gap)} (tolerance {_usd(TOLERANCE)})"]
        lines.append("A report figure could not be independently reproduced — "
                     "fix the ledger/tools, then regenerate.")
        print("\n".join(lines), file=sys.stderr)
        raise SystemExit(2)
    _verified = True
    print(f"cross-checks: {len(checks)}/{len(checks)} agree")


if __name__ == "__main__":
    ensure_crosschecks()
