#!/usr/bin/env python3
"""Generate reports/home.html — "Sara Home", the spouse-legible morning page.

Run:    tools/run home.py
Writes: reports/home.html — the ONLY file written; the vault is otherwise
        read-only. One fully self-contained page (Inter + Apache ECharts
        inlined, CSP default-src 'none') openable straight from disk; it
        cannot phone home. Three views: fava (dashboard.sh) is the
        microscope, reports/dashboard.html (--pretty) the dense brief, and
        THIS page (--home) the morning glance a spouse reads in ten seconds.

Page contract, in order: the aurora hero (greeting, Sara's one-line verdict,
freshness stamp) with the four headline KPIs floating over it — liquid net
worth, left this month, net this month, walk-away % — then: is this month
unusual (spend-pace: solid actual vs dotted typical-median path)? is the
machine running (rules.toml [[lanes]] — payroll deposits, auto-invests,
floors — via checks.lane_status, the same detector the findings use)? does
anything need us ("Needs you" verb cards + "Money that must move" dated
obligations with real dollars from facts/)? are the long lines going up
(walk-away + what-if dials, the 529 story, realized "Sara's wins", the
liquid net-worth curve)? Then the month's cheshbon with its category ribbon
and the project envelopes. EVERY figure carries its window label; every
delta names its comparison.

Money honesty, rendered: Python computes and formats every dollar; JS
receives chart geometry (plain numbers) and display strings only — no
toFixed, no float math on money, no compact notation on real values. Whole
dollars, true minus U+2212, ≈ on every derived or projected figure with its
window named. Liquid-only counting per THESIS: illiquid paper appears only
as the labelled "if the paper converts" shadow, never inside a total.
"Sara's wins" parses only explicit `[x] … realized $N` lines from dated
notes — no estimate ever counts as found money.

Security: payees, findings text, facts bullets, lane names, and account
names are bank-controlled DATA. Server side: the page renders through
jinja2 with autoescape on (markupsafe), so every context string is escaped
by default; the few intentional-HTML fragments are built with Markup.format,
which escapes their arguments. Chart data rides one
<script type="application/json"> island with '<' escaped so no payload can
close the tag. Client side: JSON.parse plus textContent / esc()-escaped
strings only; the CSP meta (default-src 'none') makes zero-network a
browser-enforced property, not a promise.
"""
import base64
import contextlib
import json
import math
import re
import sys
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import NamedTuple

import jinja2
from markupsafe import Markup, escape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import REPORTS, VAULT, amount, dated_bullets, household, query  # noqa: E402
from reports import liquid_balances, paper_value  # noqa: E402
from webview import (MONTH_ABBR, action_queue, code_spans,  # noqa: E402
                     deadline_items, latest_ledger_date, month_label,
                     networth_series, nice_ticks, parse_findings)
from checks import goals as goals_config  # noqa: E402
from checks import lane_status  # noqa: E402

ASSETS = Path(__file__).resolve().parent / "assets"
MONTH_FULL = ["", "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
MINUS = "−"               # U+2212, the true minus — never the ASCII hyphen
PACE_WINDOW = 6           # "a typical month" = median of the last 6 full months
BASELINE_WINDOW = 12      # the true-spend baseline = median of last 12 full months
MIN_FULL_MONTHS = 3       # fewer than this and a median is a guess, not a baseline
WALKAWAY_LO, WALKAWAY_HI = 25, 28.5   # 4% and 3.5% rules, as spend multiples
NEEDS_CARDS = 3           # verb cards shown; the rest fold into a count
DEADLINE_CARD_DAYS = 10   # deadlines this close become cards, not just counts
MUSTMOVE_DAYS = 60        # dated obligations with dollars, this far ahead
MUSTMOVE_MAX = 6          # chips shown; facts/ keeps the rest
RIBBON_SLOTS = 3          # cheshbon ribbon: top categories shown; rest fold to Other

YM = tuple[int, int]      # a calendar month as (year, month)


# --------------------------------------------------------- typed containers
@dataclass(frozen=True)
class Pace:
    """The spend-pace numbers. `ideal` is the MEDIAN PATH of the window
    months (cumulative by day-of-month, median across months) — its
    endpoint IS the median of the month totals, so hero and line agree."""
    cur: YM
    ndays: int
    through_day: int | None       # ledger's reach into the month; None = no data
    daily_cum: list[float]        # cumulative actual spend, day 1..through_day
    spent: float
    typical: float | None         # median full-month total (= ideal[-1])
    typical_window: list[YM]
    ideal: list[float]            # the typical path, day 1..ndays
    typical_by_now: float | None  # ideal[through_day - 1]
    left: float | None            # typical − spent (the KPI-strip number)
    pace_delta: float | None      # spent − typical_by_now (the card's hero)
    fallback: bool                # True = pacing the last imported month instead


@dataclass(frozen=True)
class Baseline:
    monthly: float                # median full-month spend over the window
    months: list[YM]
    burn: float                   # MEAN full-month spend — the true burn a
                                  # year actually costs, lumpy months included
    drift_pct: float | None       # burn vs the 12 months before the window;
                                  # None until 24 full months exist


@dataclass(frozen=True)
class Walkaway:
    target: float
    src: str                      # 'set' (facts/goals) | 'computed' (baseline×25)
    lo: float | None              # baseline×12×25, $1k-rounded
    hi: float | None              # baseline×12×28.5, $1k-rounded
    baseline: Baseline | None
    pct: float                    # liquid progress, percent of target
    paper: float
    paper_pct: float              # (liquid+paper) progress — the labelled shadow


class Card(NamedTuple):
    kind: str                     # alert | watch | deadline — picks the dot color
    verb: str                     # the imperative headline
    why: str                      # what happened, or when it's due
    meta: str                     # tiny right-hand label


class EduAccount(NamedTuple):
    account: str
    kid: str
    value: float
    at_cost: bool                 # True = no market price on file, valued at cost


class Envelope(NamedTuple):
    tag: str
    spent: float
    budget: float | None


# ------------------------------------------------------------- formatting
def m0(x: float) -> str:
    """Whole dollars with a true minus: -1234.5 -> −$1,235."""
    r = round(x)
    return f"{MINUS}${abs(r):,.0f}" if r < 0 else f"${r:,.0f}"


def delta0(x: float) -> str:
    """Signed whole dollars for deltas: +$120 / −$85 / ±$0."""
    r = round(x)
    sign = "+" if r > 0 else (MINUS if r < 0 else "±")
    return f"{sign}${abs(r):,.0f}"


def round1k(x: float) -> float:
    """Nearest $1,000 — for ×25-amplified estimates, where dollar precision
    would be false precision."""
    return round(x / 1000.0) * 1000


def mon_d(d: date) -> str:
    return f"{MONTH_ABBR[d.month]} {d.day}"


def mon_yr(ym: YM) -> str:
    return f"{MONTH_ABBR[ym[1]]} {ym[0]}"


def window_label(months: list[YM]) -> str:
    """[(y, m), ...] -> 'Feb–Jul 2026' or 'Aug 2025 – Jul 2026'."""
    if not months:
        return ""
    a, b = months[0], months[-1]
    if a == b:
        return mon_yr(a)
    if a[0] == b[0]:
        return f"{MONTH_ABBR[a[1]]}–{MONTH_ABBR[b[1]]} {a[0]}"
    return f"{mon_yr(a)} – {mon_yr(b)}"


def pct_display(pct: float) -> str:
    """Never claim 100% early: 99.5–99.99 displays as 99%, not a rounded 100%."""
    return f"{pct:.0f}%" if pct < 99.5 or pct >= 100 else "99%"


def add_months(d: date, n: int) -> date:
    y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


# ------------------------------------------------------------------- data
def monthly_expense_totals() -> list[tuple[YM, float]]:
    """[((y, m), total_spend)] for every month with expense activity."""
    rows = query("SELECT year, month, sum(convert(position, 'USD')) AS v "
                 "WHERE account ~ '^Expenses' GROUP BY year, month "
                 "ORDER BY year, month")
    out = []
    for r in rows:
        try:
            out.append(((int(r["year"]), int(r["month"])), amount(r["v"])))
        except (TypeError, ValueError):
            continue
    return out


def month_in_out(ym: YM) -> tuple[float, float]:
    """(income, expenses) for one calendar month, both positive dollars."""
    rows = query(f"SELECT root(account,1) AS r, sum(convert(position,'USD')) AS v "
                 f"WHERE year = {ym[0]} AND month = {ym[1]} "
                 f"AND account ~ '^(Income|Expenses)' GROUP BY r")
    vals = {r["r"]: amount(r["v"]) for r in rows}
    return -vals.get("Income", 0.0), vals.get("Expenses", 0.0)


def month_categories(ym: YM) -> list[tuple[str, float]]:
    """This month's spending by category (root 2), largest first — the
    cheshbon ribbon's data. Refund-net categories (≤ $0) drop out."""
    rows = query(f"SELECT root(account,2) AS cat, sum(convert(position,'USD')) "
                 f"AS v WHERE account ~ '^Expenses' AND year = {ym[0]} "
                 f"AND month = {ym[1]} GROUP BY cat")
    cats = [(r["cat"] or "Expenses", amount(r["v"])) for r in rows]
    return sorted([(c, v) for c, v in cats if v > 0.005], key=lambda cv: -cv[1])


def spend_pace(today: date, asof: date | None,
               totals: list[tuple[YM, float]], month: YM | None = None) -> Pace:
    """Compute the Pace for the calendar month (or `month`, the stale-ledger
    fallback). The median path beats a straight line because a rent-shaped
    day 1 shouldn't false-alarm; honesty about ledger lag means the actual
    line ends at the ledger's last posting day, never at 'today'."""
    cur = month or (today.year, today.month)
    ndays = monthrange(*cur)[1]
    window = [(ym, v) for ym, v in totals if ym < cur][-PACE_WINDOW:]
    win_months = [ym for ym, _ in window]

    # one query covers the window months and the current month, day by day
    start = date(win_months[0][0], win_months[0][1], 1) if win_months \
        else date(cur[0], cur[1], 1)
    rows = query(f"SELECT date, sum(convert(position,'USD')) AS v "
                 f"WHERE account ~ '^Expenses' AND date >= {start.isoformat()} "
                 f"GROUP BY date ORDER BY date")
    by_month_day: dict[YM, dict[int, float]] = {}
    for r in rows:
        try:
            d = date.fromisoformat(r["date"])
        except (KeyError, TypeError, ValueError):
            continue
        days = by_month_day.setdefault((d.year, d.month), {})
        days[d.day] = days.get(d.day, 0.0) + amount(r["v"])

    typical, ideal = None, []
    if len(window) >= MIN_FULL_MONTHS:
        cums = {}
        for ym in win_months:
            run, cs = 0.0, []
            for d in range(1, monthrange(*ym)[1] + 1):
                run += by_month_day.get(ym, {}).get(d, 0.0)
                cs.append(run)
            cums[ym] = cs
        # a month shorter than day d contributes its total from then on
        ideal = [round(median(cums[ym][min(d, len(cums[ym])) - 1]
                              for ym in win_months), 2)
                 for d in range(1, ndays + 1)]
        typical = ideal[-1]  # at the last day every month contributes its
        # total, so the path's endpoint IS the median of the month totals

    daily = by_month_day.get(cur, {})
    # through-day: the ledger's own reach into this month (feed lag stays visible)
    through_day = None
    cand = min(asof, date(cur[0], cur[1], ndays)) if asof else None
    if cand and month is None:
        cand = min(cand, today)
    if cand and cand >= date(cur[0], cur[1], 1):
        through_day = cand.day
    elif daily:  # postings exist but max(date) sits elsewhere — trust the postings
        through_day = max(daily)

    cum, run = [], 0.0
    for d in range(1, (through_day or 0) + 1):
        run += daily.get(d, 0.0)
        cum.append(round(run, 2))
    spent = cum[-1] if cum else 0.0
    typical_by_now = ideal[through_day - 1] if (ideal and through_day) else None
    return Pace(
        cur=cur, ndays=ndays, through_day=through_day, daily_cum=cum,
        spent=spent, typical=typical, typical_window=win_months, ideal=ideal,
        typical_by_now=typical_by_now,
        left=(typical - spent) if typical is not None else None,
        pace_delta=(spent - typical_by_now) if typical_by_now is not None else None,
        fallback=month is not None)


def true_spend_baseline(today: date,
                        totals: list[tuple[YM, float]]) -> Baseline | None:
    """The spend baseline over the last up-to-12 FULL months: the median
    (a typical month — the pace card's comparator) and the MEAN (the true
    burn — what a year actually costs, lumpy months included; the walk-away
    math and the what-if spend dial run on this one). With 24 full months
    on file, drift_pct says how this year's burn compares to the last."""
    cur = (today.year, today.month)
    full = [(ym, v) for ym, v in totals if ym < cur]
    window = full[-BASELINE_WINDOW:]
    if len(window) < MIN_FULL_MONTHS:
        return None
    burn = sum(v for _, v in window) / len(window)
    prior = full[-2 * BASELINE_WINDOW:-BASELINE_WINDOW]
    drift = None
    if len(window) == BASELINE_WINDOW and len(prior) == BASELINE_WINDOW:
        prior_burn = sum(v for _, v in prior) / BASELINE_WINDOW
        if prior_burn > 0:
            drift = 100.0 * (burn - prior_burn) / prior_burn
    return Baseline(monthly=median(v for _, v in window),
                    months=[ym for ym, _ in window],
                    burn=burn, drift_pct=drift)


def _as_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def walkaway(liquid: float, paper: float, baseline: Baseline | None,
             goals: dict) -> Walkaway | None:
    """The walk-away hero: target from the set goal when there is one, else
    from the true burn ×25 (the ×28.5 end of the range shown too). Burn, not
    the median — a year costs its lumpy months too, and a walk-away number
    that forgets them is optimistic exactly when it must not be."""
    target_set = _as_float(goals.get("retirement_target"))
    lo = hi = None
    if baseline:
        annual = baseline.burn * 12
        lo, hi = round1k(annual * WALKAWAY_LO), round1k(annual * WALKAWAY_HI)
    if target_set is not None:
        target, src = target_set, "set"
    elif lo:
        target, src = lo, "computed"
    else:
        return None
    return Walkaway(
        target=target, src=src, lo=lo, hi=hi, baseline=baseline,
        pct=100.0 * liquid / target if target else 0.0, paper=paper,
        paper_pct=100.0 * (liquid + paper) / target if target else 0.0)


# ----------------------------------------------------------- what-if grid
WHATIF_RATES = [round(3.0 + 0.1 * i, 1) for i in range(21)]     # 3.0–5.0%
WHATIF_GROWTHS = [round(0.5 * i, 1) for i in range(17)]         # 0–8% real
WHATIF_SPEND_STEP = 5_000       # slider step for annual spend
WHATIF_MAX_YEARS = 40           # projection horizon; beyond = "not within 40"


def net_savings_baseline(months: list[YM]) -> float:
    """Median monthly net (income − expenses) over the baseline window —
    the contribution stream the what-if projection compounds."""
    if not months:
        return 0.0
    rows = query("SELECT year, month, root(account,1) AS r, "
                 "sum(convert(position,'USD')) AS v "
                 "WHERE account ~ '^(Income|Expenses)' GROUP BY year, month, r")
    per: dict[YM, dict[str, float]] = {}
    for r in rows:
        try:
            ym = (int(r["year"]), int(r["month"]))
        except (TypeError, ValueError):
            continue
        per.setdefault(ym, {})[r["r"]] = amount(r["v"])
    keep = set(months)
    nets = [-d.get("Income", 0.0) - d.get("Expenses", 0.0)
            for ym, d in per.items() if ym in keep]
    return median(nets) if nets else 0.0


def _months_to_target(liquid: float, monthly_save: float, target: float,
                      growth_pct: float) -> float | None:
    """Months until liquid + monthly savings, compounding monthly at the
    given annual real growth, first reaches target. Closed form (verified
    independently by the ground-truth table); None = not within the horizon."""
    if liquid >= target:
        return 0.0
    horizon = WHATIF_MAX_YEARS * 12
    if growth_pct <= 0:
        if monthly_save <= 0:
            return None
        m = (target - liquid) / monthly_save
        return m if m <= horizon else None
    f = (1.0 + growth_pct / 100.0) ** (1.0 / 12.0)
    k = monthly_save / (f - 1.0)
    if liquid + k <= 0:        # savings drain faster than growth can lift
        return None
    m = math.log((target + k) / (liquid + k)) / math.log(f)
    return m if 0 <= m <= horizon else None


def _years_or_none(months: float | None) -> float | None:
    return None if months is None else round(months / 12.0, 1)


def whatif_grid(liquid: float, baseline: Baseline, monthly_save: float,
                today: date) -> dict:
    """The full precomputed scenario space for the "Play with it" dials.
    Python owns every dollar figure; the page's JS only indexes this grid.
    Display strings ride pre-formatted; numbers exist solely for chart
    geometry (markLines, paths, milestone dots). The spend dial centers on
    the TRUE BURN (mean of the baseline window) — the ledger's own answer,
    not a hand-set guess. Per cell, three arrival horizons: `years` (saving
    as usual), `coast` (growth alone, $0 more saved — the coast stat), and
    `half` (when the saving path crosses the halfway mark)."""
    annual = baseline.burn * 12
    step = WHATIF_SPEND_STEP
    lo = max(step, int(round(annual * 0.5 / step)) * step)
    hi = max(lo + step, int(round(annual * 1.5 / step)) * step)
    spends = list(range(lo, hi + 1, step))

    target_n, target_s, gap_s, pct_s = [], [], [], []
    half_n, half_s = [], []
    for r in WHATIF_RATES:
        tn_row, ts_row, gap_row, pct_row = [], [], [], []
        hn_row, hs_row = [], []
        for sp in spends:
            t = round1k(sp / (r / 100.0))
            tn_row.append(t)
            ts_row.append("≈" + m0(t))
            hn_row.append(t / 2.0)
            hs_row.append("≈" + m0(t / 2.0))
            gap = t - liquid
            gap_row.append(("past it by " + m0(-gap)) if gap <= 0
                           else (m0(gap) + " to go"))
            pct_row.append(f"{min(999.0, 100.0 * liquid / t):.0f}%")
        target_n.append(tn_row)
        target_s.append(ts_row)
        half_n.append(hn_row)
        half_s.append(hs_row)
        gap_s.append(gap_row)
        pct_s.append(pct_row)

    # per-cell horizons, all closed-form: saving as usual, growth alone
    # ($0 more saved = the coast stat), and the halfway crossing
    years, coast, half = [], [], []
    for ri in range(len(WHATIF_RATES)):
        y_r, c_r, h_r = [], [], []
        for si in range(len(spends)):
            t = target_n[ri][si]
            y_r.append([_years_or_none(
                _months_to_target(liquid, monthly_save, t, g))
                for g in WHATIF_GROWTHS])
            c_r.append([_years_or_none(
                _months_to_target(liquid, 0.0, t, g))
                for g in WHATIF_GROWTHS])
            h_r.append([_years_or_none(
                _months_to_target(liquid, monthly_save, t / 2.0, g))
                for g in WHATIF_GROWTHS])
        years.append(y_r)
        coast.append(c_r)
        half.append(h_r)

    # projected balance paths, one per growth setting, yearly points —
    # the saving-as-usual path and its growth-alone ($0 saved) twin
    paths, paths0, path_tips, ymaps = [], [], [], []
    global_target_max = max(max(row) for row in target_n)
    for g in WHATIF_GROWTHS:
        f = (1.0 + g / 100.0) ** (1.0 / 12.0)
        pts, pts0, tips = [], [], []
        for yr in range(WHATIF_MAX_YEARS + 1):
            m = yr * 12
            if g <= 0:
                bal, bal0 = liquid + monthly_save * m, liquid
            else:
                grown = f ** m
                bal = liquid * grown + monthly_save * (grown - 1) / (f - 1)
                bal0 = liquid * grown
            bal, bal0 = max(0.0, round(bal)), max(0.0, round(bal0))
            pts.append([yr, bal])
            pts0.append([yr, bal0])
            tips.append({"t": "today" if yr == 0 else f"in {yr} years",
                         "rows": [["≈" + m0(bal), "saving as usual"],
                                  ["≈" + m0(bal0), "growth alone, $0 saved"]]})
        paths.append(pts)
        paths0.append(pts0)
        path_tips.append(tips)
        ymaps.append(yaxis_payload(
            0, max(pts[-1][1], pts0[-1][1], global_target_max)))

    xticks = {str(y): ("today" if y == 0 else f"{y} yrs")
              for y in range(0, WHATIF_MAX_YEARS + 1, 10)}
    # calendar-year labels for the milestone dots: offset -> "2041" / "’41"
    xyear = {str(y): str(today.year + y)
             for y in range(WHATIF_MAX_YEARS + 1)}
    xyear_s = {k: "’" + v[2:] for k, v in xyear.items()}
    nearest_si = min(range(len(spends)), key=lambda i: abs(spends[i] - annual))
    return {
        "rates": [f"{r:.1f}%" for r in WHATIF_RATES],
        "spends": [f"{m0(sp)}/yr" for sp in spends],
        "growths": [f"{g:.1f}% real" for g in WHATIF_GROWTHS],
        "nRates": len(WHATIF_RATES), "nSpends": len(spends),
        "nGrowths": len(WHATIF_GROWTHS),
        "targetN": target_n, "target": target_s,
        "halfN": half_n, "halfS": half_s,
        "gap": gap_s, "pct": pct_s,
        "years": years, "coast": coast, "half": half,
        "paths": paths, "paths0": paths0, "pathTips": path_tips,
        "ymaps": ymaps, "xticks": xticks, "xyear": xyear, "xyearS": xyear_s,
        "maxYears": WHATIF_MAX_YEARS,
        "def": {"ri": WHATIF_RATES.index(4.0), "si": nearest_si,
                "gi": WHATIF_GROWTHS.index(4.0)},
    }


def kid_name(account: str) -> str:
    leaf = account.split(":")[-1]
    name = re.sub(r"529", "", leaf).strip(":-_ ")
    return name or "Education"


def education_accounts() -> list[EduAccount]:
    """Every Assets account whose name carries '529'. Valued at market when
    a price exists, else at cost — and labelled when it is."""
    rows = query("SELECT account, sum(convert(position,'USD')) AS usd, "
                 "sum(cost(position)) AS cost WHERE account ~ '^Assets' "
                 "AND account ~ '529' GROUP BY account ORDER BY account")
    out = []
    for r in rows:
        usd = amount(r["usd"], "USD")
        cost = amount(r["cost"], "USD")
        if abs(usd) >= 0.005:
            out.append(EduAccount(r["account"], kid_name(r["account"]), usd, False))
        elif abs(cost) >= 0.005:
            out.append(EduAccount(r["account"], kid_name(r["account"]), cost, True))
    return out


def education_pace(accounts: list[EduAccount]) -> float | None:
    """Median monthly 529 contribution over the last 6 contribution months.
    Prefers the Equity:*529* pass-through convention; falls back to
    cost-basis inflows on the 529 asset accounts (skipping each account's
    opening-snapshot month)."""
    rows = query("SELECT year, month, sum(convert(position,'USD')) AS v "
                 "WHERE account ~ '^Equity' AND account ~ '529' "
                 "GROUP BY year, month ORDER BY year, month")
    monthly = [abs(amount(r["v"])) for r in rows if abs(amount(r["v"])) >= 1]
    if not monthly:
        for a in accounts:
            acct = a.account.replace("'", "''")
            rows = query(f"SELECT year, month, sum(cost(position)) AS v "
                         f"WHERE account = '{acct}' GROUP BY year, month "
                         f"ORDER BY year, month")
            vals = [amount(r["v"]) for r in rows]
            monthly += [v for v in vals[1:] if v >= 1]  # [0] = opening snapshot
    return median(monthly[-6:]) if monthly else None


def project_envelopes(goals: dict) -> list[Envelope]:
    """Tagged Expenses spending, joined to facts/goals project_budget_* keys.
    A transaction carrying several tags counts toward each of them."""
    rows = query("SELECT str(tags) AS t, sum(convert(position,'USD')) AS v "
                 "WHERE account ~ '^Expenses' GROUP BY t")
    spent: dict[str, float] = {}
    for r in rows:
        for tag in re.findall(r"'([^']+)'", r["t"] or ""):
            spent[tag] = spent.get(tag, 0.0) + amount(r["v"])
    return [Envelope(tag, v,
                     _as_float(goals.get(f"project_budget_{tag.replace('-', '_')}")))
            for tag, v in sorted(spent.items(), key=lambda kv: -kv[1])]


def findings_date() -> str | None:
    """The `_Generated YYYY-MM-DD` stamp inside findings.md, for the header."""
    p = REPORTS / "findings.md"
    if not p.exists():
        return None
    m = re.search(r"_Generated (\d{4}-\d{2}-\d{2}) ", p.read_text())
    return m.group(1) if m else None


def needs_you(today: date) -> tuple[list[Card], int, str]:
    """Top verb cards: alerts first, then near deadlines, then watches.
    Returns (cards, more_count, state); state 'none' = checks never ran,
    'ok' = ran and nothing needs a human, 'cards' = the list below."""
    findings, _, errors = parse_findings()
    if findings is None:
        return [], 0, "none"
    queue = action_queue(findings)
    try:
        horizon = int(goals_config().get("deadline_horizon_days") or 45)
    except (TypeError, ValueError):
        horizon = 45
    deadlines = deadline_items(today, horizon)

    cards = [Card("alert", f["fix"] or f["title"], f["title"], "alert")
             for f in queue if f["severity"] == "alert"]
    for dl in sorted(deadlines, key=lambda d: d["days"]):
        if dl["days"] <= DEADLINE_CARD_DAYS:
            when = "today" if dl["days"] == 0 else (
                "tomorrow" if dl["days"] == 1 else f"in {dl['days']} days")
            cards.append(Card("deadline", dl["text"],
                              f"due {dl['date'].strftime('%a %b %-d')} · {when}",
                              "deadline"))
    cards += [Card("watch", f["fix"] or f["title"], f["title"], "watch")
              for f in queue if f["severity"] == "watch"]
    cards += [Card("watch", "A check needs fixing before it can watch for you.",
                   e, "check error") for e in errors]
    far_deadlines = sum(1 for d in deadlines if d["days"] > DEADLINE_CARD_DAYS)
    shown = cards[:NEEDS_CARDS]
    more = len(cards) - len(shown) + far_deadlines
    return shown, more, ("cards" if shown else "ok")


# ---------------------------------------------------- money that must move
MUSTMOVE_AMT = re.compile(r"([~≈]?)\$(\d[\d,]*(?:\.\d+)?)\s*([KkMm]?)(?![\w.])")
_AMT_MULT = {"": 1, "k": 1_000, "m": 1_000_000}


def must_move(today: date) -> list[dict]:
    """Dated facts bullets inside MUSTMOVE_DAYS that carry a real dollar
    figure — the obligations calendar. Nothing is invented: no date + amount
    in the vault, no chip. The first $ figure in the bullet is the chip's
    number; `~`, `≈`, or a K/M suffix keeps its ≈."""
    seen, out = set(), []
    for d, text, _relpath in dated_bullets():
        days = (d - today).days
        if not (0 <= days <= MUSTMOVE_DAYS):
            continue
        m = MUSTMOVE_AMT.search(text)
        if not m:
            continue
        key = (d, " ".join(text.lower().split())[:80])
        if key in seen:
            continue
        seen.add(key)
        val = float(m.group(2).replace(",", "")) * _AMT_MULT[m.group(3).lower()]
        approx = bool(m.group(1)) or bool(m.group(3))
        when = ("today" if days == 0 else
                "tomorrow" if days == 1 else f"in {days} days")
        out.append({"date": d, "days": days, "when": when,
                    "day_lbl": mon_d(d), "near": days <= DEADLINE_CARD_DAYS,
                    "amt": ("≈" if approx else "") + m0(val), "text": text})
    out.sort(key=lambda r: r["date"])
    return out[:MUSTMOVE_MAX]


# --------------------------------------------------------------- Sara's wins
WIN_LINE = re.compile(r"^\s*[-*]\s*\[[xX]\]\s*(.+)$", re.M)
WIN_REALIZED = re.compile(
    r"realized[^$\n]{0,24}\$(\d[\d,]*(?:\.\d+)?)\s*([Kk]?)\s*(/\s*yr|per\s+year)?",
    re.I)


def saras_wins(today: date) -> dict | None:
    """Realized savings/found money, parsed CONSERVATIVELY from this year's
    dated notes: only `[x]` checklist lines that say `realized $N` count
    (the savings-hunt log convention — estimates and captures never blend).
    Returns {total, items:[{label, amt, peryr}]} or None when nothing
    parses — no fake trophies."""
    notes = VAULT / "notes"
    if not notes.is_dir():
        return None
    items = []
    for f in sorted(notes.glob("*.md")):
        m = re.match(r"(\d{4})-\d{2}-\d{2}", f.name)
        if not m or int(m.group(1)) != today.year:
            continue
        try:
            txt = f.read_text()
        except OSError:
            continue
        for line_m in WIN_LINE.finditer(txt):
            line = line_m.group(1)
            rm = WIN_REALIZED.search(line)
            if not rm:
                continue
            val = float(rm.group(1).replace(",", "")) * (1000 if rm.group(2) else 1)
            label = re.sub(r"\s+", " ", line[:rm.start()]).strip(" -—·:;,(")
            items.append({"label": label[:72] or "Realized saving",
                          "amt": val, "peryr": bool(rm.group(3))})
    if not items:
        return None
    items.sort(key=lambda i: -i["amt"])
    return {"total": sum(i["amt"] for i in items), "items": items}


SMALL_NUMS = ["zero", "One", "Two", "Three", "Four", "Five", "Six",
              "Seven", "Eight", "Nine"]


def sara_line(pace: Pace, cards_state: str, cards: list[Card], more: int) -> str:
    """One warm, honest sentence for the hero. States, never invents."""
    over = (pace.pace_delta or 0) > 0.10 * (pace.typical or float("inf"))
    if cards_state == "none":
        return "First morning here — run the checks and I'll start watching for you."
    n_alerts = sum(1 for c in cards if c.kind == "alert")
    if n_alerts:
        n_lbl = SMALL_NUMS[n_alerts] if n_alerts < 10 else str(n_alerts)
        verb = "wants" if n_alerts == 1 else "want"
        thing = "thing" if n_alerts == 1 else "things"
        return (f"{n_lbl} {thing} {verb} a decision this morning — start at "
                f"the top of the list; the rest keeps.")
    if cards:
        n = len(cards) + more
        s = "s" if n != 1 else ""
        lead = "Spending is running hot, and a" if over else "A"
        return (f"{lead} few small thing{s} below could use your hands — "
                f"none of it is urgent.")
    if pace.typical is None:
        return "All quiet. A few more months of history and I can show your typical pace."
    if over:
        return ("Nothing needs your hands — but spending is running ahead of "
                "typical. The line below tells it.")
    return "All quiet. Spending is on pace, and nothing needs your hands today."


# --------------------------------------------------------------- rendering
def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _asset(name: str, what: str) -> Path:
    p = ASSETS / name
    if not p.exists():
        raise SystemExit(f"{what} missing at {p} — the repo ships it; "
                         "re-clone or restore tools/assets/.")
    return p


def _codespans(text: str) -> Markup:
    """Escape, then `x` -> <code>x</code> — findings text is untrusted."""
    return Markup(code_spans(str(escape(text))))


_ENV = jinja2.Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)
_ENV.filters["codespans"] = _codespans

# The aurora system. Light = white cards floating over the 115° gradient
# band with soft violet shadows; dark = the same layout on a deep-navy
# canvas, the aurora kept but deepened, cards #131a2e. Declared twice so
# the OS preference and the explicit toggle both win in their own direction.
CSS_TEMPLATE = """
@font-face { font-family:'Inter'; font-style:normal; font-weight:400;
  src:url(data:font/woff2;base64,__INTER_R__) format('woff2'); }
@font-face { font-family:'Inter'; font-style:normal; font-weight:600;
  src:url(data:font/woff2;base64,__INTER_S__) format('woff2'); }

:root { color-scheme:light;
  --bg:#f6f6fa; --surface:#ffffff; --surface-2:#f1f0f8;
  --border:rgba(49,42,124,.10); --border-strong:rgba(49,42,124,.22);
  --ink:#151329; --ink-2:#4c4a63; --muted:#6e6c85;
  --grid:#eceaf4; --axis:#cfccdf;
  --accent:#6157ff; --accent-soft:rgba(97,87,255,.10); --link:#4d43e0;
  --pos:#067647; --pos-soft:#e7f9ef;
  --neg:#d02b4c; --neg-soft:#fdedf0; --neg-dot:#f43f5e;
  --warn:#9a5b00; --warn-soft:#fdf3e0; --warn-dot:#f59e0b;
  --edu-track:#d2ecea;
  --edu-fill:linear-gradient(90deg,#0d9488,#2bbcab);
  --ideal:#8d87b8; --code:#efedf8;
  --rib-1:#6157ff; --rib-2:#9d8cff; --rib-3:#d97a06; --rib-4:#8579c9;
  --hero-grad:linear-gradient(115deg,#6157ff 0%,#74c0fc 35%,#ff7eb6 68%,#ffb86b 100%);
  --hero-wash:radial-gradient(120% 90% at 15% 0%,rgba(255,255,255,.30),transparent 55%);
  --hero-scrim:linear-gradient(180deg,rgba(30,22,96,.34),rgba(30,22,96,.10) 62%,rgba(30,22,96,0));
  --walk-grad:linear-gradient(180deg,#ffffff,#f6f5ff);
  --walk-border:#e6e3fb;
  --bar-track:#ece9f8; --bar-fill:linear-gradient(90deg,#6157ff,#9d8cff);
  --shadow-card:0 1px 2px rgba(49,42,124,.05),0 8px 24px rgba(49,42,124,.07);
  --shadow-float:0 12px 32px rgba(49,42,124,.16);
}
@media screen and (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { color-scheme:dark;
    --bg:#0a0e1e; --surface:#151d33; --surface-2:#1d2540;
    --border:rgba(196,200,255,.13); --border-strong:rgba(196,200,255,.26);
    --ink:#e9ebfa; --ink-2:#b3b8d6; --muted:#8b91b2;
    --grid:#232c47; --axis:#39415f;
    --accent:#8f88ff; --accent-soft:rgba(143,136,255,.16); --link:#a9a3ff;
    --pos:#4ec17e; --pos-soft:rgba(78,193,126,.12);
    --neg:#ff6b84; --neg-soft:rgba(255,107,132,.12); --neg-dot:#ff6b84;
    --warn:#d2a041; --warn-soft:rgba(210,160,65,.13); --warn-dot:#d2a041;
    --edu-track:rgba(43,188,171,.16);
    --edu-fill:linear-gradient(90deg,#0f9c8d,#2bbcab);
    --ideal:#767fa8; --code:#1c2440;
    --rib-1:#544ae4; --rib-2:#8d80f4; --rib-3:#c97e1e; --rib-4:#8074c4;
    --hero-grad:linear-gradient(115deg,#4a41d6 0%,#3c7ec2 35%,#d4548c 68%,#d98b3f 100%);
    --hero-wash:radial-gradient(120% 90% at 15% 0%,rgba(255,255,255,.12),transparent 55%);
    --hero-scrim:linear-gradient(180deg,rgba(6,8,30,.42),rgba(6,8,30,.16) 62%,rgba(6,8,30,0));
    --walk-grad:linear-gradient(180deg,#161d36,#181d3e);
    --walk-border:#2c3158;
    --bar-track:#242b4c; --bar-fill:linear-gradient(90deg,#6a61f0,#9d8cff);
    --shadow-card:0 1px 2px rgba(0,0,0,.3),0 10px 28px rgba(0,0,0,.34);
    --shadow-float:0 14px 36px rgba(0,0,0,.5);
  }
}
:root[data-theme="dark"] { color-scheme:dark;
  --bg:#0a0e1e; --surface:#131a2e; --surface-2:#1a2238;
  --border:rgba(196,200,255,.10); --border-strong:rgba(196,200,255,.24);
  --ink:#e9ebfa; --ink-2:#b3b8d6; --muted:#8b91b2;
  --grid:#222b45; --axis:#39415f;
  --accent:#8f88ff; --accent-soft:rgba(143,136,255,.16); --link:#a9a3ff;
  --pos:#3ecf7a; --pos-soft:rgba(62,207,122,.13);
  --neg:#ff6b84; --neg-soft:rgba(255,107,132,.12); --neg-dot:#ff6b84;
  --warn:#f0b13c; --warn-soft:rgba(240,177,60,.13); --warn-dot:#f0b13c;
  --gold:#e2a04b; --gold-track:rgba(226,154,61,.18);
  --gold-fill:linear-gradient(90deg,#c97e1e,#e2a04b);
  --ideal:#767fa8; --code:#1c2440;
  --rib-1:#544ae4; --rib-2:#8d80f4; --rib-3:#c97e1e; --rib-4:#8074c4;
  --hero-grad:linear-gradient(115deg,#4a41d6 0%,#3c7ec2 35%,#d4548c 68%,#d98b3f 100%);
  --hero-wash:radial-gradient(120% 90% at 15% 0%,rgba(255,255,255,.12),transparent 55%);
  --hero-scrim:linear-gradient(180deg,rgba(6,8,30,.42),rgba(6,8,30,.16) 62%,rgba(6,8,30,0));
  --walk-grad:linear-gradient(180deg,#161d36,#181d3e);
  --walk-border:#2c3158;
  --bar-track:#242b4c; --bar-fill:linear-gradient(90deg,#6a61f0,#9d8cff);
  --shadow-card:0 1px 2px rgba(0,0,0,.3),0 10px 28px rgba(0,0,0,.34);
  --shadow-float:0 14px 36px rgba(0,0,0,.5);
}

* { box-sizing:border-box; margin:0; }
html { -webkit-text-size-adjust:100%; }
body { background:var(--bg); color:var(--ink);
  font:15px/1.5 'Inter',system-ui,-apple-system,'Segoe UI',sans-serif; }
.wrap { max-width:1200px; margin:0 auto; padding:0 28px; }
a { color:var(--link); text-decoration-thickness:1px; text-underline-offset:2px; }
code { background:var(--code); border-radius:4px; padding:.08em .35em;
  font-size:.9em; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
:focus-visible { outline:2px solid var(--accent); outline-offset:2px; border-radius:4px; }
/* tabular numerals wherever figures align or sit inline; the two biggest
   heroes (the pace card's delta, the walk-away number) keep Inter's
   proportional digits — large standalone numbers set tighter that way */
.num, td, .tick { font-variant-numeric:tabular-nums lining-nums; }
.pos { color:var(--pos); } .neg { color:var(--neg); }

/* ---- the aurora hero band ---- */
.hero { background:var(--hero-grad); color:#fff; position:relative;
  padding:30px 0 96px; }
.hero::before { content:""; position:absolute; inset:0;
  background:var(--hero-wash); pointer-events:none; }
.hero::after { content:""; position:absolute; inset:0;
  background:var(--hero-scrim); pointer-events:none; }
.hero-in { position:relative; z-index:1; }
.hero-top { display:flex; justify-content:space-between; align-items:flex-start;
  gap:20px; }
.hi { font-size:13px; font-weight:600; letter-spacing:.05em;
  text-transform:uppercase; text-shadow:0 1px 2px rgba(21,15,74,.30); }
.say { margin-top:8px; font-size:18px; line-height:1.45; font-weight:600;
  max-width:62ch; letter-spacing:-.005em; text-wrap:balance;
  text-shadow:0 1px 3px rgba(21,15,74,.30); }
.hero-side { display:flex; align-items:center; gap:12px; flex:none; }
.stamp { font-size:11.5px; line-height:1.5; color:#fff; text-align:right;
  background:rgba(21,15,74,.50); padding:5px 12px; border-radius:10px; }
.themebtn { background:rgba(21,15,74,.45); color:#fff;
  border:1px solid rgba(255,255,255,.55); border-radius:999px; padding:3px 12px;
  font:12.5px 'Inter',system-ui,sans-serif; cursor:pointer;
  transition:background .15s ease; }
.themebtn:hover { background:rgba(21,15,74,.60); }
.card .themebtn { background:var(--surface);
  color:var(--ink-2); border-color:var(--border); }
.card .themebtn:hover {
  border-color:var(--border-strong); background:var(--surface); }

/* ---- the floating KPI strip ---- */
.kpis { display:grid; grid-template-columns:repeat(4,1fr); background:var(--surface);
  border-radius:14px; box-shadow:var(--shadow-float); border:1px solid var(--border);
  margin-top:-64px; position:relative; z-index:2; padding:18px 0; }
.kpi { padding:2px 24px; border-left:1px solid var(--grid); min-width:0; }
.kpi:first-child { border-left:none; }
.kk { font-size:11.5px; font-weight:600; color:var(--muted);
  text-transform:uppercase; letter-spacing:.05em; white-space:nowrap; }
.kv { font-size:31px; font-weight:600; letter-spacing:-.02em; line-height:1.15;
  margin-top:3px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.ks { font-size:12px; color:var(--muted); margin-top:2px; }

/* ---- cards + grids ---- */
main { padding-bottom:44px; }
/* start, not stretch: a short card (the machine) self-sizes instead of
   dragging dead space to match its tall neighbor */
.grid { display:grid; gap:18px; margin-top:18px; align-items:start; }
.g-pace { grid-template-columns:1.5fr 1fr; }
.g-needs { grid-template-columns:1.5fr 1fr; }
.g-walk { grid-template-columns:1.4fr 1fr; }
.g-chesh { grid-template-columns:1.4fr 1fr; }
.g-solo { grid-template-columns:1fr; }
.sidecol { display:flex; flex-direction:column; gap:18px; min-width:0;
  align-self:start; }
.card { background:var(--surface); border:1px solid var(--border);
  border-radius:14px; padding:20px 22px; box-shadow:var(--shadow-card);
  break-inside:avoid; min-width:0; }
.card.walk { background:var(--walk-grad); border-color:var(--walk-border); }
.cardhead { display:flex; justify-content:space-between; align-items:baseline;
  gap:14px; margin-bottom:12px; }
.ck { font-size:11.5px; font-weight:600; color:var(--muted);
  text-transform:uppercase; letter-spacing:.05em; }
.card .sub { color:var(--muted); font-size:12.5px; margin-top:3px;
  text-transform:none; letter-spacing:0; font-weight:400; }
.window { color:var(--muted); font-size:12px; white-space:nowrap; flex:none; }

/* hero figures inside cards */
.phero { font-size:40px; font-weight:600; letter-spacing:-.02em; line-height:1.08; }
.phero.good { color:var(--pos); } .phero.bad { color:var(--neg); }
.whero { font-size:32px; font-weight:600; letter-spacing:-.02em; line-height:1.1; }
.whero .of, .heromini .of { color:var(--muted); font-weight:600; font-size:14px; }
.heromini { font-size:26px; font-weight:600; letter-spacing:-.015em; line-height:1.15; }
.herolab { color:var(--ink-2); font-size:13px; margin-top:4px; }
.chiprow { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
.chip { display:inline-flex; align-items:baseline; gap:6px; border-radius:999px;
  padding:4px 12px; font-size:12px; font-weight:400; color:var(--ink-2);
  background:var(--surface-2); }
.chip b { font-weight:600; font-variant-numeric:tabular-nums; }
.chip.good { background:var(--pos-soft); color:var(--pos); }
.chip.good b { color:var(--pos); }
.chip.bad { background:var(--neg-soft); color:var(--neg); }
.chip.bad b { color:var(--neg); }

/* chart chrome */
.chart { width:100%; margin-top:6px; }
#pace-chart { height:280px; } #nw-chart { height:300px; } #wi-chart { height:240px; }

/* ---- the machine (lanes) ---- */
.lanes { list-style:none; padding:0; margin:2px 0 0; }
.lanes li { display:flex; gap:11px; padding:11px 0; align-items:flex-start;
  border-top:1px solid var(--grid); }
.lanes li:first-child { border-top:none; padding-top:5px; }
.lanedot { width:8px; height:8px; border-radius:50%; flex:none; margin-top:6px; }
.lanedot.ok { background:var(--pos); }
.lanedot.watch { background:var(--warn-dot); }
.lanedot.bad { background:var(--neg-dot); }
.lanedot.mut { background:var(--axis); }
.lanes .lname { font-size:13.5px; font-weight:600; line-height:1.35; }
.lanes .ldet { color:var(--ink-2); font-size:12.5px; margin-top:1px; }
.lanes .ldet.bad { color:var(--neg); }
.lanetag { margin-left:auto; color:var(--muted); font-size:10px;
  text-transform:uppercase; letter-spacing:.06em; flex:none; padding-top:4px; }
.lanesum { color:var(--muted); font-size:12.5px; margin-top:10px;
  padding-top:10px; border-top:1px solid var(--grid); max-width:68ch; }

/* ---- needs-you + money that must move ---- */
.needs { list-style:none; padding:0; margin:2px 0 0; }
.needs li { display:flex; gap:11px; padding:11px 0; align-items:flex-start;
  border-top:1px solid var(--grid); }
.needs li:first-child { border-top:none; padding-top:5px; }
.sevdot { width:8px; height:8px; border-radius:50%; flex:none; margin-top:7px; }
.sevdot.alert { background:var(--neg-dot); } .sevdot.watch { background:var(--warn-dot); }
.sevdot.deadline { background:var(--accent); }
.needs .verb { font-size:13.5px; font-weight:600; line-height:1.35; }
.needs .why { color:var(--ink-2); font-size:12.5px; margin-top:2px; }
.needs .meta { margin-left:auto; color:var(--muted); font-size:10px;
  text-transform:uppercase; letter-spacing:.06em; flex:none; padding-top:4px; }
.allclear { display:flex; gap:12px; align-items:center; padding:14px;
  background:var(--pos-soft); border-radius:10px; margin-top:8px; }
.allclear b { font-size:14px; font-weight:600; }
.allclear .s { color:var(--ink-2); font-size:12.5px; }
.morelink { color:var(--muted); font-size:12.5px; margin-top:10px; }
.moves { list-style:none; padding:0; margin:2px 0 0; }
.moves li { display:flex; gap:11px; padding:11px 0; align-items:baseline;
  border-top:1px solid var(--grid); }
.moves li:first-child { border-top:none; padding-top:5px; }
.mvdate { flex:none; font-size:12px; font-weight:600; color:var(--ink-2);
  min-width:52px; }
.mvdate.near { color:var(--warn); }
.mvamt { flex:none; font-size:13.5px; font-weight:600; }
.mvtext { color:var(--ink-2); font-size:12.5px; min-width:0; }

/* progress bars — track is a lighter step of the fill's own ramp */
.barwrap { position:relative; margin-top:14px; }
.track { height:10px; border-radius:999px; background:var(--bar-track); overflow:hidden; }
.fill { display:block; height:100%; border-radius:999px; background:var(--bar-fill); }
.fill.over { background:var(--neg); }
.fill.edu { background:var(--edu-fill); }
.track.edu { background:var(--edu-track); }
.shadowtick { position:absolute; top:-3px; width:2px; height:16px;
  background:var(--muted); border-radius:1px; }
.barnotes { display:flex; justify-content:space-between; gap:10px; margin-top:7px;
  color:var(--muted); font-size:12px; }
.shadownote { color:var(--muted); font-size:12.5px; margin-top:10px;
  max-width:68ch; }
.shadownote b { color:var(--ink-2); font-weight:600; font-variant-numeric:tabular-nums; }
.goalfoot { color:var(--muted); font-size:12px; margin-top:12px;
  padding-top:10px; border-top:1px solid var(--grid); max-width:68ch;
  text-wrap:pretty; }
.card.walk .goalfoot, .card.walk .wi-cut, .card.walk .wi-fold,
.card.walk .wi-how { border-top-color:var(--walk-border); }
.nudge { background:var(--warn-soft); border-radius:10px; padding:10px 14px;
  color:var(--ink-2); font-size:13px; margin-top:12px; }

/* what-if dials (inside the walk card): ONE promoted output (the years),
   everything else quiet; the dials + chart live behind a disclosure */
.wi-cut { margin-top:16px; padding-top:14px; border-top:1px solid var(--grid); }
.wv-big { font-size:30px; font-weight:600; letter-spacing:-.02em; line-height:1.1; }
.coastline { color:var(--ink-2); font-size:12.5px; margin-top:8px;
  max-width:68ch; }
.wi-quiet { color:var(--muted); font-size:12.5px; margin-top:4px; }
.wi-quiet b { color:var(--ink-2); font-weight:600; }
.wi-fold, .wi-how { margin-top:14px; padding-top:12px;
  border-top:1px solid var(--grid); }
.wi-fold > summary, .wi-how > summary { cursor:pointer; color:var(--muted); }
.wi-fold > summary:hover, .wi-how > summary:hover { color:var(--ink-2); }
.wi-how > summary { font-size:12.5px; }
.dials { display:grid; gap:8px 26px; grid-template-columns:repeat(3,1fr); margin-top:12px; }
.dial { display:grid; grid-template-columns:1fr auto; gap:0 10px; align-items:center; }
.dial label { color:var(--ink-2); font-size:12.5px; }
.dial .dval { font-size:13px; font-weight:600; text-align:right; }
.dial input[type=range] { grid-column:1 / -1; width:100%; height:22px; margin:0;
  accent-color:var(--accent); }
.wi-actions { display:flex; justify-content:flex-end; margin-top:8px; }
.wi-formula { color:var(--muted); font-size:12px; margin-top:10px;
  max-width:68ch; }

/* stats (cheshbon) */
.statrow { display:flex; gap:28px; flex-wrap:wrap; margin-top:10px;
  align-items:flex-end; }
.stat .v { font-size:22px; font-weight:600; letter-spacing:-.01em; }
.stat .l { color:var(--muted); font-size:12px; margin-bottom:2px; }
/* the category ribbon: 2px surface gaps between segments do the separating */
.ribbon { display:flex; gap:2px; height:12px; border-radius:999px; overflow:hidden;
  margin-top:8px; }
.ribbon .seg { min-width:8px; }
.seg.rib-1 { background:var(--rib-1); } .seg.rib-2 { background:var(--rib-2); }
.seg.rib-3 { background:var(--rib-3); } .seg.rib-4 { background:var(--rib-4); }
.riblegend { display:flex; flex-wrap:wrap; gap:6px 16px; margin-top:16px; }
.riblegend .li { display:inline-flex; align-items:baseline; gap:6px;
  font-size:12px; color:var(--ink-2); }
.riblegend .sw { width:9px; height:9px; border-radius:3px; flex:none;
  align-self:center; }
.sw.rib-1 { background:var(--rib-1); } .sw.rib-2 { background:var(--rib-2); }
.sw.rib-3 { background:var(--rib-3); } .sw.rib-4 { background:var(--rib-4); }
.riblegend b { font-weight:600; font-variant-numeric:tabular-nums; }

/* wins */
.winhero { font-size:26px; font-weight:600; letter-spacing:-.015em; margin-top:2px; }
.winhero .of { color:var(--muted); font-weight:600; font-size:13px; }
.wins-list { margin:10px 0 0; padding:0; list-style:none; }
.wins-list li { display:flex; justify-content:space-between; gap:12px;
  padding:7px 0; border-top:1px solid var(--grid); font-size:12.5px;
  color:var(--ink-2); }
.wins-list b { font-weight:600; font-variant-numeric:tabular-nums;
  color:var(--ink); white-space:nowrap; }

/* envelope + per-kid rows */
.envrow { display:grid; grid-template-columns:minmax(96px,max-content) 1fr auto; gap:14px;
  align-items:center; padding:10px 0; border-top:1px solid var(--grid); }
.envrow:first-of-type { border-top:none; }
.envrow .name { font-size:13.5px; font-weight:600; max-width:240px; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.envrow .amt { font-size:12.5px; color:var(--ink-2); font-variant-numeric:tabular-nums;
  white-space:nowrap; }
.envrow .track { margin:0; height:8px; }

/* table twins + empty states */
.tv { margin-top:12px; }
.tv summary { color:var(--muted); font-size:12.5px; cursor:pointer; }
.tv table { border-collapse:collapse; margin-top:8px; font-size:12.5px; width:100%; }
.tv caption { text-align:left; color:var(--muted); font-size:11.5px; margin-bottom:6px; }
.tv th { text-align:left; color:var(--ink-2); font-weight:600;
  border-bottom:1px solid var(--axis); padding:4px 10px 4px 0; }
.tv td { border-bottom:1px solid var(--grid); padding:4px 10px 4px 0; }
.empty { color:var(--muted); font-size:13.5px; padding:12px 0; }
.empty b { color:var(--ink-2); font-weight:600; }

footer { margin-top:28px; padding-top:16px; border-top:1px solid var(--border);
  color:var(--muted); font-size:12.5px; text-align:center; }
footer p { max-width:68ch; margin-inline:auto; text-wrap:pretty; }
footer p + p { margin-top:4px; }
footer .tagline { color:var(--ink-2); }

@media (max-width:960px) {
  .g-pace, .g-needs, .g-walk, .g-chesh { grid-template-columns:1fr; }
  .kpis { grid-template-columns:1fr 1fr; row-gap:14px; }
  .kpi:nth-child(3) { border-left:none; }
  .hero-top { flex-direction:column; }
  .hero-side { align-self:flex-start; flex-direction:row-reverse; }
  .stamp { text-align:left; }
}
@media (max-width:560px) {
  .wrap { padding:0 16px; }
  .hero { padding-top:22px; }
  .kpis { grid-template-columns:1fr 1fr; padding:12px 0; }
  .kpi { padding:2px 16px; }
  .kv { font-size:24px; }
  .phero { font-size:33px; }
  .statrow { gap:18px; }
  .dials { grid-template-columns:1fr; }
  /* the lane tag column steals a third of a phone row — drop it to an
     inline chip under the lane's name instead */
  .lanes li { flex-wrap:wrap; }
  .lanes li > div { flex:1; min-width:72%; }
  .lanetag { order:4; width:max-content; margin-left:19px; padding:2px 9px;
    background:var(--surface-2); border-radius:999px; }
}
@media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
@media print {
  body { background:#fff; }
  .themebtn, .tv { display:none; }
  .card, .kpis { border-color:#ddd; box-shadow:none; }
  .wrap { max-width:none; padding:0; }
  * { print-color-adjust:exact; -webkit-print-color-adjust:exact; }
}
"""

JS_TEMPLATE = """
(function () {
  'use strict';
  var DATA = JSON.parse(document.getElementById('sara-data').textContent);
  var FONT = "'Inter',system-ui,-apple-system,'Segoe UI',sans-serif";
  function esc(s) {  // every string entering tooltip HTML passes through here
    return String(s).replace(/[&<>"']/g, function (c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }
  function cssv(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  // theme: auto -> light -> dark, persisted; charts re-skin on every change
  var root = document.documentElement;
  var btn = document.getElementById('themebtn');
  var order = ['auto', 'light', 'dark'];
  var theme = 'auto';
  try { theme = localStorage.getItem('sara-home-theme') || 'auto'; } catch (e) {}
  if (order.indexOf(theme) < 0) theme = 'auto';
  function applyTheme(t) {
    if (t === 'auto') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', t);
    if (btn) btn.textContent = t === 'auto' ? '\\u25d1 auto'
      : (t === 'light' ? '\\u2600 light' : '\\u263e dark');
    try { localStorage.setItem('sara-home-theme', t); } catch (e) {}
    buildCharts();
  }
  if (btn) btn.addEventListener('click', function () {
    theme = order[(order.indexOf(theme) + 1) % order.length];
    applyTheme(theme);
  });
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)')
      .addEventListener('change', function () { if (theme === 'auto') buildCharts(); });
  }

  function tipHtml(tip) {  // tip = {t: title, rows: [[value, label], ...]}
    var h = "<div style='font-size:11.5px;color:" + cssv('--muted') + "'>"
      + esc(tip.t) + '</div>';
    for (var i = 0; i < tip.rows.length; i++) {
      h += "<div><b style='font-variant-numeric:tabular-nums'>" + esc(tip.rows[i][0])
        + "</b> <span style='color:" + cssv('--ink-2') + ";font-size:12px'>"
        + esc(tip.rows[i][1]) + '</span></div>';
    }
    return h;
  }
  function baseOption() {
    return {
      tooltip: {
        trigger: 'axis', confine: true,
        backgroundColor: cssv('--surface'), borderColor: cssv('--border-strong'),
        borderWidth: 1, padding: [7, 11],
        textStyle: { color: cssv('--ink'), fontSize: 12.5, fontFamily: FONT },
        axisPointer: { lineStyle: { color: cssv('--axis') } },
        extraCssText: 'border-radius:8px;box-shadow:none;'
      },
      animation: false,  // a statement page; money that counts up erodes trust
      textStyle: { fontFamily: FONT }
    };
  }
  function catAxis(labels, interval, width) {
    // layout only, never value math: on narrow screens, halve label density
    // so day labels don't collide
    var iv = (width && width < 520) ? interval * 2 + 1 : interval;
    return {
      type: 'category', data: labels, boundaryGap: false,
      axisLine: { lineStyle: { color: cssv('--axis') } },
      axisTick: { show: false },
      axisLabel: { color: cssv('--muted'), fontSize: 11, interval: iv,
                   fontFamily: FONT }
    };
  }
  // y ticks are Python-computed (min/step/labels) so no tick math happens here
  function valAxis(y) {
    return {
      type: 'value', min: y.min, max: y.max, interval: y.step,
      axisLabel: {
        color: cssv('--muted'), fontSize: 11, fontFamily: FONT,
        formatter: function (v) { return y.labels[String(v)] || ''; }
      },
      splitLine: { lineStyle: { color: cssv('--grid'), width: 1 } },
      axisLine: { show: false }, axisTick: { show: false }
    };
  }
  function legendBox() {  // ECharts-native legend: toggles series on click
    return {
      show: true, left: 0, top: 0, itemGap: 18, itemWidth: 22, itemHeight: 10,
      textStyle: { color: cssv('--ink-2'), fontSize: 12, fontFamily: FONT },
      inactiveColor: cssv('--muted'), selectedMode: true
    };
  }
  function xValAxis(maxX, ticks) {  // value x-axis, Python-labelled ticks
    return {
      type: 'value', min: 0, max: maxX, interval: 10,
      axisLabel: {
        color: cssv('--muted'), fontSize: 11, fontFamily: FONT,
        formatter: function (v) { return ticks[String(v)] || ''; }
      },
      splitLine: { show: false },
      axisLine: { lineStyle: { color: cssv('--axis') } },
      axisTick: { show: false }
    };
  }
  function nowDot(xy, label, side) {  // the "you are here" marker + end label
    return {
      type: 'scatter', data: [xy], symbolSize: 9, legendHoverLink: false,
      itemStyle: { color: cssv('--accent'), borderColor: cssv('--surface'),
                   borderWidth: 2 },
      label: { show: true, position: side, formatter: label,
               color: cssv('--ink'), fontWeight: 600, fontSize: 12.5,
               fontFamily: FONT },
      tooltip: { show: false }, z: 3
    };
  }

  var charts = [];
  function buildCharts() {
    charts.forEach(function (c) { c.dispose(); });
    charts = [];
    var P = DATA.pace;
    var el = document.getElementById('pace-chart');
    if (el && P) {
      var c = echarts.init(el, null, { renderer: 'svg' });
      var opt = baseOption();
      // narrow screens wrap the legend onto two lines — the grid starts
      // below it so legend text never overprints the top y-axis label
      opt.grid = { left: 62, right: 16, top: el.clientWidth < 520 ? 88 : 38,
                   bottom: 28 };
      opt.legend = legendBox();
      opt.xAxis = catAxis(P.days, P.xint, el.clientWidth);
      opt.yAxis = valAxis(P.y);
      opt.tooltip.formatter = function (ps) {
        return tipHtml(P.tips[ps[0].dataIndex]);
      };
      opt.series = [];
      if (P.ideal.length) opt.series.push({
        name: 'a typical month, day by day', type: 'line', data: P.ideal,
        symbol: 'none', color: cssv('--ideal'),
        lineStyle: { type: 'dotted', width: 2.5 },
        emphasis: { disabled: true }, z: 1
      });
      if (P.actual.length) opt.series.push({
        name: 'spent, day by day', type: 'line', data: P.actual,
        symbol: 'none', color: cssv('--accent'),
        lineStyle: { width: 2.5, cap: 'round' },
        areaStyle: { opacity: 0.07 },
        emphasis: { disabled: true }, z: 2
      });
      if (P.now) opt.series.push(nowDot(P.now.xy, P.now.label, P.now.side));
      c.setOption(opt);
      charts.push(c);
    }
    var N = DATA.nw;
    el = document.getElementById('nw-chart');
    if (el && N) {
      var c2 = echarts.init(el, null, { renderer: 'svg' });
      var o2 = baseOption();
      var hasCost = N.atcost.some(function (v) { return v !== null; });
      var zoomBar = N.labels.length >= 10;  // slider zoom once history earns it
      o2.grid = { left: 62, right: 84, top: hasCost ? 38 : 16,
                  bottom: zoomBar ? 52 : 28 };
      if (hasCost) o2.legend = legendBox();
      o2.dataZoom = [{ type: 'inside', zoomOnMouseWheel: 'shift',
                       moveOnMouseWheel: false }];
      if (zoomBar) o2.dataZoom.push({
        type: 'slider', height: 16, bottom: 4, brushSelect: false,
        borderColor: 'rgba(0,0,0,0)', backgroundColor: 'rgba(0,0,0,0)',
        fillerColor: cssv('--accent-soft'),
        handleStyle: { color: cssv('--surface'), borderColor: cssv('--axis') },
        moveHandleStyle: { color: cssv('--axis') },
        emphasis: { handleStyle: { borderColor: cssv('--accent') } },
        dataBackground: { lineStyle: { color: cssv('--grid') },
                          areaStyle: { color: cssv('--accent-soft') } },
        selectedDataBackground: { lineStyle: { color: cssv('--axis') },
                                  areaStyle: { color: cssv('--accent-soft') } },
        textStyle: { color: cssv('--muted'), fontSize: 10, fontFamily: FONT }
      });
      o2.xAxis = catAxis(N.labels, N.xint, el.clientWidth);
      o2.yAxis = valAxis(N.y);
      o2.tooltip.formatter = function (ps) {
        return tipHtml(N.tips[ps[0].dataIndex]);
      };
      var market = {
        name: 'at market prices', type: 'line', data: N.market, symbol: 'none',
        color: cssv('--accent'),
        lineStyle: { width: 2.5, cap: 'round' },
        areaStyle: { opacity: 0.07 },
        emphasis: { disabled: true }, z: 2
      };
      if (N.seam !== null) market.markLine = {  // where at-cost becomes market
        symbol: 'none', silent: true,
        lineStyle: { color: cssv('--axis'), type: 'dashed', width: 1 },
        label: { formatter: N.seamLabel, position: 'insideEndTop',
                 color: cssv('--muted'), fontSize: 10.5, fontFamily: FONT },
        data: [{ xAxis: N.seam }]
      };
      o2.series = [market];
      if (hasCost) o2.series.push({
        name: 'at cost (no dated price yet)', type: 'line', data: N.atcost,
        symbol: 'none', color: cssv('--accent'),
        lineStyle: { width: 2, type: 'dashed', opacity: 0.75 },
        emphasis: { disabled: true }, z: 1
      });
      o2.series.push(nowDot(N.end.xy, N.end.label, 'right'));
      c2.setOption(o2);
      charts.push(c2);
    }
    buildWhatif();
  }
  // ---- what-if dials: JS only INDEXES the Python-precomputed grid ----
  var WI = DATA.whatif;
  var wiChart = null;
  var wiEls = {
    rate: document.getElementById('wi-rate'),
    spend: document.getElementById('wi-spend'),
    growth: document.getElementById('wi-growth'),
    rateV: document.getElementById('wi-rate-v'),
    spendV: document.getElementById('wi-spend-v'),
    growthV: document.getElementById('wi-growth-v'),
    target: document.getElementById('wi-target'),
    gap: document.getElementById('wi-gap'),
    pct: document.getElementById('wi-pct'),
    years: document.getElementById('wi-years'),
    coast: document.getElementById('wi-coast'),
    fold: document.getElementById('wi-fold'),
    reset: document.getElementById('wi-reset'),
    chart: document.getElementById('wi-chart')
  };
  function buildWhatif() {
    if (!WI || !wiEls.chart) return;
    wiChart = echarts.init(wiEls.chart, null, { renderer: 'svg' });
    charts.push(wiChart);
    wiUpdate();
  }
  function calyr(years, short) {  // Python-made calendar labels, by offset
    return (short ? WI.xyearS : WI.xyear)[String(Math.round(years))] || '';
  }
  function wiDots(dots) {  // milestone dots: coords + strings all precomputed
    return {
      symbol: 'circle', symbolSize: 9, z: 4,
      itemStyle: { color: cssv('--accent'), borderColor: cssv('--surface'),
                   borderWidth: 2 },
      label: { show: true, position: 'top', distance: 7,
               formatter: function (p) { return p.data.lbl; },
               color: cssv('--ink-2'), fontSize: 10.5, fontFamily: FONT },
      tooltip: { trigger: 'item', formatter: function (p) {
        return tipHtml({ t: p.data.tipT,
                         rows: [['≈' + p.data.tipY + ' yrs', 'from today']] });
      } },
      data: dots
    };
  }
  function wiUpdate() {
    if (!WI || !wiChart) return;
    var ri = +wiEls.rate.value, si = +wiEls.spend.value, gi = +wiEls.growth.value;
    wiEls.rateV.textContent = WI.rates[ri];
    wiEls.spendV.textContent = WI.spends[si];
    wiEls.growthV.textContent = WI.growths[gi];
    wiEls.rate.setAttribute('aria-valuetext', WI.rates[ri]);
    wiEls.spend.setAttribute('aria-valuetext', WI.spends[si]);
    wiEls.growth.setAttribute('aria-valuetext', WI.growths[gi]);
    wiEls.target.textContent = WI.target[ri][si];
    wiEls.gap.textContent = WI.gap[ri][si];
    wiEls.pct.textContent = WI.pct[ri][si];
    var y = WI.years[ri][si][gi];   // Python-computed; null = beyond horizon
    wiEls.years.textContent = y === null ? 'not within ' + WI.maxYears + ' yrs'
      : (y === 0 ? 'already there' : '≈' + y + ' yrs');
    var c = WI.coast[ri][si][gi];   // the coast stat: $0 more saved
    if (wiEls.coast) {
      wiEls.coast.textContent =
        c === 0 ? '' :
        c === null ? ('Growth alone would not get there within ' +
                      WI.maxYears + ' yrs — the saving is doing the lifting.') :
        ('Even with $0 more saved, growth alone gets there in ≈' + c +
         ' yrs (' + calyr(c, false) + ').');
      wiEls.coast.hidden = !wiEls.coast.textContent;
    }
    var ml = {
      symbol: 'none', silent: true,
      label: { fontFamily: FONT, fontSize: 11 },
      data: [{
        yAxis: WI.targetN[ri][si],
        lineStyle: { color: cssv('--ink-2'), type: 'dashed', width: 1 },
        label: { formatter: 'target ' + WI.target[ri][si],
                 position: 'insideStartTop', color: cssv('--ink-2') }
      }]
    };
    var h = WI.half[ri][si][gi];
    var dots = [];
    if (h !== null && h > 0 && (y === null || h < y)) dots.push({
      coord: [h, WI.halfN[ri][si]], lbl: 'halfway ' + calyr(h, true),
      tipT: 'halfway — ' + WI.halfS[ri][si], tipY: h,
      label: { position: 'right' }  // clear of the target line above it
    });
    if (y !== null && y > 0) dots.push({
      coord: [y, WI.targetN[ri][si]], lbl: 'target ' + calyr(y, true),
      tipT: 'target — ' + WI.target[ri][si], tipY: y
    });
    var coastDots = [];
    if (c !== null && c > 0) coastDots.push({
      coord: [c, WI.targetN[ri][si]], lbl: '$0-saved ' + calyr(c, true),
      tipT: 'growth alone reaches ' + WI.target[ri][si], tipY: c
    });
    var opt = baseOption();
    opt.grid = { left: 62, right: 20, top: 36, bottom: 26 };
    opt.legend = legendBox();
    opt.xAxis = xValAxis(WI.maxYears, WI.xticks);
    opt.yAxis = valAxis(WI.ymaps[gi]);
    opt.tooltip.formatter = function (ps) {
      return tipHtml(WI.pathTips[gi][ps[0].dataIndex]);
    };
    opt.series = [{
      name: 'saving as usual', type: 'line', data: WI.paths[gi],
      symbol: 'none', lineStyle: { width: 2.5, color: cssv('--accent') },
      areaStyle: { color: cssv('--accent'), opacity: 0.07 },
      emphasis: { disabled: true }, z: 2, markLine: ml,
      markPoint: wiDots(dots)
    }, {
      name: 'growth alone, $0 saved', type: 'line', data: WI.paths0[gi],
      symbol: 'none', color: cssv('--ideal'),
      lineStyle: { width: 2, type: 'dotted' },
      emphasis: { disabled: true }, z: 1,
      markPoint: wiDots(coastDots)
    }];
    wiChart.setOption(opt, true);
  }
  if (WI && wiEls.rate) {
    [wiEls.rate, wiEls.spend, wiEls.growth].forEach(function (el) {
      el.addEventListener('input', wiUpdate);
    });
    if (wiEls.reset) wiEls.reset.addEventListener('click', function () {
      wiEls.rate.value = WI.def.ri;
      wiEls.spend.value = WI.def.si;
      wiEls.growth.value = WI.def.gi;
      wiUpdate();
    });
    if (wiEls.fold) {
      // phones start with the dials folded; the chart sizes itself when opened
      if (window.matchMedia && window.matchMedia('(max-width:640px)').matches)
        wiEls.fold.removeAttribute('open');
      wiEls.fold.addEventListener('toggle', function () {
        if (wiEls.fold.open && wiChart) { wiChart.resize(); wiUpdate(); }
      });
    }
  }

  applyTheme(theme);

  var rt = null;
  window.addEventListener('resize', function () {
    clearTimeout(rt);
    rt = setTimeout(function () {
      charts.forEach(function (c) { c.resize(); });
    }, 120);
  });
})();
"""

# The whole document. Autoescape is ON: every plain string below escapes on
# output; the deliberately-HTML values (css/js/island, chip bodies, codespans)
# arrive as Markup. Macros keep the repeated shapes in one place.
PAGE_TEMPLATE = """\
{% macro cardhead(title, sub, window='') %}
  <div class="cardhead">
    <div><h2 class="ck">{{ title }}</h2>
    {% if sub %}<p class="sub">{{ sub }}</p>{% endif %}</div>
    {% if window %}<span class="window num">{{ window }}</span>{% endif %}
  </div>
{% endmacro %}
{% macro tablewin(caption, headers, rows) %}
  <details class="tv"><summary>View as table</summary>
  <table><caption>{{ caption }}</caption>
  <thead><tr>{% for h in headers %}<th>{{ h }}</th>{% endfor %}</tr></thead>
  <tbody>{% for r in rows %}<tr>{% for c in r %}<td>{{ c }}</td>{% endfor %}</tr>
  {% endfor %}</tbody></table></details>
{% endmacro %}
{% macro meterrow(name, width, amt, over=false, edu=false) %}
  <div class="envrow"><span class="name">{{ name }}</span>
  <div class="track{{ ' edu' if edu }}"><span class="fill{{ ' over' if over }}{{ ' edu' if edu }}" style="width:{{ width }}%"></span></div>
  <span class="amt">{{ amt }}</span></div>
{% endmacro %}
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:; font-src data:">
<meta name="referrer" content="no-referrer">
<title>Home — the household's money</title>
<style>{{ css }}</style>
</head>
<body>
<script>
try { var t = localStorage.getItem('sara-home-theme');
  if (t === 'light' || t === 'dark')
    document.documentElement.setAttribute('data-theme', t); } catch (e) {}
</script>
<div class="hero">
  <div class="wrap hero-in">
    <div class="hero-top">
      <h1 class="hi">{{ greet }}</h1>
      <div class="hero-side">
        <p class="stamp">{{ stamp }}<br>{{ ledger_stamp }}{{ checks_stamp }}</p>
        <button id="themebtn" class="themebtn" type="button">◑ auto</button>
      </div>
    </div>
    <p class="say">{{ sara }}</p>
  </div>
</div>
<main class="wrap">
<div class="kpis" role="group" aria-label="Today's headline numbers">
  {% for t in kpis %}
  <div class="kpi">
    <div class="kk">{{ t.k }}</div>
    <div class="kv num{{ ' ' + t.cls if t.cls }}">{{ t.v }}</div>
    <div class="ks">{{ t.sub }}</div>
  </div>
  {% endfor %}
</div>

<div class="grid g-pace">
<section class="card">
  {{ cardhead('This month — is it unusual?', p.sub, p.window) }}
  {% if p.empty %}
  <div class="empty"><b>Nothing to pace yet.</b> Import a few months of
  statements and regenerate — the morning line starts there.</div>
  {% else %}
  <p class="phero{{ ' ' + p.hero_cls if p.hero_cls }}">{{ p.hero }}</p>
  <p class="herolab">{{ p.herolab }}</p>
  <div id="pace-chart" class="chart" role="img"
       aria-label="Cumulative spending this month against the typical-month path"></div>
  {% if p.lag_note %}<div class="empty">{{ p.lag_note }}</div>{% endif %}
  {% if p.table_rows %}{{ tablewin(p.table_caption, ['Day', 'Spent so far', 'Typical path'], p.table_rows) }}{% endif %}
  {% endif %}
</section>
<section class="card">
  {{ cardhead('On autopilot', mach.sub, mach.window) }}
  {% if mach.rows %}
  <ul class="lanes">
    {% for r in mach.rows %}
    <li><span class="lanedot {{ r.dot }}" aria-hidden="true"></span>
    <div><div class="lname">{{ r.name }}</div>
    <div class="ldet{{ ' bad' if r.dot == 'bad' }}">{{ r.detail }}</div></div>
    <span class="lanetag">{{ r.tag }}</span></li>
    {% endfor %}
  </ul>
  {% if mach.summary %}<p class="lanesum">{{ mach.summary }}</p>{% endif %}
  {% else %}
  <div class="empty"><b>Nothing on autopilot yet.</b> Tell Sara about the
  household's standing orders — paychecks, auto-invests, balance floors —
  and their health lives here.</div>
  {% endif %}
</section>
</div>

<div class="grid {{ 'g-needs' if moves else 'g-solo' }}">
<section class="card">
  {{ cardhead('Needs you', 'Anything that wants a decision or a quick hand this week.') }}
  {% if needs.state == 'none' %}
  <div class="empty"><b>Checks haven't run yet.</b> Ask Sara to run them —
  anything that needs a human lands here.</div>
  {% elif needs.state == 'ok' %}
  <div class="allclear"><span aria-hidden="true">✓</span>
  <div><b>Nothing needs you today.</b><div class="s">No open alerts, no watch
  items, nothing due soon.</div></div></div>
  {% else %}
  <ul class="needs">
    {% for c in needs.cards %}
    <li><span class="sevdot {{ c.kind }}" aria-hidden="true"></span>
    <div><div class="verb">{{ c.verb|codespans }}</div>
    <div class="why">{{ c.why|codespans }}</div></div>
    <span class="meta">{{ c.meta }}</span></li>
    {% endfor %}
  </ul>
  {% if needs.more > 0 %}
  <p class="morelink">+ {{ needs.more }} more in the <a href="dashboard.html">dense brief</a>.</p>
  {% endif %}
  {% endif %}
</section>
{% if moves %}
<section class="card">
  {{ cardhead('Money that must move', 'Payments and transfers with a date already on them.', 'next ' ~ mustmove_days ~ ' days') }}
  <ul class="moves">
    {% for mv in moves %}
    <li><span class="mvdate num{{ ' near' if mv.near }}">{{ mv.day_lbl }}</span>
    <span class="mvamt num">{{ mv.amt }}</span>
    <span class="mvtext">{{ mv.text }} · {{ mv.when }}</span></li>
    {% endfor %}
  </ul>
</section>
{% endif %}
</div>

<div class="grid g-walk">
<section class="card walk">
  {{ cardhead('The walk-away number', 'The pot where work becomes optional. Liquid dollars only, per the thesis.', wa.window if wa else '') }}
  {% if not wa %}
  <div class="empty"><b>No baseline yet.</b> After {{ min_months }} full months
  of spending history the walk-away math turns on by itself.</div>
  {% else %}
  <p class="whero">{{ wa.hero }} <span class="of">· {{ wa.pct }} of the way</span></p>
  <p class="herolab">{{ wa.srcline }}</p>
  <div class="barwrap">
    <div class="track"><span class="fill" style="width:{{ wa.fill }}%"></span></div>
    {% if wa.shadow_left is not none %}<span class="shadowtick" style="left:{{ wa.shadow_left }}%"></span>{% endif %}
  </div>
  <div class="barnotes num">
    <span>liquid today: <b>{{ wa.liquid }}</b></span>
    {% if wa.shadow_left is not none %}<span>tick = if the paper converts</span>{% endif %}
  </div>
  {% if wa.shadow_note %}<p class="shadownote">{{ wa.shadow_note }}</p>{% endif %}
  {% if wa.foot %}<p class="goalfoot">{{ wa.foot }}</p>{% endif %}
  {% if wi %}
  <div class="wi-cut">
  <div class="wi-hero num">
    <div class="wv-big" id="wi-years"></div>
    <div class="herolab">to the target, at the current dials</div>
    <p class="coastline" id="wi-coast"></p>
    <p class="wi-quiet">target <b id="wi-target"></b> · <b id="wi-gap"></b> · <b id="wi-pct"></b> of the way</p>
  </div>
  <details class="wi-fold" id="wi-fold" open>
    <summary><span class="ck">Play with it · what-if, not advice</span></summary>
    <div class="dials">
      <div class="dial"><label for="wi-rate">withdrawal rate</label>
        <span class="dval num" id="wi-rate-v"></span>
        <input id="wi-rate" type="range" min="0" max="{{ wi.max_ri }}" step="1" value="{{ wi.def_ri }}"></div>
      <div class="dial"><label for="wi-spend">yearly spend</label>
        <span class="dval num" id="wi-spend-v"></span>
        <input id="wi-spend" type="range" min="0" max="{{ wi.max_si }}" step="1" value="{{ wi.def_si }}"></div>
      <div class="dial"><label for="wi-growth">real growth</label>
        <span class="dval num" id="wi-growth-v"></span>
        <input id="wi-growth" type="range" min="0" max="{{ wi.max_gi }}" step="1" value="{{ wi.def_gi }}"></div>
    </div>
    <div class="wi-actions"><button id="wi-reset" class="themebtn" type="button">↺ reset to your numbers</button></div>
    <div id="wi-chart" class="chart" role="img"
         aria-label="Hypothetical projection of liquid net worth toward the dialed target"></div>
  </details>
  <details class="wi-how"><summary>How it works</summary>
  <p class="wi-formula">{{ wi.formula }}</p></details>
  </div>
  {% endif %}
  {% endif %}
</section>
<div class="sidecol">
<section class="card">
  {{ cardhead(edu.title, edu.sub) }}
  {% if edu.empty %}
  <div class="empty">{{ edu.empty }}</div>
  {% else %}
  <p class="heromini num">{{ edu.value }} <span class="of">{{ edu.of }}</span></p>
  {% if edu.fill is not none %}
  <div class="barwrap"><div class="track edu"><span class="fill edu" style="width:{{ edu.fill }}%"></span></div></div>
  <div class="barnotes num"><span>{{ edu.val_lbl }}</span><span>{{ edu.pct }} of the target</span></div>
  {% else %}
  <div class="barnotes num" style="margin-top:8px"><span>{{ edu.val_lbl }}</span></div>
  {% endif %}
  {% for k in edu.perkid %}{{ meterrow(k.name, k.width, k.amt, edu=true) }}{% endfor %}
  {% if edu.nudge %}<div class="nudge">{{ edu.nudge }}</div>{% endif %}
  {% if edu.foot %}<p class="goalfoot">{{ edu.foot }}</p>{% endif %}
  {% endif %}
</section>
{% if wins %}
<section class="card">
  {{ cardhead("Sara's wins", 'Realized only — every dollar here is money already found or saved.', wins.year) }}
  <p class="winhero num">{{ wins.total }} <span class="of">found this year</span></p>
  <details class="tv"><summary>{{ wins.count_lbl }}</summary>
  <ul class="wins-list">
    {% for it in wins.rows %}
    <li><span>{{ it.label }}</span><b>{{ it.amt }}</b></li>
    {% endfor %}
  </ul></details>
</section>
{% endif %}
</div>
</div>

<section class="card" style="margin-top:18px">
  {{ cardhead('Net worth — the long line', nw.sub, nw.window) }}
  <p class="heromini num">{{ nw.liquid }}</p>
  <p class="herolab">liquid net worth — {{ nw.asof }}</p>
  {% if nw.delta %}<div class="chiprow"><span class="chip{{ ' ' + nw.delta.cls if nw.delta.cls }}">{{ nw.delta.body }}</span></div>{% endif %}
  {% if nw.has_chart %}
  <div id="nw-chart" class="chart" role="img" aria-label="Liquid net worth by month"></div>
  {{ tablewin('Liquid net worth at each month end; the endpoint is the headline at the latest prices.',
              ['Month', 'Liquid net worth', 'Basis'], nw.table_rows) }}
  {% else %}
  <div class="empty"><b>Not enough history for a line yet.</b> Two month-ends
  in the ledger and the curve appears.</div>
  {% endif %}
</section>

<div class="grid {{ 'g-chesh' if env_rows else 'g-solo' }}">
<section class="card">
  {{ cardhead(ch.title, "The month's cheshbon — the monthly review closes the book.", ch.window) }}
  <div class="statrow num">
    <div class="stat"><div class="l">in</div><div class="v">{{ ch.inc }}</div></div>
    <div class="stat"><div class="l">out</div><div class="v">{{ ch.exp }}</div></div>
    <div class="stat"><div class="l">net</div><div class="v {{ ch.net_cls }}">{{ ch.net }}</div></div>
  </div>
  {% if ch.ribbon %}
  <div class="ribbon" role="img" aria-label="{{ ch.ribbon_aria }}">
    {% for s in ch.ribbon %}<div class="seg rib-{{ loop.index }}" style="width:{{ s.width }}%"></div>{% endfor %}
  </div>
  <div class="riblegend">
    {% for s in ch.ribbon %}
    <span class="li"><span class="sw rib-{{ loop.index }}" aria-hidden="true"></span>{{ s.name }} <b>{{ s.amt }}</b></span>
    {% endfor %}
  </div>
  {% endif %}
  {% if ch.closed %}<p class="goalfoot">{{ ch.closed.month }}, closed: in {{ ch.closed.inc }} · out {{ ch.closed.exp }} · net {{ ch.closed.net }}.</p>{% endif %}
  {% if ch.table_rows %}{{ tablewin(ch.table_caption, ['Category', 'Spent'], ch.table_rows) }}{% endif %}
</section>
{% if env_rows %}
<section class="card">
  {{ cardhead('Projects', projects_sub) }}
  {% for e in env_rows %}{{ meterrow('#' + e.tag, e.width, e.amt, e.over) }}{% endfor %}
</section>
{% endif %}
</div>

<footer>
  <p class="tagline">Decision support, not licensed advice · every figure
  names its window and is verified against the ledger · Sara keeps the books</p>
  {% if paper %}
  <p>Illiquid paper (not counted anywhere above): <b class="num">{{ paper }}</b> — it
  enters the plan only when it converts, per the household thesis.</p>
  {% endif %}
  {% if unpriced %}
  <p>{{ unpriced|length }} holding{{ 's' if unpriced|length != 1 }} excluded —
  no USD price on file:
  {% for a in unpriced %}<code>{{ a }}</code>{{ ', ' if not loop.last }}{% endfor %}{{ '…' if unpriced_more }}</p>
  {% endif %}
  <p>≈ marks estimates and projections.</p>
  <p>Regenerate with <code>tools/run home.py</code> · the dense brief:
  <a href="dashboard.html">dashboard.html</a> · the microscope:
  <code>scripts/dashboard.sh</code> (fava).</p>
</footer>
</main>
<script id="sara-data" type="application/json">{{ island }}</script>
<script>{{ echarts }}</script>
<script>{{ js }}</script>
</body>
</html>
"""


# ------------------------------------------------------- context builders
def _pace_ctx(pace: Pace) -> dict:
    """The spend-pace card, framed as "is this month unusual?" — the hero is
    the delta vs the typical PATH, never a budget allowance."""
    cur_lbl = month_label(*pace.cur)
    month_name = MONTH_ABBR[pace.cur[1]]
    month_full = MONTH_FULL[pace.cur[1]]
    through_lbl = (f"through {month_name} {pace.through_day}"
                   if pace.through_day else "no activity imported yet")
    window = (f"{cur_lbl} · {through_lbl}"
              + (" · latest imported month" if pace.fallback else ""))
    if pace.typical is None and not pace.daily_cum:
        return {"empty": True, "window": cur_lbl,
                "sub": "Spending vs a typical month."}

    if pace.typical is not None and pace.left is not None:
        sub = (f"typical = the median path of your last "
               f"{len(pace.typical_window)} full months "
               f"({window_label(pace.typical_window)}).")
        if pace.fallback:
            d = round(pace.spent - pace.typical)
            hero = (f"{m0(abs(d))} {'under' if d < 0 else 'over'}"
                    if d else "on pace")
            hero_cls = "good" if d < 0 else ("bad" if d > 0 else "")
            herolab = (f"a typical month's total — {month_full} closed at "
                       f"{m0(pace.spent)} against a typical {m0(pace.typical)}")
        elif pace.pace_delta is not None:
            d = round(pace.pace_delta)
            hero = (f"{m0(abs(d))} {'under' if d < 0 else 'over'}"
                    if d else "on pace")
            hero_cls = "good" if d < 0 else ("bad" if d > 0 else "")
            herolab = (("with" if d == 0 else "") +
                       f" the typical path through {month_name} "
                       f"{pace.through_day} (≈{m0(pace.typical_by_now or 0)} "
                       f"by now) — a typical {month_full} runs "
                       f"{m0(pace.typical)}").strip()
        else:  # a month with a baseline but no postings yet
            hero, hero_cls = m0(pace.left), ""
            herolab = (f"left of a typical {month_full} ({m0(pace.typical)}) — "
                       f"nothing spent on record yet")
    else:
        sub = "no typical-month baseline yet."
        hero, hero_cls = m0(pace.spent), ""
        herolab = (f"spent so far — {MIN_FULL_MONTHS}+ full months unlock the "
                   f"typical-month line")

    return {
        "empty": False, "window": window, "sub": sub,
        "hero": hero, "hero_cls": hero_cls, "herolab": herolab,
        "lag_note": ("" if pace.daily_cum else
                     f"No {month_name} activity imported yet — the solid line "
                     f"starts with the next import."),
        "table_caption": (f"Cumulative spend, {cur_lbl} ({through_lbl}), vs the "
                          f"median path of the window months."),
        "table_rows": [(f"{month_name} {i}", m0(v),
                        "≈" + m0(pace.ideal[i - 1]) if pace.ideal else "—")
                       for i, v in enumerate(pace.daily_cum, 1)],
    }


def _kpi_ctx(liquid: float, asof: date | None, pace: Pace,
             net_mtd: float, wa: Walkaway | None) -> list[dict]:
    """The hero strip: the four numbers the household always sees first,
    each with its tiny window label. '—' + a reason, never a fake zero."""
    ledger_lbl = f"through {mon_d(asof)}" if asof else "ledger empty"
    month_name = MONTH_ABBR[pace.cur[1]]
    tiles = [{"k": "Liquid net worth", "v": m0(liquid), "sub": ledger_lbl,
              "cls": ""}]
    if pace.left is not None:
        left_sub = (f"{month_name} vs typical · latest imported"
                    if pace.fallback else
                    f"a typical {month_name} runs {m0(pace.typical or 0)}")
        tiles.append({"k": "Left this month", "v": m0(pace.left),
                      "sub": left_sub, "cls": "neg" if pace.left < 0 else ""})
    else:
        tiles.append({"k": "Left this month", "v": "—",
                      "sub": f"needs {MIN_FULL_MONTHS} full months", "cls": ""})
    net_sub = (f"{month_name} 1–{pace.through_day} · in − out"
               if pace.through_day else f"{month_name} · nothing imported yet")
    tiles.append({"k": "Net this month", "v": delta0(net_mtd), "sub": net_sub,
                  "cls": "pos" if round(net_mtd) > 0
                  else ("neg" if round(net_mtd) < 0 else "")})
    if wa:
        approx = "" if wa.src == "set" else "≈"
        tiles.append({"k": "Walk-away", "v": pct_display(wa.pct),
                      "sub": (f"of {approx}{m0(wa.target)} — the pot where "
                              f"work turns optional"),
                      "cls": ""})
    else:
        tiles.append({"k": "Walk-away", "v": "—",
                      "sub": "needs a spend baseline", "cls": ""})
    return tiles


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


LANE_TAG = {"deposit": "deposit", "invest": "auto-invest", "floor": "floor"}
LANE_DOT = {"ok": "ok", "intact": "ok", "pending": "watch",
            "overdue": "bad", "below": "bad", "invalid": "mut"}


def _machine_ctx(rows: list[dict]) -> dict:
    """The machine panel, rendered from checks.lane_status() — the same
    detector the findings use, so panel and findings can never disagree."""
    out = []
    running = watching = broken = 0
    for r in rows:
        status = r["status"]
        cad = r["cadence"] + (f" on the {ordinal(int(r['day']))}"
                              if r.get("day") and r["cadence"] == "monthly"
                              else "")
        if status == "ok":
            running += 1
            verb = "landed" if r["kind"] == "deposit" else "ran"
            detail = f"{verb} {mon_d(r['last'])} · {m0(r['last_amount'])} · {cad}"
        elif status == "intact":
            running += 1
            detail = f"{m0(r['balance'])} today · floor {m0(r['floor'])}"
        elif status == "pending":
            watching += 1
            detail = (f"watching for the first arrival · expected "
                      f"~{mon_d(r['expected'])}" if r["expected"] else
                      "watching for the first arrival")
        elif status == "overdue":
            broken += 1
            since = (f"nothing since {mon_d(r['last'])}" if r["last"]
                     else "never seen in the ledger")
            exp = f" · expected ~{mon_d(r['expected'])}" if r["expected"] else ""
            detail = f"{since}{exp}"
        elif status == "below":
            broken += 1
            detail = f"{m0(r['balance'])} today — under the {m0(r['floor'])} floor"
        else:
            broken += 1
            detail = r["note"] or "misdeclared lane"
        out.append({"name": r["name"], "detail": detail,
                    "dot": LANE_DOT.get(status, "mut"),
                    "tag": LANE_TAG.get(r["kind"], r["kind"])})
    n = len(rows)
    bits = [f"{running} of {n} running"]
    if watching:
        bits.append(f"{watching} watching for a first arrival")
    if broken:
        bits.append(f"{broken} need{'s' if broken == 1 else ''} a look")
    return {"rows": out,
            "sub": ("The machine — paychecks, auto-invests, and floors, "
                    "checked against the ledger."),
            "window": "last 2 cycles",
            "summary": " · ".join(bits) if n else ""}


def _walkaway_ctx(wa: Walkaway | None, liquid: float,
                  asof: date | None) -> dict | None:
    if wa is None:
        return None
    if wa.src == "set":
        hero = m0(wa.target)
        srcline = "the target you set"
    else:
        hero = f"≈{m0(wa.target)}"
        srcline = (f"{WALKAWAY_LO}× your true yearly burn — up to "
                   f"≈{m0(wa.hi or 0)} at the safer {WALKAWAY_HI}×")
    foot_bits = []
    if wa.baseline:
        b = wa.baseline
        foot_bits.append(f"True burn: ≈{m0(b.burn)}/mo — what the last "
                         f"{len(b.months)} full months actually cost "
                         f"({window_label(b.months)}).")
        if b.drift_pct is not None:
            d = round(b.drift_pct)
            trend = ("held roughly flat" if d == 0 else
                     f"drifted {'+' if d > 0 else MINUS}{abs(d)}%")
            foot_bits.append(f"Year over year it has {trend} "
                             f"vs the 12 months before.")
        if wa.src == "set" and wa.lo:
            foot_bits.append(f"For reference, burn math alone says "
                             f"≈{m0(wa.lo)}–≈{m0(wa.hi or 0)} "
                             f"({WALKAWAY_LO}–{WALKAWAY_HI}× the yearly burn).")
    shadow_left = shadow_note = None
    if wa.paper > 0:
        shadow_left = f"{max(0.0, min(100.0, wa.paper_pct)):.1f}"
        combined = m0(liquid + wa.paper)
        if wa.paper_pct >= 100:
            shadow_note = Markup(
                "◦ If the paper converts: <b>{}</b> — past the line the day "
                "it's real. Until then it isn't counted.").format(combined)
        else:
            shadow_note = Markup(
                "◦ If the paper converts: <b>{}</b> ({}% of the way). "
                "Not counted until it's real.").format(combined,
                                                      f"{wa.paper_pct:.0f}")
    return {
        "hero": hero, "srcline": srcline, "fill": f"{max(0.0, min(100.0, wa.pct)):.1f}",
        "liquid": m0(liquid),
        "window": (f"through {mon_d(asof)}" if asof else "ledger empty"),
        "pct": pct_display(wa.pct), "shadow_left": shadow_left,
        "shadow_note": shadow_note, "foot": " ".join(foot_bits),
    }


def _education_ctx(accounts: list[EduAccount], pace: float | None,
                   goals: dict, today: date) -> dict:
    target = _as_float(goals.get("education_target"))
    if not accounts:
        empty = (Markup("<b>Target set — {}</b> — but no 529 account in the "
                        "ledger yet. Open one (or import its statements) and "
                        "the story starts here.").format(m0(target))
                 if target else
                 Markup("<b>No 529 on file yet.</b> When one exists in the "
                        "ledger, its story lives here — balance, pace, and "
                        "the date it gets there."))
        return {"title": "The college fund", "sub": "Education savings, per kid.",
                "empty": empty}
    total = sum(a.value for a in accounts)
    kids = ", ".join(sorted({a.kid for a in accounts}))
    ctx = {
        "title": f"{kids}’s 529" if len(accounts) == 1 else "The 529s",
        "sub": "Education savings — the long game with a date on it.",
        "empty": "", "value": m0(total),
        "val_lbl": ("valued at cost — no market price on file yet"
                    if any(a.at_cost for a in accounts)
                    else "at the latest prices on file"),
        "fill": None, "pct": "", "of": "saved", "nudge": None, "foot": None,
        "perkid": [],
    }
    if target:
        pct = 100.0 * total / target
        ctx.update(of=f"of {m0(target)}",
                   fill=f"{max(0.0, min(100.0, pct)):.1f}", pct=pct_display(pct))
        if pace and total < target:
            eta = add_months(today, int((target - total) / pace + 0.999))
            ctx["foot"] = Markup(
                "On pace for <b>≈{} {}</b> at ≈{}/mo (median of recent "
                "contributions), ignoring market growth.").format(
                    MONTH_ABBR[eta.month], eta.year, m0(pace))
        elif total >= target:
            ctx["foot"] = "Past the target — time to raise it, or rest easy."
        else:
            ctx["foot"] = ("No regular contributions detected yet — no arrival "
                           "date to forecast.")
    else:
        pace_bit = f" Contributions are running ≈{m0(pace)}/mo." if pace else ""
        ctx["nudge"] = (f"No target set yet — pick the college number with "
                        f"Sara and this card grows a finish line.{pace_bit}")
    if len(accounts) > 1:  # the per-kid-bar pattern, when a second 529 appears
        mx = max(a.value for a in accounts) or 1.0
        ctx["perkid"] = [
            {"name": a.kid, "width": f"{max(1.0, 100.0 * a.value / mx):.1f}",
             "amt": m0(a.value) + (" · at cost" if a.at_cost else "")}
            for a in accounts]
    return ctx


def _wins_ctx(wins: dict | None, today: date) -> dict | None:
    if not wins:
        return None
    n = len(wins["items"])
    return {
        "total": m0(wins["total"]), "year": str(today.year),
        "count_lbl": f"{n} win{'s' if n != 1 else ''} — the receipts",
        # "rows", not "items": in jinja, wins.items would resolve dict.items()
        "rows": [{"label": it["label"],
                  "amt": m0(it["amt"]) + ("/yr" if it["peryr"] else "")}
                 for it in wins["items"]],
    }


def _networth_ctx(series: list[dict], baseline_cut: int, liquid: float,
                  asof: date | None) -> dict:
    delta = None
    if len(series) >= 2:
        prev = series[-2]
        d = liquid - prev["v"]
        cmp_lbl = f"vs {MONTH_ABBR[prev['d'].month]} {prev['d'].day}"
        if prev["est"]:
            cmp_lbl += " (then at cost)"
        delta = {"cls": "good" if round(d) > 0 else ("bad" if round(d) < 0 else ""),
                 "body": Markup("<b>{}</b> {}").format(delta0(d), cmp_lbl)}
    notes = ["Liquid only — paper stays out, same as every number on this page."]
    if baseline_cut and series:
        s0 = series[0]["d"]
        notes.append(f"Starts {mon_yr((s0.year, s0.month))}, when the ledger's "
                     f"opening balances were complete.")
    est_any = any(p["est"] for p in series)
    if est_any:
        first_mkt = next((p for p in series if not p["est"]), None)
        notes.append("Dashed months lean on cost basis — no dated prices yet"
                     + (f"; market pricing begins "
                        f"{mon_yr((first_mkt['d'].year, first_mkt['d'].month))}"
                        if first_mkt else "") + ".")

    def basis(i: int, p: dict) -> str:
        if i == len(series) - 1 and asof:
            return f"market · through {asof.isoformat()}"
        return "at cost" if p["est"] else "market"
    return {
        "sub": " ".join(notes),
        "window": f"{len(series)} month-ends" if len(series) >= 2 else "",
        "liquid": m0(liquid),
        "asof": (f"through {mon_d(asof)}" if asof else "no ledger yet"),
        "delta": delta, "has_chart": len(series) >= 2,
        "table_rows": [(mon_yr((p["d"].year, p["d"].month)), m0(p["v"]),
                        basis(i, p)) for i, p in enumerate(series)],
    }


def _cheshbon_ctx(pace: Pace) -> dict:
    """This month's money in vs out, how last month closed, and the category
    ribbon (top spenders + Other, 2px surface gaps, legend carries the
    names and dollars). Follows the paced month so a stale ledger shows its
    latest real month, not zeros."""
    ym = pace.cur
    inc, exp = month_in_out(ym)
    net = inc - exp
    through_lbl = (f"through {MONTH_ABBR[ym[1]]} {pace.through_day}"
                   if pace.through_day else "nothing imported yet")
    prev = (ym[0] - 1, 12) if ym[1] == 1 else (ym[0], ym[1] - 1)
    pinc, pexp = month_in_out(prev)
    closed = None
    if pinc or pexp:
        pnet = pinc - pexp
        closed = {"month": mon_yr(prev), "inc": m0(pinc), "exp": m0(pexp),
                  "net": delta0(pnet),
                  "cls": "pos" if round(pnet) > 0 else ("neg" if round(pnet) < 0 else "")}
    cats = month_categories(ym)
    ribbon, table_rows = [], []
    if len(cats) >= 2:
        total = sum(v for _, v in cats)
        segs = cats[:RIBBON_SLOTS]
        other = sum(v for _, v in cats[RIBBON_SLOTS:])
        rows = [(c.replace("Expenses:", "") or "Other", v) for c, v in segs]
        if other > 0.005:
            rows.append(("Other", other))
        ribbon = [{"name": name, "amt": m0(v),
                   "width": f"{max(1.0, 100.0 * v / total):.1f}"}
                  for name, v in rows]
        table_rows = [(c.replace("Expenses:", "") or "Other", m0(v))
                      for c, v in cats]
    return {
        "title": f"{MONTH_FULL[ym[1]]} money in / out",
        "window": f"{mon_yr(ym)} · {through_lbl}",
        "net_raw": net,   # the KPI strip reuses this — one query, one truth
        "inc": m0(inc), "exp": m0(exp), "net": delta0(net),
        "net_cls": "pos" if round(net) > 0 else ("neg" if round(net) < 0 else ""),
        "closed": closed, "ribbon": ribbon,
        "ribbon_aria": ("Where the month went: "
                        + ", ".join(f"{s['name']} {s['amt']}" for s in ribbon)),
        "table_caption": f"Where {MONTH_ABBR[ym[1]]} went, by category ({through_lbl}).",
        "table_rows": table_rows,
    }


def _envelope_rows(envelopes: list[Envelope]) -> list[dict]:
    rows = []
    for e in envelopes[:6]:
        if e.budget:
            pct = 100.0 * e.spent / e.budget
            rows.append({"tag": e.tag, "over": pct > 100,
                         "width": f"{max(1.0, min(100.0, pct)):.1f}",
                         "amt": (f"{m0(e.spent)} of {m0(e.budget)}"
                                 + (f" · {m0(e.spent - e.budget)} over"
                                    if pct > 100 else ""))})
        else:
            rows.append({"tag": e.tag, "over": False, "width": "100.0",
                         "amt": f"{m0(e.spent)} · no budget set"})
    return rows


# ----------------------------------------------------- chart data builders
def _pace_chart_data(pace: Pace) -> dict | None:
    """The pace chart's slice of the data island: category labels, series
    arrays (numbers only), pre-formatted tooltip strings, Python-made y ticks."""
    if not pace.daily_cum and not pace.ideal:
        return None
    month_name = MONTH_ABBR[pace.cur[1]]
    days = [f"{month_name} {d}" for d in range(1, pace.ndays + 1)]
    actual = pace.daily_cum + [None] * (pace.ndays - len(pace.daily_cum))
    tips = []
    for i in range(pace.ndays):
        rows = []
        if i < len(pace.daily_cum):
            rows.append([m0(pace.daily_cum[i]), "spent so far"])
        if pace.ideal:
            rows.append(["≈" + m0(pace.ideal[i]), "typical by now"])
        if i < len(pace.daily_cum) and pace.ideal:
            rows.append([delta0(pace.daily_cum[i] - pace.ideal[i]), "vs typical"])
        tips.append({"t": days[i], "rows": rows})
    spread = (pace.daily_cum or [0]) + (pace.ideal or [0])
    now = None
    if pace.daily_cum:
        idx = len(pace.daily_cum) - 1
        now = {"xy": [idx, pace.daily_cum[-1]], "label": m0(pace.spent),
               "side": "left" if idx > pace.ndays * 0.72 else "right"}
    return {"days": days, "actual": actual, "ideal": pace.ideal, "tips": tips,
            "y": yaxis_payload(min(0, min(spread)), max(spread) or 1),
            "xint": max(0, round(pace.ndays / 7) - 1), "now": now}


def _nw_chart_data(series: list[dict], asof: date | None) -> dict | None:
    """The net-worth chart's slice: months, solid market vs dashed at-cost
    arrays (split so each segment draws in the right style), the seam index."""
    if len(series) < 2:
        return None
    n = len(series)
    labels, market, atcost, tips = [], [], [], []
    for i, p in enumerate(series):
        d = p["d"]
        labels.append(MONTH_ABBR[d.month]
                      + (f" ’{str(d.year)[2:]}" if d.month == 1 or i == 0 else ""))
        market.append(None if p["est"] else p["v"])
        # at-cost series needs the priced neighbors too, so dashed segments
        # reach the boundary points instead of stopping one month short
        near_est = (p["est"] or (i > 0 and series[i - 1]["est"])
                    or (i < n - 1 and series[i + 1]["est"]))
        atcost.append(p["v"] if near_est else None)
        t = mon_yr((d.year, d.month))
        if i == n - 1 and asof:
            t += f" · through {mon_d(asof)}"
        elif p["est"]:
            t += " · at cost"
        tips.append({"t": t, "rows": [[m0(p["v"]), "liquid net worth"]]})
    vs = [p["v"] for p in series]
    seam = next((i for i in range(1, n)
                 if series[i - 1]["est"] and not series[i]["est"]), None)
    return {"labels": labels, "market": market, "atcost": atcost, "tips": tips,
            "y": yaxis_payload(min(vs), max(vs)),
            "xint": max(0, round(n / 8) - 1),
            "seam": seam, "seamLabel": "market prices →" if seam is not None else "",
            "end": {"xy": [n - 1, series[-1]["v"]], "label": m0(series[-1]["v"])}}


def yaxis_payload(lo: float, hi: float) -> dict:
    """Python-computed y-axis: min/max/step plus {value: label} — ECharts
    only looks labels up, it never formats money. (Chart payloads stay
    dicts: they are JSON for the data island, not domain objects.)"""
    ticks = nice_ticks(lo, hi)
    step = ticks[1] - ticks[0] if len(ticks) >= 2 else (hi - lo or 1)
    labels = {str(int(t)) if float(t).is_integer() else str(t): m0(t)
              for t in ticks}
    return {"min": ticks[0], "max": ticks[-1], "step": step, "labels": labels}


# ------------------------------------------------------------------- page
def build_page(now: datetime | None = None) -> str:
    """Pull every surface once, shape it into template context, render."""
    now = now or datetime.now()
    today = now.date()
    balances, unpriced = liquid_balances()
    liquid = sum(v for _, v in balances)
    paper = paper_value()
    asof = latest_ledger_date()
    goals = goals_config()
    totals = monthly_expense_totals()

    pace = spend_pace(today, asof, totals)
    if pace.through_day is None and totals:
        # stale ledger: nothing imported for the calendar month — pace the
        # latest month that has data instead, and say so
        last_m = max(ym for ym, _ in totals)
        if last_m < pace.cur:
            pace = spend_pace(today, asof, totals, month=last_m)
    wa = walkaway(liquid, paper, true_spend_baseline(today, totals), goals)
    wig = wi_ctx = None
    if wa and wa.baseline:
        save = net_savings_baseline(wa.baseline.months)
        wig = whatif_grid(liquid, wa.baseline, save, today)
        b = wa.baseline
        wi_ctx = {
            "max_ri": wig["nRates"] - 1, "max_si": wig["nSpends"] - 1,
            "max_gi": wig["nGrowths"] - 1,
            "def_ri": wig["def"]["ri"], "def_si": wig["def"]["si"],
            "def_gi": wig["def"]["gi"],
            "liquid": m0(liquid),
            "formula": (f"Target = a year of spending ÷ the withdrawal rate. "
                        f"The spend dial starts at your true burn, "
                        f"≈{m0(b.burn * 12)}/yr — what the last "
                        f"{len(b.months)} full months actually cost, "
                        f"annualized. The solid curve compounds today's "
                        f"liquid ({m0(liquid)}) monthly at the chosen real "
                        f"growth and adds your median net savings, "
                        f"≈{m0(save)}/mo ({len(b.months)} full months, "
                        f"{window_label(b.months)}); the dotted twin saves "
                        f"$0 — growth alone. Hypothetical only — taxes, "
                        f"raises, and market reality not included; the "
                        f"thesis, not this toy, sets policy."),
        }
    edu_accounts = education_accounts()
    edu_pace = education_pace(edu_accounts) if edu_accounts else None
    series, baseline_cut = networth_series(liquid, asof)
    cards, more, needs_state = needs_you(today)
    ch = _cheshbon_ctx(pace)

    names = household("names")
    hour = now.hour
    daypart = "morning" if hour < 12 else ("afternoon" if hour < 18 else "evening")
    checks_stamp = ""
    if (cd := findings_date()):
        # a hand-mangled findings stamp must never crash the whole page
        with contextlib.suppress(ValueError):
            checks_stamp = f" · checks from {date.fromisoformat(cd).strftime('%b %-d')}"

    island = {"pace": _pace_chart_data(pace), "nw": _nw_chart_data(series, asof),
              "whatif": wig}
    # '<' escaped so no payee can smuggle a </script> into the island
    island_json = json.dumps(island, separators=(",", ":")).replace("<", "\\u003c")
    css = (CSS_TEMPLATE
           .replace("__INTER_R__", _b64(_asset("Inter-Regular.woff2", "Inter Regular")))
           .replace("__INTER_S__", _b64(_asset("Inter-SemiBold.woff2", "Inter SemiBold"))))

    return _ENV.from_string(PAGE_TEMPLATE).render(
        greet=f"Good {daypart}, {names}" if names else f"Good {daypart}",
        sara=sara_line(pace, needs_state, cards, more),
        stamp=(f"Generated {today.strftime('%a %b %-d')}, "
               f"{now.strftime('%-I:%M %p').lower()}"),
        ledger_stamp=(f"Ledger through {mon_d(asof)}" if asof
                      else "Ledger empty"),
        checks_stamp=checks_stamp,
        kpis=_kpi_ctx(liquid, asof, pace, ch["net_raw"], wa),
        p=_pace_ctx(pace),
        mach=_machine_ctx(lane_status(today)),
        needs={"state": needs_state, "cards": cards, "more": more},
        moves=must_move(today),
        mustmove_days=MUSTMOVE_DAYS,
        wa=_walkaway_ctx(wa, liquid, asof),
        wi=wi_ctx,
        min_months=MIN_FULL_MONTHS,
        edu=_education_ctx(edu_accounts, edu_pace, goals, today),
        wins=_wins_ctx(saras_wins(today), today),
        nw=_networth_ctx(series, baseline_cut, liquid, asof),
        ch=ch,
        env_rows=_envelope_rows(project_envelopes(goals)),
        projects_sub=("Each project's tagged spending against the budget "
                      "set for it — all-time totals."),
        paper=m0(paper) if paper else None,
        unpriced=[a for a, _ in unpriced[:3]], unpriced_more=len(unpriced) > 3,
        css=Markup(css), island=Markup(island_json),
        echarts=Markup(_asset("echarts.min.js", "ECharts bundle").read_text()),
        js=Markup(JS_TEMPLATE))


def home() -> None:
    """Write reports/home.html (also called from reports.py's report loop)."""
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "home.html").write_text(build_page())


if __name__ == "__main__":
    home()
    print(f"ok   home -> {REPORTS / 'home.html'}")
