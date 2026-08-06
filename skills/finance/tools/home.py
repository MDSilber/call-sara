#!/usr/bin/env python3
"""Generate reports/home.html — "Sara Home", the spouse-legible morning page.

Run:    tools/run home.py
Writes: reports/home.html — the ONLY file written; the vault is otherwise
        read-only. One fully self-contained page (Inter + Apache ECharts
        inlined, CSP default-src 'none') openable straight from disk; it
        cannot phone home. Three views: fava (dashboard.sh) is the
        microscope, reports/dashboard.html (--pretty) the dense brief, and
        THIS page (--home) the morning glance a spouse reads in ten seconds.

Page contract — THE GLANCE, then THE ROOMS. The glance fits one laptop
viewport with zero scroll: the aurora hero (greeting, Sara's one-line
verdict, freshness stamp) with four VERDICT tiles floating over it —
Spending (plain-words pace verdict, delta secondary), Net worth (value +
delta chip + sparkline), Autopilot (n-of-n green + one dot per lane), and
the 529 (verdict + saved) — then ONE "Next" line: the single
highest-priority item (top alert, else the nearest dated obligation, else
"nothing needs you"). DENSITY LAW: a closed surface shows at most two
figures and leads with verdict words; everything else lives inside rooms.

The rooms are a tab bar (Spending · Money map · Goals · Autopilot), one
room open at a time — plain JS tabs over the same precomputed data island,
deep-linkable via location.hash, keyboard accessible, all rooms expanded in
print. SPENDING: clickable category bars for the active period (This month
· Last month · 6 months); picking a category swaps in its 6-month trend
and its top merchants (both precomputed per category per period); the
is-this-month-unusual pace chart (solid actual vs dotted typical-median
path) and realized "Sara's finds" live here. MONEY MAP: an ECharts treemap
of where every liquid dollar sits (institution → account → holding,
drill-down on click, sums tied to the headline), the net-worth line with
its why-it-moved attribution (markets vs saved vs spent, suppressed rather
than mislabeled on stale prices), and the thesis drift strip (loud only
out of band). GOALS: the 529 story with a contribution what-if slider
riding a precomputed glide-path grid, plus the project envelopes.
AUTOPILOT: the lanes (rules.toml [[lanes]] via checks.lane_status, the
same detector the findings use), "Needs you" verb cards beyond the
promoted one, and "Money that must move" — human obligations up front,
own-account plumbing folded. EVERY figure carries its window label; every
delta names its comparison.

INDEPENDENCE is an OPT-IN fifth room (see show_walkaway_room for the one
rule): the walk-away number, what-if dials, life-event toggles, and the
1871-history survival replay (market_history.py — counts, never decimal
percents). Off = none of it is computed or shipped.

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
from vault import (REPORTS, VAULT, amount, dated_bullets, household,  # noqa: E402
                   illiquid_currency_regex, query)
from reports import liquid_balances, paper_value  # noqa: E402
from webview import (MONTH_ABBR, _units, action_queue, code_spans,  # noqa: E402
                     deadline_items, latest_ledger_date, month_label,
                     networth_series, nice_ticks, parse_findings,
                     price_history)
from checks import goals as goals_config  # noqa: E402
from checks import lane_status  # noqa: E402
from allocation import allocation_view  # noqa: E402
from market_history import RETIREMENT_YEARS, survival_tables  # noqa: E402

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
PACE_BAND = 0.10          # within ±this × typical = "On pace"; past it, a verdict
TREND_MONTHS = 6          # spending drill: trend window, current month included
MERCHANTS_TOP = 8         # merchant rows per category per period
CATS_VISIBLE = 10         # clickable category rows; the rest fold into the table
SPARK_MONTHS = 13         # net-worth tile sparkline: up to this many month-ends
EDU_SLIDER_MAX = 2000     # 529 what-if slider: $0..this per month
EDU_SLIDER_STEP = 50      # ...in these steps

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
    days: int | None = None       # deadline cards: days until due (Next-line pick)


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
WHATIF_SPEND_POINTS = 21        # ~this many spend steps across 0.5–1.5× burn
SPEND_STEP_LADDER = (2_500, 5_000, 10_000, 15_000, 20_000, 25_000, 50_000)
WHATIF_MAX_YEARS = 40           # projection horizon; beyond = "not within 40"
DEFAULT_STOCK_PCT = 70.0        # history-replay mix until targets are declared
HOUSE_DOWN_BURN_MULT = 1.5      # house preset ≈ 1.5× a year of burn, $25k-rounded
HOUSE_YEARS_OUT = 5             # ...bought this many years out, absent a plan
HOUSE_AMOUNT_MULTS = (0.75, 1.0, 1.25)   # the quiet select's three amounts
HOUSE_YEAR_OFFSETS = (-2, 0, 2)          # ...and its three purchase years
COLLEGE_AGE = 18
COLLEGE_FALLBACK_COST = 400_000  # today's-dollar private-college placeholder


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


def salary_streams(months: list[YM]) -> list[tuple[str, float]]:
    """Per salary account: employer label + median monthly net posted to
    the ledger over the baseline-window months that saw any posting.
    Largest stream first."""
    rows = query("SELECT account, year, month, sum(convert(position,'USD')) AS v "
                 "WHERE account ~ 'Salary' GROUP BY account, year, month")
    keep = set(months)
    per: dict[str, list[float]] = {}
    for r in rows:
        try:
            ym = (int(r["year"]), int(r["month"]))
        except (TypeError, ValueError):
            continue
        if ym in keep:
            per.setdefault(r["account"], []).append(-amount(r["v"]))
    out = []
    for acct, vals in per.items():
        vals = [v for v in vals if v > 0]
        if not vals:
            continue
        segs = [s for s in acct.split(":") if s and s != "Salary"]
        out.append((segs[-1] if segs else acct, median(vals)))
    return sorted(out, key=lambda kv: -kv[1])


class LifeEvents(NamedTuple):
    """The three toggles' presets, derived from the vault (or the named
    fallback). Dollars are today's dollars; months count from today."""
    house_amounts: list[int]        # the quiet select's options, preset included
    house_years: list[int]          # calendar years, preset included
    house_def_a: int                # index of the preset amount
    house_def_y: int
    house_src: str
    partner_monthly: float | None   # smaller salary stream, $/mo net (ledger)
    partner_label: str
    college_year: int | None        # calendar year of the college lump
    college_lump: float             # net of the 529's current path; 0 = covered
    college_cost: float
    college_529_path: float
    college_src: str


PEOPLE_BORN = re.compile(r"^born:\s*(\d{4})-(\d{2})-(\d{2})", re.M)
PEOPLE_NAME = re.compile(r"^name:\s*(.+)$", re.M)
TARGET_YEAR = re.compile(r"(20\d{2})")


def _college_year(today: date,
                  edu_accounts: list["EduAccount"]) -> tuple[int | None, str]:
    """(calendar year college starts, source note): the youngest kid turns
    18 (facts/people), else the 529's own target year read off the account
    or portfolio name; (None, '') when neither exists."""
    kid = _youngest_person(today)
    if kid:
        return kid[1] + COLLEGE_AGE, f"the year {kid[0]} turns {COLLEGE_AGE}"
    for a in edu_accounts:
        m = TARGET_YEAR.search(a.account) or TARGET_YEAR.search(a.kid)
        if m:
            return int(m.group(1)), "the 529's target year"
    return None, ""


def _youngest_person(today: date) -> tuple[str, int] | None:
    """(first name, birth year) of the youngest person in facts/people/."""
    best: tuple[date, str] | None = None
    people = VAULT / "facts" / "people"
    if not people.is_dir():
        return None
    for f in sorted(people.rglob("*.md")):
        try:
            txt = f.read_text()
        except OSError:
            continue
        m = PEOPLE_BORN.search(txt)
        if not m:
            continue
        try:
            born = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if born > today:
            continue
        nm = PEOPLE_NAME.search(txt)
        name = (nm.group(1).strip().split()[0] if nm
                else f.parent.name.capitalize())
        if best is None or born > best[0]:
            best = (born, name)
    return (best[1], best[0].year) if best else None


def life_events(today: date, burn_annual: float, months: list[YM],
                goals: dict, edu_accounts: list["EduAccount"],
                edu_pace: float | None) -> LifeEvents:
    # house: a declared plan (facts/goals house_downpayment + house_year)
    # wins; else a stated derivation from the household's own burn
    down = _as_float(goals.get("house_downpayment"))
    year = _as_float(goals.get("house_year"))
    if down and down > 0:
        down = round(down)                # the declared number, verbatim
        house_src = "the down-payment goal in facts/goals"
    else:
        down = max(25_000,               # derived: stated multiple, $25k-round
                   round(burn_annual * HOUSE_DOWN_BURN_MULT / 25_000) * 25_000)
        house_src = (f"a placeholder ≈{HOUSE_DOWN_BURN_MULT:g}× a year of "
                     f"burn — set house_downpayment in facts/goals to pin it")
    year_cal = (int(year) if year and year > today.year
                else today.year + HOUSE_YEARS_OUT)
    amounts = sorted({down} | {max(5_000, round(down * m / 5_000) * 5_000)
                               for m in HOUSE_AMOUNT_MULTS if m != 1.0})
    years = sorted({max(today.year + 1, year_cal + off)
                    for off in HOUSE_YEAR_OFFSETS})

    # partner: the smaller of the two biggest salary streams keeps coming
    streams = salary_streams(months)
    partner_monthly, partner_label = None, ""
    if len(streams) >= 2:
        partner_label, partner_monthly = min(streams[:2], key=lambda kv: kv[1])

    college_year, college_src = _college_year(today, edu_accounts)
    lump = cost = path529 = 0.0
    if college_year and college_year > today.year:
        cost = _as_float(goals.get("education_target")) or 0.0
        if cost <= 0:
            cost = COLLEGE_FALLBACK_COST
            college_src += (" · the cost is a private-college placeholder — "
                            "set education_target to pin it")
        months_to = max(0, (college_year - today.year) * 12 - today.month + 8)
        path529 = (sum(a.value for a in edu_accounts)
                   + (edu_pace or 0.0) * months_to)
        lump = max(0.0, cost - path529)
    else:
        college_year = None
    return LifeEvents(
        house_amounts=amounts, house_years=years,
        house_def_a=amounts.index(down) if down in amounts else len(amounts) // 2,
        house_def_y=(years.index(year_cal) if year_cal in years
                     else len(years) // 2),
        house_src=house_src, partner_monthly=partner_monthly,
        partner_label=partner_label, college_year=college_year,
        college_lump=round1k(lump), college_cost=cost,
        college_529_path=path529, college_src=college_src)


def _months_to_target(liquid: float, monthly_save: float, target: float,
                      growth_pct: float,
                      events: tuple | list = ()) -> float | None:
    """First month from which the projected balance sits at/above target
    and no later life event knocks it back under. `events` = (month, lump)
    pairs — dollars leaving the path at that month. Closed form inside each
    inter-event regime (verified independently by the ground-truth table);
    None = not within the horizon."""
    if target <= 0:
        return 0.0
    horizon = WHATIF_MAX_YEARS * 12
    evs = sorted((m, a) for m, a in events if 0 < m <= horizon and a > 0)
    f = (1.0 + growth_pct / 100.0) ** (1.0 / 12.0)

    def grow(b0: float, months: float) -> float:
        if growth_pct <= 0:
            return b0 + monthly_save * months
        g = f ** months
        return b0 * g + monthly_save * (g - 1.0) / (f - 1.0)

    def cross(b0: float, m0: float, m1: float) -> float | None:
        if b0 >= target:
            return m0
        if growth_pct <= 0:
            if monthly_save <= 0:
                return None
            m = m0 + (target - b0) / monthly_save
            return m if m < m1 else None
        k = monthly_save / (f - 1.0)
        if b0 + k <= 0:            # drains faster than growth can lift
            return None
        m = m0 + math.log((target + k) / (b0 + k)) / math.log(f)
        return m if m < m1 else None

    bounds: list[float] = [0.0] + [float(m) for m, _ in evs] + [float(horizon)]
    bals = [liquid]
    for i, (m, lump) in enumerate(evs):
        bals.append(grow(bals[i], m - bounds[i]) - lump)

    def cross_req(b0: float, m0: float, m1: float, req: float,
                  me: float) -> float | None:
        """First m in [m0, m1) where the balance covers `req` dollars due at
        month me, growing alone in between: B(m) ≥ req · f^(m−me)."""
        if growth_pct <= 0:
            if b0 >= req:
                return m0
            if monthly_save <= 0:
                return None
            m = m0 + (req - b0) / monthly_save
            return m if m < m1 else None
        k = monthly_save / (f - 1.0)
        coeff = (b0 + k) - req * f ** (m0 - me)
        if coeff <= 0:
            return None
        m = (m0 if k <= coeff
             else m0 + math.log(k / coeff) / math.log(f))
        return m if m < m1 else None

    # a crossing is the walk-away month only if the pot ALSO pre-funds every
    # later life event: growing alone from that month, it must absorb each
    # remaining lump and still sit at the target (post-walk-away spending is
    # the survival replay's job; this guards the lumps).
    for i in range(len(bounds) - 1):
        cands = [cross(bals[i], bounds[i], bounds[i + 1])]
        for e in range(i, len(evs)):
            req = target + sum(
                evs[k][1] * (f ** (evs[e][0] - evs[k][0])
                             if growth_pct > 0 else 1.0)
                for k in range(i, e + 1))
            cands.append(cross_req(bals[i], bounds[i], bounds[i + 1],
                                   req, float(evs[e][0])))
        if all(c is not None for c in cands):
            c = max(c for c in cands if c is not None)
            return c if c <= horizon else None
    return None


def _years_or_none(months: float | None) -> float | None:
    return None if months is None else round(months / 12.0, 1)


def _spend_axis(annual_burn: float) -> list[int]:
    """Spend-dial steps across 0.5–1.5× the true burn: ~21 points snapped
    to a human step (the resolution the grid's size budget allows)."""
    raw = annual_burn / WHATIF_SPEND_POINTS
    step = next((s for s in SPEND_STEP_LADDER if s >= raw),
                SPEND_STEP_LADDER[-1])
    lo = max(step, int(round(annual_burn * 0.5 / step)) * step)
    hi = max(lo + step, int(round(annual_burn * 1.5 / step)) * step)
    return list(range(lo, hi + 1, step))


def _event_list(ev: LifeEvents, today: date, h: int, c: int,
                ai: int, yi: int) -> list[tuple[int, float]]:
    """The (month, lump) list a toggle state implies, months from today
    (events land mid-year of their calendar year)."""
    out = []
    if h:
        m = max(1, (ev.house_years[yi] - today.year) * 12 - today.month + 7)
        out.append((m, float(ev.house_amounts[ai])))
    if c and ev.college_year and ev.college_lump > 0:
        m = max(1, (ev.college_year - today.year) * 12 - today.month + 8)
        out.append((m, ev.college_lump))
    return out


def whatif_grid(liquid: float, baseline: Baseline, monthly_save: float,
                today: date, goals: dict, edu_accounts: list["EduAccount"],
                edu_pace: float | None, stock_pct: float,
                stock_src: str) -> dict:
    """The full precomputed scenario space: the "Play with it" dials, the
    three life-event toggles, and the walk-away history replay. Python owns
    every dollar figure AND every sequence replay; the page's JS only
    indexes this grid (keys are concatenated index digits, nothing more).

    Layout: `tg[p]` = target family per partner state; `years[hKey+p+c]` =
    arrival horizons (hKey carries the quiet-select lattice, "h1a2y0");
    `coast`/`half` ride the base state only — milestones hide under
    toggles; `paths[hKey+c]` = projection curves (partner moves the target
    line, not the curve); `surv` = the historical-sequence tables — counts
    and bands per withdrawal rate (pot-normalized replays, so survival
    depends only on the rate and the mix), guardrail + unspent medians
    scaled to cell dollars here. The spend dial centers on the TRUE BURN."""
    annual = baseline.burn * 12
    spends = _spend_axis(annual)
    ev = life_events(today, annual, baseline.months, goals,
                     edu_accounts, edu_pace)
    surv = survival_tables(WHATIF_RATES, stock_pct)
    n_r, n_s = len(WHATIF_RATES), len(spends)

    # ---- target family, per partner state -------------------------------
    p_states = ["0"] + (["1"] if ev.partner_monthly else [])
    partner_annual = (ev.partner_monthly or 0.0) * 12
    tg: dict[str, dict] = {}
    for p in p_states:
        tN, tS, gap, pct = [], [], [], []
        for r in WHATIF_RATES:
            tn_r, ts_r, gp_r, pc_r = [], [], [], []
            for sp in spends:
                wbase = max(0.0, sp - (partner_annual if p == "1" else 0.0))
                if wbase <= 0:
                    tn_r.append(0.0)
                    ts_r.append("$0 — the paycheck covers this spend")
                    gp_r.append("nothing to bridge")
                    pc_r.append("—")
                    continue
                t = round1k(wbase / (r / 100.0))
                tn_r.append(t)
                ts_r.append("≈" + m0(t))
                g = t - liquid
                gp_r.append(("past it by " + m0(-g)) if g <= 0
                            else (m0(g) + " to go"))
                pc_r.append(f"{min(999.0, 100.0 * liquid / t):.0f}%")
            tN.append(tn_r)
            tS.append(ts_r)
            gap.append(gp_r)
            pct.append(pc_r)
        tg[p] = {"targetN": tN, "target": tS, "gap": gap, "pct": pct}

    # halfway strings ride the base state only
    half_n = [[tg["0"]["targetN"][ri][si] / 2.0 for si in range(n_s)]
              for ri in range(n_r)]
    half_s = [["≈" + m0(v) for v in row] for row in half_n]

    def horizon_grid(target_n, save, events):
        return [[[_years_or_none(_months_to_target(
            liquid, save, target_n[ri][si], g, events))
            for g in WHATIF_GROWTHS] for si in range(n_s)]
            for ri in range(n_r)]

    # ---- arrival horizons per toggle state ------------------------------
    college_on = ev.college_year is not None and ev.college_lump > 0
    c_states = ["0", "1"] if college_on else ["0"]
    h_keys = ["h0"] + [f"h1a{ai}y{yi}"
                       for ai in range(len(ev.house_amounts))
                       for yi in range(len(ev.house_years))]
    years: dict[str, list] = {}
    for hk in h_keys:
        h, ai, yi = (0, 0, 0) if hk == "h0" else (1, int(hk[3]), int(hk[5]))
        for p in p_states:
            for c in c_states:
                evts = _event_list(ev, today, h, int(c), ai, yi)
                years[f"{hk}p{p}c{c}"] = horizon_grid(
                    tg[p]["targetN"], monthly_save, evts)
    coast = horizon_grid(tg["0"]["targetN"], 0.0, [])
    half = [[[_years_or_none(_months_to_target(
        liquid, monthly_save, half_n[ri][si], g))
        for g in WHATIF_GROWTHS] for si in range(n_s)] for ri in range(n_r)]

    # ---- projection paths per house/college state -----------------------
    def balance_at(months, f, g, save, events):
        if g <= 0:
            bal = liquid + save * months
        else:
            grown = f ** months
            bal = liquid * grown + save * (grown - 1.0) / (f - 1.0)
        for em, lump in events:
            if em <= months:
                bal -= lump * (f ** (months - em) if g > 0 else 1.0)
        return max(0.0, round(bal))

    paths: dict[str, list] = {}
    ptips: dict[str, list] = {}
    pdots: dict[str, list] = {}
    pmax: dict[str, list] = {}
    paths0 = []                      # growth-alone twin, base state only
    for g in WHATIF_GROWTHS:
        f = (1.0 + g / 100.0) ** (1.0 / 12.0)
        paths0.append([[yr, balance_at(yr * 12, f, g, 0.0, [])]
                       for yr in range(WHATIF_MAX_YEARS + 1)])
    for hk in h_keys:
        h, ai, yi = (0, 0, 0) if hk == "h0" else (1, int(hk[3]), int(hk[5]))
        for c in c_states:
            evts = _event_list(ev, today, h, int(c), ai, yi)
            key = f"{hk}c{c}"
            pp, tt, dd, mm = [], [], [], []
            for gi, g in enumerate(WHATIF_GROWTHS):
                f = (1.0 + g / 100.0) ** (1.0 / 12.0)
                pts = [[yr, balance_at(yr * 12, f, g, monthly_save, evts)]
                       for yr in range(WHATIF_MAX_YEARS + 1)]
                pp.append(pts)
                tt.append(["≈" + m0(v) for _, v in pts])
                dd.append([{"coord": [round(em / 12.0, 1),
                                      balance_at(em, f, g, monthly_save, evts)],
                            "lbl": delta0(-lump)}
                           for em, lump in evts])
                mx = max(v for _, v in pts)
                mm.append(max(mx, max(v for _, v in paths0[gi])
                              if key == "h0c0" else 0))
            paths[key] = pp
            ptips[key] = tt
            pdots[key] = dd
            pmax[key] = mm

    # ---- y-axis pool: deduplicated nice scales, indexed per state -------
    ymaps_pool: list[dict] = []
    pool_seen: dict[tuple, int] = {}
    ymap_idx: dict[str, list[int]] = {}
    for key, mm in pmax.items():
        for p in p_states:
            tmax = max((max(row) for row in tg[p]["targetN"]), default=0.0)
            idxs = []
            for gi in range(len(WHATIF_GROWTHS)):
                y = yaxis_payload(0, max(mm[gi], tmax, liquid, 1.0))
                sig = (y["min"], y["max"])
                if sig not in pool_seen:
                    pool_seen[sig] = len(ymaps_pool)
                    ymaps_pool.append(y)
                idxs.append(pool_seen[sig])
            ymap_idx[f"{key}p{p}"] = idxs

    # ---- history replay, scaled to cell dollars -------------------------
    guard_trig: dict[str, list] = {}
    guard_cut: dict[str, list] = {}
    unspent: dict[str, list] = {}
    for p in p_states:
        gt, gc, un = [], [], []
        for ri in range(n_r):
            g = surv.guard[ri]
            uf = surv.unspent_frac[ri]
            gt_r, gc_r, un_r = [], [], []
            for si in range(n_s):
                pot = tg[p]["targetN"][ri][si]
                wbase = max(0.0, spends[si]
                            - (partner_annual if p == "1" else 0.0))
                if pot <= 0:
                    gt_r.append(None)
                    gc_r.append(None)
                    un_r.append(None)
                    continue
                gt_r.append(m0(round1k(pot * g.trigger_frac)) if g else None)
                gc_r.append("≈" + m0(round(wbase * (1 - g.cut_frac) / 12))
                            + "/mo" if g else None)
                un_r.append("≈" + m0(round1k(pot * uf)) if uf else None)
            gt.append(gt_r)
            gc.append(gc_r)
            un.append(un_r)
        guard_trig[p] = gt
        guard_cut[p] = gc
        unspent[p] = un

    mix_lbl = f"{stock_pct:.0f}/{100 - stock_pct:.0f} stock/bond mix ({stock_src})"
    surv_ctx = {
        "survived": surv.survived, "nSeq": surv.n_seq, "bands": surv.bands,
        "bandY": {"min": 0, "max": surv.n_seq, "step": surv.n_seq / 4,
                  "labels": {str(surv.n_seq): f"all {surv.n_seq}", "0": "0"}},
        "bandX": {"0": "walk-away",
                  **{str(x): f"{x} yrs in" for x in (10, 20, 30)},
                  "40": "40"},
        "guardTrig": guard_trig, "guardCut": guard_cut, "unspent": unspent,
        "window": (f"{surv.n_seq} starts, {surv.start_lo}–{surv.start_hi}, "
                   f"data through {surv.data_hi}"),
        "mix": mix_lbl,
    }

    xticks = {str(y): ("today" if y == 0 else f"{y} yrs")
              for y in range(0, WHATIF_MAX_YEARS + 1, 10)}
    xyear = {str(y): str(today.year + y)
             for y in range(WHATIF_MAX_YEARS + 1)}
    xyear_s = {k: "’" + v[2:] for k, v in xyear.items()}
    nearest_si = min(range(n_s), key=lambda i: abs(spends[i] - annual))
    return {
        "rates": [f"{r:.1f}%" for r in WHATIF_RATES],
        "spends": [f"{m0(sp)}/yr" for sp in spends],
        "growths": [f"{g:.1f}% real" for g in WHATIF_GROWTHS],
        "nRates": n_r, "nSpends": n_s, "nGrowths": len(WHATIF_GROWTHS),
        "tg": tg, "halfN": half_n, "halfS": half_s,
        "years": years, "coast": coast, "half": half,
        "paths": paths, "pathTips": ptips, "pathDots": pdots, "paths0": paths0,
        "paths0Tips": [["≈" + m0(v) for _, v in pts] for pts in paths0],
        "ymapsPool": ymaps_pool, "ymapIdx": ymap_idx,
        "surv": surv_ctx,
        "ev": {"houseAmts": [m0(a) for a in ev.house_amounts],
               "houseYears": [str(y) for y in ev.house_years],
               "houseDefA": ev.house_def_a, "houseDefY": ev.house_def_y,
               "partner": ev.partner_monthly is not None,
               "college": college_on},
        "xticks": xticks, "xyear": xyear, "xyearS": xyear_s,
        "maxYears": WHATIF_MAX_YEARS,
        "def": {"ri": WHATIF_RATES.index(4.0), "si": nearest_si,
                "gi": WHATIF_GROWTHS.index(4.0)},
        "_ev": ev,      # template context only; stripped before the island
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
                              "deadline", dl["days"]))
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
# Internal plumbing = money shuffling between the household's own accounts
# to feed automation (settlement top-ups, sweeps). It still must move, but
# it isn't a HUMAN obligation — it folds behind a disclosure. Matched on
# wording only; a miss just leaves the item in the human list, never hides it.
PLUMBING_RE = re.compile(
    r"top[ -]?up|sweep\b|settlement (?:fund|cash|account)"
    r"|replenish|move (?:cash|funds) (?:to|into)", re.I)


def must_move(today: date) -> list[dict]:
    """Dated facts bullets inside MUSTMOVE_DAYS that carry a real dollar
    figure — the obligations calendar. Nothing is invented: no date + amount
    in the vault, no chip. The first $ figure in the bullet is the chip's
    number; `~`, `≈`, or a K/M suffix keeps its ≈. Each row is tagged
    plumbing=True when it reads like an own-account shuffle (PLUMBING_RE);
    the Autopilot room folds those, the human ones stay up front."""
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
                    "amt": ("≈" if approx else "") + m0(val), "text": text,
                    "plumbing": bool(PLUMBING_RE.search(text))})
    out.sort(key=lambda r: r["date"])
    human = [r for r in out if not r["plumbing"]][:MUSTMOVE_MAX]
    return human + [r for r in out if r["plumbing"]]


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


# ------------------------------------------------------ the spending room
def _last_months(cur: YM, n: int) -> list[YM]:
    """The n calendar months ending AT cur, oldest first."""
    y, m = cur
    out = []
    for back in range(n - 1, -1, -1):
        yy, mm = y, m - back
        while mm < 1:
            yy, mm = yy - 1, mm + 12
        out.append((yy, mm))
    return out


def _cat_label(cat: str) -> str:
    return (cat or "Expenses").replace("Expenses:", "") or "Other"


def spending_data(pace: Pace) -> dict | None:
    """The Spending room's island: per-period category rollups with their
    top merchants, plus a per-category monthly trend — every dollar string
    preformatted here. Periods are the paced month, the month before, and
    the whole trend window; JS only swaps precomputed rows in and out.
    One query covers all of it (cat × payee × month over the window)."""
    cur = pace.cur
    months = _last_months(cur, TREND_MONTHS)
    prev = months[-2] if len(months) >= 2 else None
    start = date(months[0][0], months[0][1], 1)
    rows = query(f"SELECT root(account,2) AS cat, payee, year, month, "
                 f"sum(convert(position,'USD')) AS v "
                 f"WHERE account ~ '^Expenses' AND date >= {start.isoformat()} "
                 f"GROUP BY cat, payee, year, month")
    by_cat: dict[str, dict[YM, float]] = {}
    merch: dict[tuple[str, str], dict[str, float]] = {}
    for r in rows:
        try:
            ym = (int(r["year"]), int(r["month"]))
        except (TypeError, ValueError):
            continue
        if ym not in set(months):
            continue
        cat, v = _cat_label(r["cat"]), amount(r["v"])
        by_cat.setdefault(cat, {})
        by_cat[cat][ym] = by_cat[cat].get(ym, 0.0) + v
        payee = (r["payee"] or "").strip() or "(no payee)"
        for period in (("cur",) if ym == cur else ()) + \
                      (("prev",) if ym == prev else ()) + ("six",):
            d = merch.setdefault((period, cat), {})
            d[payee] = d.get(payee, 0.0) + v
    if not by_cat:
        return None

    cat_names = sorted(by_cat, key=lambda c: -sum(by_cat[c].values()))
    per_totals = {p: {c: sum(v for ym, v in by_cat[c].items() if keep(ym))
                      for c in cat_names}
                  for p, keep in (("cur", lambda ym: ym == cur),
                                  ("prev", lambda ym: ym == prev),
                                  ("six", lambda ym: True))}
    partial = (pace.through_day is not None
               and pace.through_day < pace.ndays)
    through = (f"{MONTH_ABBR[cur[1]]} 1–{pace.through_day}"
               if pace.through_day else "nothing imported yet")

    cats = []
    for ci, c in enumerate(cat_names):
        series = [round(by_cat[c].get(ym, 0.0), 2) for ym in months]
        tips = []
        for mi, ym in enumerate(months):
            t = mon_yr(ym)
            if ym == cur and partial:
                t += f" · {through} (partial)"
            tips.append({"t": t, "rows": [[m0(series[mi]), c]]})
        per = {}
        for p in ("cur", "prev", "six"):
            tot = per_totals[p][c]
            if tot <= 0.005:
                continue
            period_sum = sum(v for v in per_totals[p].values() if v > 0)
            period_max = max((v for v in per_totals[p].values() if v > 0),
                             default=1.0)
            pairs = sorted(((n, v) for n, v in merch.get((p, c), {}).items()
                            if v > 0.005), key=lambda nv: -nv[1])
            per[p] = {
                "amt": m0(tot),
                "pct": (f"{100.0 * tot / period_sum:.0f}%"
                        if period_sum else "—"),
                "w": round(max(1.5, 100.0 * tot / period_max), 1),
                "merch": [[n, m0(v)] for n, v in pairs[:MERCHANTS_TOP]],
                "more": max(0, len(pairs) - MERCHANTS_TOP),
            }
        cats.append({"name": c, "series": series, "tips": tips,
                     "y": yaxis_payload(0, max(max(series), 1.0)), "per": per})

    order = {p: [ci for ci, c in sorted(
        ((ci, c) for ci, c in enumerate(cat_names)
         if per_totals[p][c] > 0.005),
        key=lambda ic: -per_totals[p][ic[1]])] for p in ("cur", "prev", "six")}
    month_name = MONTH_ABBR[cur[1]]
    partial_note = f" · {month_name} partial" if partial else ""
    periods = [
        {"key": "cur", "label": "This month",
         "win": f"{mon_yr(cur)} · {through}",
         "total": m0(sum(v for v in per_totals["cur"].values() if v > 0))},
        {"key": "six", "label": f"{len(months)} months",
         "win": window_label(months) + partial_note,
         "total": m0(sum(v for v in per_totals["six"].values() if v > 0))},
    ]
    if prev and any(v > 0.005 for v in per_totals["prev"].values()):
        periods.insert(1, {
            "key": "prev", "label": "Last month", "win": mon_yr(prev),
            "total": m0(sum(v for v in per_totals["prev"].values() if v > 0))})
    return {
        "periods": periods, "cats": cats, "order": order,
        "months": [MONTH_ABBR[m] for _, m in months],
        "trendWin": window_label(months) + partial_note,
        "partialIdx": len(months) - 1 if partial else -1,
        "visible": CATS_VISIBLE,
        # jinja-only twins (stripped from the JSON island): the no-JS/print table
        "table_caption": (f"Every category — {periods[0]['win']} vs the "
                          f"{len(months)}-month window."),
        "table_rows": [(c,
                        m0(per_totals["cur"][c])
                        if per_totals["cur"][c] > 0.005 else "—",
                        m0(per_totals["six"][c]))
                       for c in cat_names],
        "six_lbl": window_label(months),
    }


# ----------------------------------------------------- the money-map room
MAP_GROUP_VARS = 5     # named group hues; groups past the 5th share the calm 6th


def _acct_parts(account: str) -> tuple[str, str]:
    """'Assets:US:Vanguard:Brokerage' -> ('Vanguard', 'Brokerage'); the
    2-letter country segment is plumbing, not identity."""
    segs = account.split(":")[1:]
    if segs and re.fullmatch(r"[A-Z]{2}", segs[0]):
        segs = segs[1:]
    inst = segs[0] if segs else account
    return inst, ":".join(segs[1:]) or inst


def _disp(seg: str) -> str:
    """The ledger's glued segments, given air for labels:
    'Checking4321' -> 'Checking 4321', '529Riley' -> '529 Riley'."""
    s = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", seg)
    return re.sub(r"(?<=\d)(?=[A-Z])", " ", s)


def moneymap_data(balances: list[tuple[str, float]],
                  liquid: float) -> dict | None:
    """The treemap's island: every liquid asset dollar by institution →
    account → holding, built from the SAME liquid_balances() rows as the
    headline so the tree and the headline can never disagree. Liabilities
    can't sit in a treemap (negative area); they ride the caption instead:
    tree total + liabilities = the headline, to the dollar."""
    assets = [(a, v) for a, v in balances if a.startswith("Assets") and v > 0]
    neg_assets = sum(v for a, v in balances
                     if a.startswith("Assets") and v < 0)
    liab = sum(v for a, v in balances if a.startswith("Liabilities"))
    if not assets:
        return None
    total = sum(v for _, v in assets)

    # holding-level split, same WHERE clause family as liquid_balances
    excl = illiquid_currency_regex()
    where = "account ~ '^Assets'" + (f" AND NOT currency ~ '{excl}'"
                                     if excl else "")
    rows = query(f"SELECT account, currency, sum(convert(position,'USD')) "
                 f"AS v WHERE {where} GROUP BY account, currency")
    held: dict[str, list[tuple[str, float]]] = {}
    for r in rows:
        v = amount(r["v"], "USD") if "USD" in (r["v"] or "") else 0.0
        if v > 0.005:
            held.setdefault(r["account"], []).append((r["currency"] or "?", v))

    def pcts(v: float) -> str:
        p = 100.0 * v / total
        return "<1%" if p < 0.5 else f"{p:.0f}%"

    groups: dict[str, dict] = {}
    for acct, v in assets:
        inst, leaf = _acct_parts(acct)
        g = groups.setdefault(inst, {"name": _disp(inst), "value": 0.0,
                                     "kids": {}})
        g["value"] += v
        node = g["kids"].setdefault(leaf, {"name": _disp(leaf), "value": 0.0,
                                           "hold": []})
        node["value"] += v
        curs = held.get(acct, [])
        if len(curs) > 1 or (curs and curs[0][0] != "USD"):
            node["hold"] += curs

    tree = []
    ordered = sorted(groups.values(), key=lambda g: -g["value"])
    for gi, g in enumerate(ordered):
        kids = []
        for node in sorted(g["kids"].values(), key=lambda n: -n["value"]):
            children = None
            hold = sorted(node["hold"], key=lambda cv: -cv[1])
            if hold:
                children = [{"name": ("cash" if cur == "USD" else cur),
                             "value": round(v, 2), "amt": m0(v),
                             "pct": pcts(v)} for cur, v in hold]
                rem = node["value"] - sum(v for _, v in hold)
                if rem > 0.005:   # priced part not itemized by currency
                    children.append({"name": "other", "value": round(rem, 2),
                                     "amt": m0(rem), "pct": pcts(rem)})
            kid = {"name": node["name"], "value": round(node["value"], 2),
                   "amt": m0(node["value"]), "pct": pcts(node["value"])}
            if children and len(children) > 1:
                kid["children"] = children
            kids.append(kid)
        tree.append({"name": g["name"], "value": round(g["value"], 2),
                     "amt": m0(g["value"]), "pct": pcts(g["value"]),
                     "cvar": f"--map-{min(gi + 1, MAP_GROUP_VARS + 1)}",
                     "children": kids})
    note_bits = [f"Assets {m0(total)}"]
    if abs(neg_assets) > 0.005:
        note_bits.append(f"in-flight transfers {delta0(neg_assets)}")
    if abs(liab) > 0.005:
        note_bits.append(f"cards & debts {delta0(liab)}")
    caption = " · ".join(note_bits) + f" = {m0(liquid)} liquid net worth"
    return {"tree": tree, "caption": caption, "totalN": round(total, 2)}


# ------------------------------------------------------- the goals room
def edu_grid(total: float, target: float | None, pace: float | None,
             today: date, college_year: int | None) -> dict | None:
    """The 529 what-if slider's precomputed grid: for each $50 step of
    monthly contribution, the straight-line arrival (same glide-path math
    as the card's own footer — today's dollars, market growth ignored) and,
    when a college year is known, how much of the target that pace covers
    by then. JS moves a slider and looks strings up; nothing is computed
    client-side."""
    if not target or target <= 0:
        return None
    steps = list(range(0, EDU_SLIDER_MAX + 1, EDU_SLIDER_STEP))
    months_to = None
    if college_year and college_year > today.year:
        months_to = max(0, (college_year - today.year) * 12 - today.month + 8)
    arrive, cover = [], []
    for c in steps:
        if total >= target:
            arrive.append("already past the target")
            cover.append("fully funded — raise the number or rest easy")
            continue
        if c == 0:
            arrive.append("parked — $0/mo never arrives")
        else:
            need = int(math.ceil((target - total) / c))
            if need > WHATIF_MAX_YEARS * 12:
                arrive.append(f"{WHATIF_MAX_YEARS}+ yrs out at this pace")
            else:
                eta = add_months(today, need)
                arrive.append(f"≈{MONTH_ABBR[eta.month]} {eta.year}")
        if months_to is not None:
            val = total + c * months_to
            p = 100.0 * val / target
            cover.append(f"fully covers the target by {college_year}"
                         if p >= 99.5 else
                         f"≈{p:.0f}% of the target by {college_year}")
        else:
            cover.append("")
    def_idx = (min(range(len(steps)), key=lambda i: abs(steps[i] - pace))
               if pace else 0)
    return {"steps": [f"{m0(c)}/mo" for c in steps], "arrive": arrive,
            "cover": cover, "def": def_idx,
            "paceS": f"≈{m0(pace)}/mo" if pace else None}


# -------------------------------------------------------- glance verdicts
def under_streak(totals: list[tuple[YM, float]], cur: YM) -> int:
    """Consecutive CLOSED months (walking back from the last full month)
    that came in under the median of the up-to-6 full months before each.
    An honest streak or nothing: fewer than MIN_FULL_MONTHS of history
    before a month ends the count."""
    full = [(ym, v) for ym, v in totals if ym < cur]
    n = 0
    for i in range(len(full) - 1, -1, -1):
        window = full[max(0, i - PACE_WINDOW):i]
        if len(window) < MIN_FULL_MONTHS:
            break
        if full[i][1] < median(v for _, v in window):
            n += 1
        else:
            break
    return n


def _sparkline(series: list[dict]) -> dict | None:
    """The net-worth tile's little line: up to SPARK_MONTHS month-ends,
    normalized into a 100×30 viewBox in Python (JS never touches it)."""
    vals = [p["v"] for p in series][-SPARK_MONTHS:]
    if len(vals) < 2:
        return None
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    w, h, pad = 100.0, 30.0, 2.5
    pts = []
    for i, v in enumerate(vals):
        x = pad + (w - 2 * pad) * i / (len(vals) - 1)
        y = pad + (h - 2 * pad) * (1.0 - (v - lo) / span)
        pts.append(f"{x:.1f},{y:.1f}")
    return {"points": " ".join(pts),
            "area": f"{pad:.1f},{h:.1f} " + " ".join(pts) + f" {w - pad:.1f},{h:.1f}",
            "win": f"{len(vals)} month-ends"}


# ----------------------------------------------------- the opt-in room rule
def show_walkaway_room(goals: dict) -> bool:
    """THE ONE RULE for the opt-in Independence room (walk-away number,
    what-if dials, 1871 history replay): an explicit `show_walkaway:
    true/false` in facts/goals always wins; with no flag, the room turns on
    exactly when a `retirement_target` is set. No flag and no target = the
    page never mentions walking away, and none of it is computed. The rule
    is documented in both vault templates' facts/goals files."""
    raw = goals.get("show_walkaway")
    if raw is not None:
        s = str(raw).strip().lower()
        if s in ("true", "yes", "on", "1", "1.0"):
            return True
        if s in ("false", "no", "off", "0", "0.0"):
            return False
    return _as_float(goals.get("retirement_target")) is not None


SMALL_NUMS = ["zero", "One", "Two", "Three", "Four", "Five", "Six",
              "Seven", "Eight", "Nine"]


def sara_line(pace: Pace, cards_state: str, cards: list[Card], more: int,
              daypart: str = "morning") -> str:
    """One warm, honest sentence for the hero. States, never invents."""
    over = (pace.pace_delta or 0) > PACE_BAND * (pace.typical or float("inf"))
    under = (pace.pace_delta or 0) < -PACE_BAND * (pace.typical or 0)
    if cards_state == "none":
        return "First morning here — run the checks and I'll start watching for you."
    n_alerts = sum(1 for c in cards if c.kind == "alert")
    if n_alerts:
        n_lbl = SMALL_NUMS[n_alerts] if n_alerts < 10 else str(n_alerts)
        verb = "wants" if n_alerts == 1 else "want"
        thing = "thing" if n_alerts == 1 else "things"
        rest = "" if n_alerts == 1 else " — the rest wait in Autopilot"
        return (f"{n_lbl} {thing} {verb} a decision this {daypart}. "
                f"Start with the Next line below{rest}.")
    if cards:
        n = len(cards) + more
        s = "s" if n != 1 else ""
        lead = "Spending is running hot, and a" if over else "A"
        return (f"{lead} few small thing{s} could use your hands — "
                f"they're waiting in Autopilot, none of it urgent.")
    if pace.typical is None:
        return "All quiet. A few more months of history and I can show your typical pace."
    if over:
        return ("Nothing needs your hands — but spending is running ahead of "
                "typical. The Spending room tells it straight.")
    if under:
        return ("Nothing needs you, and spending is running under typical. "
                "I checked twice.")
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
  --bg:#fbfaf8; --surface:#ffffff; --surface-2:#f1f0f8;
  --border:rgba(87,74,204,.13); --border-strong:rgba(49,42,124,.22);
  --ink:#151329; --ink-2:#4c4a63; --muted:#6e6c85;
  --grid:#eceaf4; --axis:#cfccdf;
  --accent:#6157ff; --accent-soft:rgba(97,87,255,.10); --link:#4d43e0;
  --pos:#067647; --pos-soft:#e7f9ef;
  --neg:#d02b4c; --neg-soft:#fdedf0; --neg-dot:#f43f5e;
  --warn:#9a5b00; --warn-soft:#fdf3e0; --warn-dot:#f59e0b;
  --edu-track:#d2ecea;
  --edu-fill:linear-gradient(90deg,#0d9488,#2bbcab);
  --ideal:#8d87b8; --code:#efedf8;
  --map-1:#6157ff; --map-2:#eb6834; --map-3:#1baf7a;
  --map-4:#d0326b; --map-5:#4dabf7; --map-6:#8d6708;
  --band-dep:#d02b4c; --band-hold:#8b82c8; --band-ahead:#067647;
  --hero-grad:linear-gradient(115deg,#6157ff 0%,#74c0fc 35%,#ff7eb6 68%,#ffb86b 100%);
  --hero-wash:radial-gradient(120% 90% at 15% 0%,rgba(255,255,255,.16),transparent 48%);
  --hero-scrim:linear-gradient(115deg,rgba(30,22,96,.34) 0%,rgba(30,22,96,.12) 42%,rgba(30,22,96,0) 64%);
  --walk-grad:linear-gradient(180deg,#ffffff,#f3f1ff);
  --walk-border:#e6e3fb;
  --bar-track:#ece9f8; --bar-fill:linear-gradient(90deg,#6157ff,#9d8cff);
  --shadow-card:0 1px 2px rgba(49,42,124,.06),0 10px 30px rgba(76,60,220,.11);
  --shadow-hover:0 4px 10px rgba(49,42,124,.08),0 18px 44px rgba(76,60,220,.17);
  --shadow-float:0 6px 16px rgba(76,60,220,.14),0 26px 60px -10px rgba(76,60,220,.32);
}
@media screen and (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) { color-scheme:dark;
    --bg:#0a0e1e; --surface:#151d33; --surface-2:#1d2540;
    --border:rgba(196,200,255,.16); --border-strong:rgba(196,200,255,.26);
    --ink:#e9ebfa; --ink-2:#b3b8d6; --muted:#8b91b2;
    --grid:#232c47; --axis:#39415f;
    --accent:#8f88ff; --accent-soft:rgba(143,136,255,.16); --link:#a9a3ff;
    --pos:#4ec17e; --pos-soft:rgba(78,193,126,.12);
    --neg:#ff6b84; --neg-soft:rgba(255,107,132,.12); --neg-dot:#ff6b84;
    --warn:#d2a041; --warn-soft:rgba(210,160,65,.13); --warn-dot:#d2a041;
    --edu-track:rgba(43,188,171,.16);
    --edu-fill:linear-gradient(90deg,#0f9c8d,#2bbcab);
    --ideal:#767fa8; --code:#1c2440;
    --map-1:#7f76ff; --map-2:#e06a30; --map-3:#12805a;
    --map-4:#e0559a; --map-5:#3585cc; --map-6:#96761f;
    --band-dep:#f0577a; --band-hold:#7b84cf; --band-ahead:#25a768;
    --hero-grad:linear-gradient(115deg,#5449ec 0%,#3f86d6 35%,#e0559a 68%,#e9953f 100%);
    --hero-wash:radial-gradient(120% 90% at 15% 0%,rgba(255,255,255,.10),transparent 48%);
    --hero-scrim:linear-gradient(115deg,rgba(6,8,30,.42) 0%,rgba(6,8,30,.16) 42%,rgba(6,8,30,0) 64%);
    --walk-grad:linear-gradient(180deg,#161d36,#181d3e);
    --walk-border:#2c3158;
    --bar-track:#242b4c; --bar-fill:linear-gradient(90deg,#6a61f0,#9d8cff);
    --shadow-card:0 1px 2px rgba(0,0,0,.32),0 12px 32px rgba(0,0,0,.38);
    --shadow-hover:0 4px 10px rgba(0,0,0,.35),0 18px 44px rgba(0,0,0,.5);
    --shadow-float:0 8px 20px rgba(24,16,90,.45),0 28px 64px -10px rgba(0,0,0,.6);
  }
}
:root[data-theme="dark"] { color-scheme:dark;
  --bg:#0a0e1e; --surface:#131a2e; --surface-2:#1a2238;
  --border:rgba(196,200,255,.14); --border-strong:rgba(196,200,255,.24);
  --ink:#e9ebfa; --ink-2:#b3b8d6; --muted:#8b91b2;
  --grid:#222b45; --axis:#39415f;
  --accent:#8f88ff; --accent-soft:rgba(143,136,255,.16); --link:#a9a3ff;
  --pos:#3ecf7a; --pos-soft:rgba(62,207,122,.13);
  --neg:#ff6b84; --neg-soft:rgba(255,107,132,.12); --neg-dot:#ff6b84;
  --warn:#f0b13c; --warn-soft:rgba(240,177,60,.13); --warn-dot:#f0b13c;
  --gold:#e2a04b; --gold-track:rgba(226,154,61,.18);
  --gold-fill:linear-gradient(90deg,#c97e1e,#e2a04b);
  --ideal:#767fa8; --code:#1c2440;
  --map-1:#7f76ff; --map-2:#e06a30; --map-3:#12805a;
  --map-4:#e0559a; --map-5:#3585cc; --map-6:#96761f;
  --band-dep:#f0577a; --band-hold:#7b84cf; --band-ahead:#25a768;
  --hero-grad:linear-gradient(115deg,#5449ec 0%,#3f86d6 35%,#e0559a 68%,#e9953f 100%);
  --hero-wash:radial-gradient(120% 90% at 15% 0%,rgba(255,255,255,.10),transparent 48%);
  --hero-scrim:linear-gradient(115deg,rgba(6,8,30,.42) 0%,rgba(6,8,30,.16) 42%,rgba(6,8,30,0) 64%);
  --walk-grad:linear-gradient(180deg,#161d36,#181d3e);
  --walk-border:#2c3158;
  --bar-track:#242b4c; --bar-fill:linear-gradient(90deg,#6a61f0,#9d8cff);
  --shadow-card:0 1px 2px rgba(0,0,0,.32),0 12px 32px rgba(0,0,0,.38);
  --shadow-hover:0 4px 10px rgba(0,0,0,.35),0 18px 44px rgba(0,0,0,.5);
  --shadow-float:0 8px 20px rgba(24,16,90,.45),0 28px 64px -10px rgba(0,0,0,.6);
}

* { box-sizing:border-box; margin:0; }
html { -webkit-text-size-adjust:100%; }
body { background:var(--bg); color:var(--ink);
  font:15px/1.5 'Inter',system-ui,-apple-system,'Segoe UI',sans-serif; }
.wrap { max-width:1240px; margin:0 auto; padding:0 28px; }
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
/* full-bleed, full-saturation; tall enough that the KPI strip and the top
   of the first card row plunge into color, the way the approved mock does.
   The slow pan lives on the wash layer so the gradient itself never zooms
   (a scaled-up gradient crops its orange endpoint and reads washed out). */
.hero { background:var(--hero-grad); color:#fff; position:relative;
  padding:42px 0 176px; }
.hero::before { content:""; position:absolute; inset:0;
  background:var(--hero-wash); background-size:220% 190%;
  background-position:0% 0%; pointer-events:none; }
.hero::after { content:""; position:absolute; inset:0;
  background:var(--hero-scrim); pointer-events:none; }
.hero-in { position:relative; z-index:1; }
.hero-top { display:flex; justify-content:space-between; align-items:flex-start;
  gap:20px; }
.hi { font-size:13px; font-weight:600; letter-spacing:.05em;
  text-transform:uppercase; text-shadow:0 1px 2px rgba(21,15,74,.30); }
.say { margin-top:10px; font-size:19.5px; line-height:1.48; font-weight:600;
  max-width:62ch; letter-spacing:-.005em; text-wrap:balance;
  text-shadow:0 1px 3px rgba(21,15,74,.32); }
.hero-side { display:flex; align-items:center; gap:12px; flex:none; }
.stamp { font-size:11.5px; line-height:1.5; color:#fff; text-align:right;
  background:rgba(21,15,74,.40); padding:5px 12px; border-radius:10px; }
.themebtn { background:rgba(21,15,74,.45); color:#fff;
  border:1px solid rgba(255,255,255,.55); border-radius:999px; padding:3px 12px;
  font:12.5px 'Inter',system-ui,sans-serif; cursor:pointer;
  transition:background .15s ease; }
.themebtn:hover { background:rgba(21,15,74,.60); }
.card .themebtn { background:var(--surface);
  color:var(--ink-2); border-color:var(--border); }
.card .themebtn:hover {
  border-color:var(--border-strong); background:var(--surface); }

/* ---- the glance: four verdict tiles floating over the aurora ---- */
.tiles { display:grid; grid-template-columns:repeat(4,1fr); background:var(--surface);
  border-radius:16px; box-shadow:var(--shadow-float); border:1px solid var(--border);
  margin-top:-118px; position:relative; z-index:2; padding:18px 0; }
.tile { padding:4px 22px 2px; border-left:1px solid var(--grid); min-width:0;
  position:relative; display:flex; flex-direction:column; }
.tile:first-child { border-left:none; }
.tk { font-size:11.5px; font-weight:600; color:var(--muted);
  text-transform:uppercase; letter-spacing:.05em; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; }
/* verdict words lead, in large type; the number is the second line */
.tv-big { font-size:25px; font-weight:600; letter-spacing:-.018em;
  line-height:1.16; margin-top:4px; text-wrap:balance; }
.tv-big.good { color:var(--pos); } .tv-big.bad { color:var(--neg); }
.tv-big.warn { color:var(--warn); }
.kv { font-size:27px; font-weight:600; letter-spacing:-.02em; line-height:1.15;
  margin-top:4px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.tfig { font-size:13px; color:var(--ink-2); margin-top:3px; }
.tfig b { font-weight:600; font-variant-numeric:tabular-nums; }
.tsub { font-size:11.5px; color:var(--muted); margin-top:2px; }
/* genuinely good news earns a quiet halo — never confetti */
.tile.glow::after { content:""; position:absolute; inset:6px 10px;
  border-radius:12px; pointer-events:none;
  background:radial-gradient(120% 90% at 50% 0%,var(--pos-soft),transparent 70%);
  z-index:-1; }
.streak { display:inline-flex; align-items:center; gap:5px; margin-top:7px;
  align-self:flex-start; background:var(--pos-soft); color:var(--pos);
  border-radius:999px; padding:2px 9px; font-size:11px; font-weight:600; }
.dots { display:flex; gap:5px; margin-top:9px; flex-wrap:wrap; }
.dots i { width:9px; height:9px; border-radius:50%; background:var(--axis); }
.dots i.ok { background:var(--pos); } .dots i.watch { background:var(--warn-dot); }
.dots i.bad { background:var(--neg-dot); } .dots i.mut { background:var(--axis); }
.spark { display:block; width:100%; height:44px; margin-top:8px; }
.spark polyline { fill:none; stroke:var(--accent); stroke-width:2px;
  vector-effect:non-scaling-stroke; stroke-linecap:round; stroke-linejoin:round; }
.spark polygon { fill:var(--accent); opacity:.12; stroke:none; }
.chip.mini { padding:2px 9px; font-size:11.5px; margin-top:6px;
  align-self:flex-start; }

/* ---- the ONE next line ---- */
.next { display:flex; align-items:baseline; gap:14px; margin-top:16px;
  background:var(--surface); border:1px solid var(--border); border-radius:14px;
  box-shadow:var(--shadow-card); padding:15px 22px; position:relative; z-index:2; }
.next .nk { flex:none; font-size:11.5px; font-weight:600; color:var(--accent);
  text-transform:uppercase; letter-spacing:.06em; padding:3px 10px;
  background:var(--accent-soft); border-radius:999px; }
.next.allquiet .nk { color:var(--pos); background:var(--pos-soft); }
.next .nt { font-size:16px; font-weight:600; line-height:1.4; min-width:0; }
.next .nm { margin-left:auto; flex:none; color:var(--muted); font-size:12px;
  white-space:nowrap; }

/* ---- the rooms: tab bar + one open panel ---- */
.tabbar { display:flex; gap:6px; margin:22px auto 0; padding:5px;
  background:var(--surface-2); border:1px solid var(--border);
  border-radius:999px; width:max-content; max-width:100%; overflow-x:auto; }
.tab { border:0; background:none; color:var(--ink-2); font:600 13.5px/1
  'Inter',system-ui,sans-serif; padding:9px 18px; border-radius:999px;
  cursor:pointer; white-space:nowrap; transition:background .15s ease,
  color .15s ease; }
.tab:hover { color:var(--ink); }
.tab[aria-selected="true"] { background:var(--surface); color:var(--ink);
  box-shadow:0 1px 3px rgba(49,42,124,.14); }
.room[hidden] { display:none; }
.roomhead { color:var(--muted); font-size:12.5px; margin-top:18px;
  max-width:76ch; }

/* ---- spending room: period chips + clickable category rail + drill ---- */
.periodbar { display:flex; gap:6px; align-items:center; flex-wrap:wrap; }
.pchip { border:1px solid var(--border-strong); background:var(--surface);
  color:var(--ink-2); font:600 12.5px/1 'Inter',system-ui,sans-serif;
  border-radius:999px; padding:7px 14px; cursor:pointer;
  transition:background .15s ease,color .15s ease; }
.pchip:hover { color:var(--ink); }
.pchip[aria-pressed="true"] { background:var(--accent); color:#fff;
  border-color:var(--accent); }
.periodwin { margin-left:auto; color:var(--muted); font-size:12px;
  white-space:nowrap; }
.catrail { margin-top:12px; }
.catrow { display:grid; grid-template-columns:minmax(96px,150px) 1fr auto;
  gap:14px; align-items:center; width:100%; padding:8px 10px; border:0;
  background:none; border-radius:10px; cursor:pointer; text-align:left;
  font:inherit; color:inherit; }
.catrow:hover { background:var(--accent-soft); }
.catrow[aria-pressed="true"] { background:var(--accent-soft); }
.catrow .name { font-size:13.5px; font-weight:600; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.catrow[aria-pressed="true"] .name { color:var(--link); }
.catrow .track { margin:0; height:9px; }
.catrow .amt { font-size:12.5px; color:var(--ink-2);
  font-variant-numeric:tabular-nums; white-space:nowrap; }
.catrow .amt b { font-weight:600; color:var(--ink); }
.morecats { margin:6px 0 0 10px; border:0; background:none; color:var(--muted);
  font:12.5px 'Inter',system-ui,sans-serif; cursor:pointer; padding:4px 0;
  text-decoration:underline; text-underline-offset:2px; }
.morecats:hover { color:var(--ink-2); }
.drill h3 { font-size:15px; font-weight:600; letter-spacing:-.01em; }
.drill .dwin { color:var(--muted); font-size:12px; }
#trend-chart { height:180px; }
.merch { list-style:none; margin:8px 0 0; padding:0; }
.merch li { display:flex; justify-content:space-between; gap:12px;
  padding:7px 0; border-top:1px solid var(--grid); font-size:12.5px;
  color:var(--ink-2); align-items:baseline; }
.merch li:first-child { border-top:none; }
.merch b { font-weight:600; font-variant-numeric:tabular-nums;
  color:var(--ink); white-space:nowrap; }
.merch .mn { min-width:0; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; }
.merchmore { color:var(--muted); font-size:12px; margin-top:8px; }

/* ---- money map ---- */
#map-chart { height:400px; }
.mapcap { color:var(--ink-2); font-size:12.5px; margin-top:10px;
  font-variant-numeric:tabular-nums; }
.maphint { color:var(--muted); font-size:12px; margin-top:2px; }

/* ---- goals room: the 529 what-if slider ---- */
.eduwhatif { margin-top:16px; padding-top:14px; border-top:1px solid var(--grid); }
.eduwhatif .dial { max-width:420px; }
.edu-out { font-size:21px; font-weight:600; letter-spacing:-.015em;
  margin-top:10px; }
.edu-out2 { color:var(--ink-2); font-size:13px; margin-top:3px; }

/* ---- cards + grids ---- */
main { padding-bottom:44px; }
/* start, not stretch: a short card (the machine) self-sizes instead of
   dragging dead space to match its tall neighbor */
.grid { display:grid; gap:18px; margin-top:18px; align-items:start; }
.g-pace { grid-template-columns:1.5fr 1fr; }
.g-spend { grid-template-columns:1.15fr 1fr; }
.g-needs { grid-template-columns:1.5fr 1fr; }
.g-walk { grid-template-columns:1.4fr 1fr; }
.g-chesh { grid-template-columns:1.4fr 1fr; }
.g-solo { grid-template-columns:1fr; }
.g-nwt { grid-template-columns:1.55fr 1fr; }
.sidecol { display:flex; flex-direction:column; gap:18px; min-width:0;
  align-self:start; }
.card { background:var(--surface); border:1px solid var(--border);
  border-radius:16px; padding:20px 22px; box-shadow:var(--shadow-card);
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

/* life-event toggles + the walk-away history disclosure */
.events { border:1px solid var(--grid); border-radius:12px; margin:14px 0 0;
  padding:8px 14px 12px; }
.evhead { color:var(--muted); font-size:12px; padding:0 6px; }
.evrow { display:flex; gap:10px; align-items:baseline; padding:7px 0 0;
  font-size:13px; line-height:1.5; color:var(--ink-2); cursor:pointer; }
.evrow input { accent-color:var(--accent); flex:none; width:15px; height:15px;
  transform:translateY(2px); cursor:pointer; }
.evrow b { color:var(--ink); font-weight:600; }
.evrow.evdone { color:var(--muted); cursor:default; }
.evsel { font:inherit; font-size:12.5px; font-variant-numeric:tabular-nums;
  color:var(--ink); background:var(--surface-2);
  border:1px solid var(--border-strong); border-radius:7px; padding:2px 5px; }
.evsrc { color:var(--muted); font-size:11.5px; }
.evnote { margin:9px 0 0; max-width:66ch; }
.survline { color:var(--ink-2); font-size:12.5px; margin-top:9px; max-width:68ch; }
.survline b { font-weight:600; }
.wi-bands { margin-top:14px; padding-top:12px; border-top:1px solid var(--grid); }
.wi-bands > summary { cursor:pointer; color:var(--muted); }
.wi-bands > summary:hover { color:var(--ink-2); }
#band-chart { height:216px; }
.bandlede { color:var(--ink-2); font-size:13px; margin-top:12px; max-width:68ch; }
.bandlede b { font-weight:600; }
.bandlegend { margin-top:4px; }
.sw.band-dep { background:var(--band-dep); }
.sw.band-hold { background:var(--band-hold); }
.sw.band-ahead { background:var(--band-ahead); }
.bandcap { color:var(--muted); font-size:12px; margin-top:8px; max-width:68ch; }
.guardline { color:var(--ink-2); font-size:12.5px; margin-top:8px; max-width:68ch; }
.guardline b { font-weight:600; }

/* net-worth attribution: why it moved */
.attr { margin-top:12px; }
.attr-row { margin-top:7px; }
.attr-line { font-size:12.5px; color:var(--ink-2); max-width:76ch; }
.attr-line b { font-weight:600; font-size:13px; }
.attr-win { color:var(--muted); }
.attr-sup { color:var(--muted); font-size:12px; margin-top:8px; max-width:72ch; }
.attrbar { display:flex; gap:2px; height:8px; border-radius:999px;
  overflow:hidden; margin-top:5px; max-width:420px; }
.attrseg { min-width:6px; }
.attrseg.mkt { background:var(--accent); }
.attrseg.in { background:var(--pos); }
.attrseg.out { background:var(--neg); }

/* vs-the-thesis drift strip */
.drift { margin-top:2px; }
.driftrow { display:grid; grid-template-columns:minmax(104px,max-content) 1fr;
  gap:3px 12px; padding:9px 0 10px; border-top:1px solid var(--grid);
  align-items:center; }
.driftrow:first-child { border-top:none; padding-top:4px; }
.dlabel { font-size:13px; font-weight:600; }
.dtrack { position:relative; height:8px; border-radius:999px;
  background:var(--bar-track); }
.dfill { position:absolute; top:0; bottom:0; left:0; border-radius:999px;
  background:var(--axis); }
.dfill.over, .dfill.under { background:var(--warn-dot); }
.dtick { position:absolute; top:-3px; bottom:-3px; width:2px;
  background:var(--ink-2); border-radius:1px; opacity:.75; }
.dnum { grid-column:2; font-size:12px; color:var(--muted); }
.dnum b { color:var(--ink-2); font-size:12.5px; font-weight:600; }
.dnum b.over, .dnum b.under, .ddelta { color:var(--warn); font-weight:600; }
.concline { color:var(--ink-2); font-size:12.5px; margin-top:12px;
  max-width:68ch; }

/* stats (cheshbon) */
.statrow { display:flex; gap:28px; flex-wrap:wrap; margin-top:10px;
  align-items:flex-end; }
.stat .v { font-size:22px; font-weight:600; letter-spacing:-.01em; }
.stat .l { color:var(--muted); font-size:12px; margin-bottom:2px; }
/* the band chart's legend (shared swatch-row pattern) */
.riblegend { display:flex; flex-wrap:wrap; gap:6px 16px; margin-top:16px; }
.riblegend .li { display:inline-flex; align-items:baseline; gap:6px;
  font-size:12px; color:var(--ink-2); }
.riblegend .sw { width:9px; height:9px; border-radius:3px; flex:none;
  align-self:center; }
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

/* ---- quiet delight: all motion behind prefers-reduced-motion, and the
   entrance uses a backwards fill only, so the resting state (print, reduced
   motion, JS-less) is always the fully visible one ---- */
@keyframes rise { from { opacity:0; transform:translateY(10px); } }
@keyframes aurora-pan { from { background-position:0% 0%; }
  to { background-position:100% 60%; } }
@media (prefers-reduced-motion: no-preference) {
  .hero::before { animation:aurora-pan 48s ease-in-out infinite alternate; }
  .tiles { animation:rise .5s cubic-bezier(.2,.7,.3,1) backwards; }
  .next { animation:rise .5s cubic-bezier(.2,.7,.3,1) .06s backwards; }
  .tabbar { animation:rise .5s cubic-bezier(.2,.7,.3,1) .12s backwards; }
  .room .card { animation:rise .5s cubic-bezier(.2,.7,.3,1) backwards; }
  .room .grid:nth-of-type(1) .card { animation-delay:.05s; }
  .room .grid:nth-of-type(2) .card { animation-delay:.1s; }
  .room .grid:nth-of-type(3) .card { animation-delay:.15s; }
  .card { transition:transform .18s ease, box-shadow .18s ease; }
  .card:hover { transform:translateY(-2px); box-shadow:var(--shadow-hover); }
  .lanes li, .needs li, .moves li { transition:background .15s ease; }
}
.lanes li, .needs li, .moves li { border-radius:10px;
  margin-inline:-10px; padding-inline:10px; }
.lanes li:hover, .needs li:hover, .moves li:hover {
  background:var(--accent-soft); }

@media (max-width:960px) {
  .g-pace, .g-spend, .g-needs, .g-walk, .g-chesh, .g-nwt {
    grid-template-columns:1fr; }
  .tiles { grid-template-columns:1fr 1fr; row-gap:14px; }
  .tile:nth-child(3) { border-left:none; }
  .hero-top { flex-direction:column; }
  .hero-side { align-self:flex-start; flex-direction:row-reverse; }
  .stamp { text-align:left; }
}
@media (max-width:560px) {
  .wrap { padding:0 16px; }
  .hero { padding-top:24px; padding-bottom:124px; }
  .tiles { grid-template-columns:1fr 1fr; padding:12px 0; margin-top:-88px; }
  .tile { padding:2px 16px; }
  .tv-big { font-size:20px; }
  .kv { font-size:21px; }
  .phero { font-size:33px; }
  .statrow { gap:18px; }
  .dials { grid-template-columns:1fr; }
  /* phone: the tab bar becomes a full-width segmented control that scrolls */
  .tabbar { width:auto; border-radius:14px; scrollbar-width:none; }
  .tabbar::-webkit-scrollbar { display:none; }
  .tab { padding:9px 13px; border-radius:10px; }
  .next { flex-wrap:wrap; }
  .next .nm { margin-left:0; width:100%; }
  .catrow { grid-template-columns:1fr auto; }
  .catrow .track { display:none; }
  .periodwin { width:100%; margin-left:0; }
  /* the lane tag column steals a third of a phone row — drop it to an
     inline chip under the lane's name instead */
  .lanes li { flex-wrap:wrap; }
  .lanes li > div { flex:1; min-width:72%; }
  .lanetag { order:4; width:max-content; margin-left:19px; padding:2px 9px;
    background:var(--surface-2); border-radius:999px; }
}
@media (prefers-reduced-motion: reduce) {
  * { transition:none !important; animation:none !important; } }
@media print {
  body { background:#fff; }
  .themebtn, .tv, .tabbar, .morecats { display:none; }
  /* print = the glance + every room, expanded in order */
  .room[hidden] { display:block; }
  .room { break-before:page; }
  .card, .tiles, .next { border-color:#ddd; box-shadow:none; animation:none; }
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
  function fade(hex, a) {  // '#rrggbb' -> 'rgba(r,g,b,a)' for gradient stops
    var h = hex.replace('#', '');
    if (h.length === 3) h = h[0] + h[0] + h[1] + h[1] + h[2] + h[2];
    var n = parseInt(h, 16);
    return 'rgba(' + (n >> 16 & 255) + ',' + (n >> 8 & 255) + ',' +
      (n & 255) + ',' + a + ')';
  }
  function areaGrad(varName, top) {  // soft vertical wash under a data line
    return { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
      { offset: 0, color: fade(cssv(varName), top) },
      { offset: 1, color: fade(cssv(varName), 0) }] };
  }
  function byId(id) { return document.getElementById(id); }
  function el(tag, cls, text) {   // untrusted strings enter via textContent only
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function isVisible(elm) {
    return !!(elm && elm.offsetWidth > 0 && elm.offsetHeight > 0);
  }

  // theme: auto -> light -> dark, persisted; charts re-skin on every change
  var root = document.documentElement;
  var btn = byId('themebtn');
  var order = ['auto', 'light', 'dark'];
  var theme = 'auto';
  try { theme = localStorage.getItem('sara-home-theme') || 'auto'; } catch (e) {}
  if (order.indexOf(theme) < 0) theme = 'auto';
  function applyTheme(t) {
    if (t === 'auto') root.removeAttribute('data-theme');
    else root.setAttribute('data-theme', t);
    if (btn) btn.textContent = t === 'auto' ? '◑ auto'
      : (t === 'light' ? '☀ light' : '☾ dark');
    try { localStorage.setItem('sara-home-theme', t); } catch (e) {}
    disposeAll();
    syncCharts();
  }
  if (btn) btn.addEventListener('click', function () {
    theme = order[(order.indexOf(theme) + 1) % order.length];
    applyTheme(theme);
  });
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: dark)')
      .addEventListener('change', function () {
        if (theme === 'auto') { disposeAll(); syncCharts(); }
      });
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

  // ---- chart builders: one per container id, built only when visible ----
  function buildPace(elm) {
    var P = DATA.pace;
    if (!P) return null;
    var c = echarts.init(elm, null, { renderer: 'svg' });
    var opt = baseOption();
    // narrow screens wrap the legend onto two lines — the grid starts
    // below it so legend text never overprints the top y-axis label
    opt.grid = { left: 62, right: 16, top: elm.clientWidth < 520 ? 88 : 38,
                 bottom: 28 };
    opt.legend = legendBox();
    opt.xAxis = catAxis(P.days, P.xint, elm.clientWidth);
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
      lineStyle: { width: 3, cap: 'round' },
      areaStyle: { color: areaGrad('--accent', 0.22) },
      emphasis: { disabled: true }, z: 2
    });
    if (P.now) opt.series.push(nowDot(P.now.xy, P.now.label, P.now.side));
    c.setOption(opt);
    return c;
  }

  function buildNW(elm) {
    var N = DATA.nw;
    if (!N) return null;
    var c2 = echarts.init(elm, null, { renderer: 'svg' });
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
    o2.xAxis = catAxis(N.labels, N.xint, elm.clientWidth);
    o2.yAxis = valAxis(N.y);
    o2.tooltip.formatter = function (ps) {
      return tipHtml(N.tips[ps[0].dataIndex]);
    };
    var market = {
      name: 'at market prices', type: 'line', data: N.market, symbol: 'none',
      color: cssv('--accent'),
      lineStyle: { width: 3, cap: 'round' },
      areaStyle: { color: areaGrad('--accent', 0.22) },
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
    return c2;
  }

  // ---- the spending room: period chips + clickable categories + drill ---
  var SP = DATA.spending;
  var spState = { p: 'cur', ci: -1, all: false };
  if (SP) {
    if (!SP.order[spState.p] || !SP.order[spState.p].length) {
      for (var pi = 0; pi < SP.periods.length; pi++) {
        if (SP.order[SP.periods[pi].key].length) {
          spState.p = SP.periods[pi].key;
          break;
        }
      }
    }
    var o0 = SP.order[spState.p] || [];
    spState.ci = o0.length ? o0[0] : -1;
  }

  function setTrend(c) {
    if (!SP || spState.ci < 0) return;
    var cat = SP.cats[spState.ci];
    var opt = baseOption();
    opt.grid = { left: 56, right: 12, top: 14, bottom: 26 };
    opt.xAxis = {
      type: 'category', data: SP.months,
      axisLine: { lineStyle: { color: cssv('--axis') } },
      axisTick: { show: false },
      axisLabel: { color: cssv('--muted'), fontSize: 11, fontFamily: FONT }
    };
    opt.yAxis = valAxis(cat.y);
    opt.tooltip.formatter = function (ps) {
      return tipHtml(cat.tips[ps[0].dataIndex]);
    };
    opt.series = [{
      type: 'bar',
      data: cat.series.map(function (v, i) {
        return i === SP.partialIdx           // the in-progress month, faded
          ? { value: v, itemStyle: { opacity: 0.45 } } : v;
      }),
      barMaxWidth: 34,
      itemStyle: { color: cssv('--accent'), borderRadius: [4, 4, 0, 0] },
      emphasis: { disabled: true }
    }];
    c.setOption(opt, true);
  }
  function buildTrend(elm) {
    if (!SP || spState.ci < 0) return null;
    var c = echarts.init(elm, null, { renderer: 'svg' });
    setTrend(c);
    return c;
  }

  function renderSpending() {
    if (!SP || !byId('catrail')) return;
    var ord = SP.order[spState.p] || [];
    if (ord.indexOf(spState.ci) < 0) spState.ci = ord.length ? ord[0] : -1;
    var per = null;
    SP.periods.forEach(function (pp) { if (pp.key === spState.p) per = pp; });
    [].slice.call(document.querySelectorAll('.pchip')).forEach(function (b) {
      b.setAttribute('aria-pressed', b.dataset.p === spState.p ? 'true' : 'false');
    });
    var winEl = byId('period-win');
    if (winEl && per) winEl.textContent = per.total + ' · ' + per.win;
    var rail = byId('catrail');
    rail.textContent = '';
    var showN = spState.all ? ord.length : Math.min(ord.length, SP.visible);
    ord.slice(0, showN).forEach(function (ci) {
      var cat = SP.cats[ci], pd = cat.per[spState.p];
      if (!pd) return;
      var b = el('button', 'catrow');
      b.type = 'button';
      b.setAttribute('aria-pressed', ci === spState.ci ? 'true' : 'false');
      b.appendChild(el('span', 'name', cat.name));
      var tr = el('div', 'track');
      var f = el('span', 'fill');
      f.style.width = pd.w + '%';
      tr.appendChild(f);
      b.appendChild(tr);
      var amt = el('span', 'amt');
      amt.appendChild(el('b', null, pd.amt));
      amt.appendChild(document.createTextNode(' · ' + pd.pct));
      b.appendChild(amt);
      b.addEventListener('click', function () {
        spState.ci = ci;
        renderSpending();
      });
      rail.appendChild(b);
    });
    var moreBtn = byId('morecats');
    if (moreBtn) {
      var hid = ord.length - showN;
      moreBtn.hidden = hid <= 0 && !spState.all;
      moreBtn.textContent = spState.all ? 'Show fewer'
        : '+ ' + hid + ' smaller categor' + (hid === 1 ? 'y' : 'ies');
    }
    // the drill panel: this category, this period
    if (spState.ci >= 0) {
      var cat2 = SP.cats[spState.ci];
      var pd2 = cat2.per[spState.p] || { amt: '$0', pct: '—', merch: [], more: 0 };
      var nameEl = byId('drill-name');
      if (nameEl) nameEl.textContent = cat2.name;
      var dwin = byId('drill-win');
      if (dwin && per) dwin.textContent = pd2.amt + ' · ' + per.win;
      var ml = byId('merch');
      if (ml) {
        ml.textContent = '';
        pd2.merch.forEach(function (nv) {
          var li = el('li');
          li.appendChild(el('span', 'mn', nv[0]));
          li.appendChild(el('b', null, nv[1]));
          ml.appendChild(li);
        });
        if (!pd2.merch.length) {
          var li0 = el('li');
          li0.appendChild(el('span', 'mn', 'no merchants in this window'));
          ml.appendChild(li0);
        }
      }
      var mm = byId('merchmore');
      if (mm) {
        mm.hidden = !pd2.more;
        mm.textContent = pd2.more
          ? '+ ' + pd2.more + ' smaller merchant' + (pd2.more === 1 ? '' : 's')
            + ' — the ledger has receipts' : '';
      }
    }
    if (charts['trend-chart']) setTrend(charts['trend-chart']);
    else syncCharts();
  }

  // ---- the money map: institution -> account -> holding treemap ---------
  function mapNode(n, color) {
    var out = { name: n.name, value: n.value,
                amt: n.amt, pct: n.pct };
    if (color) out.itemStyle = { color: color };
    if (n.children) {
      out.children = n.children.map(function (k) { return mapNode(k, null); });
    }
    return out;
  }
  function buildMap(elm) {
    var M = DATA.map;
    if (!M) return null;
    var c = echarts.init(elm, null, { renderer: 'svg' });
    var opt = baseOption();
    opt.tooltip.trigger = 'item';
    opt.tooltip.formatter = function (p) {
      var d = p.data || {};
      if (!d.amt) return '';
      return tipHtml({ t: p.treePathInfo
        ? p.treePathInfo.map(function (t) { return t.name; })
            .filter(Boolean).join(' › ') : d.name,
        rows: [[d.amt, (d.pct || '') + ' of assets']] });
    };
    opt.series = [{
      type: 'treemap', name: 'everything',
      data: M.tree.map(function (g) { return mapNode(g, cssv(g.cvar)); }),
      roam: false, nodeClick: 'zoomToNode', leafDepth: 2,
      left: 0, right: 0, top: 4, bottom: 34,
      breadcrumb: { show: true, left: 'center', bottom: 0, height: 22,
        itemStyle: { color: cssv('--surface-2'),
                     borderColor: cssv('--border-strong'), borderWidth: 1,
                     textStyle: { color: cssv('--ink-2'), fontFamily: FONT,
                                  fontSize: 11.5 } },
        emphasis: { itemStyle: { color: cssv('--accent-soft') } } },
      label: { show: true, fontFamily: FONT, fontSize: 12,
        formatter: function (p) {
          var d = p.data || {};
          return d.amt ? d.name + String.fromCharCode(10) + d.amt + ' · ' + d.pct
            : p.name;
        } },
      itemStyle: { borderColor: cssv('--surface'), borderWidth: 2,
                   gapWidth: 2 },
      levels: [
        { itemStyle: { borderWidth: 0, gapWidth: 3 } },
        { colorAlpha: [0.92, 1],
          itemStyle: { gapWidth: 2, borderWidth: 2,
                       borderColorSaturation: 0.55 },
          upperLabel: { show: true, height: 22, fontFamily: FONT,
                        fontSize: 11.5, fontWeight: 600, color: '#fff' } },
        { colorAlpha: [0.68, 0.88] }
      ],
      emphasis: { label: { show: true } }
    }];
    c.setOption(opt);
    return c;
  }

  // ---- the goals room: 529 what-if slider (grid lookups only) -----------
  var EG = DATA.edu;
  var eduEls = { c: byId('edu-c'), v: byId('edu-c-v'),
                 a: byId('edu-arrive'), k: byId('edu-cover') };
  function eduUpdate() {
    if (!EG || !eduEls.c) return;
    var i = Math.max(0, Math.min(EG.steps.length - 1, +eduEls.c.value || 0));
    eduEls.v.textContent = EG.steps[i];
    eduEls.c.setAttribute('aria-valuetext', EG.steps[i]);
    if (eduEls.a) eduEls.a.textContent = EG.arrive[i];
    if (eduEls.k) {
      eduEls.k.textContent = EG.cover[i] || '';
      eduEls.k.hidden = !EG.cover[i];
    }
  }
  if (EG && eduEls.c) eduEls.c.addEventListener('input', eduUpdate);

  // ---- what-if dials + life events: JS only INDEXES the Python grid ----
  // (keys into DATA.whatif are concatenated index digits; every dollar
  // string and every replay count was precomputed server-side)
  var WI = DATA.whatif;
  var wiChart = null, bandChart = null;
  var wiEls = {
    rate: byId('wi-rate'), spend: byId('wi-spend'), growth: byId('wi-growth'),
    rateV: byId('wi-rate-v'), spendV: byId('wi-spend-v'), growthV: byId('wi-growth-v'),
    target: byId('wi-target'), gap: byId('wi-gap'), pct: byId('wi-pct'),
    years: byId('wi-years'), coast: byId('wi-coast'), surv: byId('wi-surv'),
    fold: byId('wi-fold'), reset: byId('wi-reset'), chart: byId('wi-chart'),
    evHouse: byId('ev-house'), evHouseA: byId('ev-house-a'), evHouseY: byId('ev-house-y'),
    evPartner: byId('ev-partner'), evCollege: byId('ev-college'), evNote: byId('ev-note'),
    bands: byId('wi-bands'), bandChart: byId('band-chart'), bandLede: byId('band-lede'),
    guard: byId('guard-line'), unspent: byId('unspent-line'), bandTable: byId('band-table')
  };
  function calyr(years, short) {  // Python-made calendar labels, by offset
    return (short ? WI.xyearS : WI.xyear)[String(Math.round(years))] || '';
  }
  function wiState() {
    var s = {
      ri: +wiEls.rate.value, si: +wiEls.spend.value, gi: +wiEls.growth.value,
      h: wiEls.evHouse && wiEls.evHouse.checked ? 1 : 0,
      p: wiEls.evPartner && wiEls.evPartner.checked ? 1 : 0,
      c: (wiEls.evCollege && wiEls.evCollege.checked
          && !wiEls.evCollege.disabled) ? 1 : 0
    };
    s.hKey = s.h ? 'h1a' + (+wiEls.evHouseA.value) + 'y' + (+wiEls.evHouseY.value)
      : 'h0';
    s.pKey = s.p ? '1' : '0';
    s.cKey = s.c ? '1' : '0';
    s.pathKey = s.hKey + 'c' + s.cKey;
    s.anyEv = !!(s.h || s.p || s.c);
    return s;
  }
  function wiDots(dots) {  // milestone/event dots: coords + strings precomputed
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
    var s = wiState();
    var ri = s.ri, si = s.si, gi = s.gi;
    var T = WI.tg[s.pKey];
    wiEls.rateV.textContent = WI.rates[ri];
    wiEls.spendV.textContent = WI.spends[si];
    wiEls.growthV.textContent = WI.growths[gi];
    wiEls.rate.setAttribute('aria-valuetext', WI.rates[ri]);
    wiEls.spend.setAttribute('aria-valuetext', WI.spends[si]);
    wiEls.growth.setAttribute('aria-valuetext', WI.growths[gi]);
    wiEls.target.textContent = T.target[ri][si];
    wiEls.gap.textContent = T.gap[ri][si];
    wiEls.pct.textContent = T.pct[ri][si];
    if (wiEls.evNote) wiEls.evNote.hidden = !s.anyEv;
    var pot = T.targetN[ri][si];
    var y = WI.years[s.hKey + 'p' + s.pKey + 'c' + s.cKey][ri][si][gi];
    wiEls.years.textContent = y === null ? 'not within ' + WI.maxYears + ' yrs'
      : (y === 0 ? 'already there' : '≈' + y + ' yrs');
    var c = s.anyEv ? null : WI.coast[ri][si][gi];  // coast: base state only
    if (wiEls.coast) {
      wiEls.coast.textContent =
        s.anyEv || c === 0 ? '' :
        c === null ? ('Growth alone would not get there within ' +
                      WI.maxYears + ' yrs — the saving is doing the lifting.') :
        ('Even with $0 more saved, growth alone gets there in ≈' + c +
         ' yrs (' + calyr(c, false) + ').');
      wiEls.coast.hidden = !wiEls.coast.textContent;
    }
    // the history check: survival depends on the rate alone (pot-normalized)
    var S = WI.surv;
    if (wiEls.surv) {
      if (pot > 0) {
        wiEls.surv.innerHTML = 'History check: at a ' + esc(WI.rates[ri]) +
          ' draw, <b>' + S.survived[ri] + ' of ' + S.nSeq +
          '</b> of history’s 40-year retirements finished with money ' +
          'left. The verdict fold below has the whole picture.';
        wiEls.surv.hidden = false;
      } else {
        wiEls.surv.hidden = true;
      }
    }
    var ml = {
      symbol: 'none', silent: true,
      label: { fontFamily: FONT, fontSize: 11 },
      data: pot > 0 ? [{
        yAxis: pot,
        lineStyle: { color: cssv('--ink-2'), type: 'dashed', width: 1 },
        label: { formatter: 'target ' + T.target[ri][si],
                 position: 'insideEndTop', color: cssv('--ink-2') }
      }] : []
    };
    var dots = [];
    if (!s.anyEv) {                       // milestones: base state only
      var h = WI.half[ri][si][gi];
      if (h !== null && h > 0 && (y === null || h < y)) dots.push({
        coord: [h, WI.halfN[ri][si]], lbl: 'halfway ' + calyr(h, true),
        tipT: 'halfway — ' + WI.halfS[ri][si], tipY: h,
        label: { position: 'right' }
      });
    }
    if (y !== null && y > 0 && pot > 0) dots.push({
      coord: [y, pot], lbl: 'target ' + calyr(y, true),
      tipT: 'target — ' + T.target[ri][si], tipY: y
    });
    var evDots = WI.pathDots[s.pathKey][gi];
    for (var di = 0; di < evDots.length; di++) {
      dots.push({
        coord: evDots[di].coord, lbl: evDots[di].lbl,
        tipT: 'life event ' + evDots[di].lbl, tipY: evDots[di].coord[0],
        label: { position: 'bottom' },
        itemStyle: { color: cssv('--warn-dot'), borderColor: cssv('--surface'),
                     borderWidth: 2 }
      });
    }
    var coastDots = [];
    if (!s.anyEv && c !== null && c > 0 && pot > 0) coastDots.push({
      coord: [c, pot], lbl: '$0-saved ' + calyr(c, true),
      tipT: 'growth alone reaches ' + T.target[ri][si], tipY: c
    });
    var tips = WI.pathTips[s.pathKey][gi];
    var opt = baseOption();
    // narrow screens wrap the legend onto two lines — start the grid lower
    opt.grid = { left: 62, right: 20,
                 top: wiEls.chart.clientWidth < 520 ? 66 : 36, bottom: 26 };
    opt.legend = legendBox();
    opt.xAxis = xValAxis(WI.maxYears, WI.xticks);
    opt.yAxis = valAxis(WI.ymapsPool[WI.ymapIdx[s.pathKey + 'p' + s.pKey][gi]]);
    opt.tooltip.formatter = function (ps) {
      var i = ps[0].dataIndex;
      var rows = [[tips[i], 'saving as usual']];
      if (!s.anyEv) rows.push([WI.paths0Tips[gi][i], 'growth alone, $0 saved']);
      return tipHtml({ t: i === 0 ? 'today' : 'in ' + i + ' years',
                       rows: rows });
    };
    opt.series = [{
      name: 'saving as usual', type: 'line', data: WI.paths[s.pathKey][gi],
      symbol: 'none', lineStyle: { width: 3, color: cssv('--accent') },
      areaStyle: { color: areaGrad('--accent', 0.22) },
      emphasis: { disabled: true }, z: 2, markLine: ml,
      markPoint: wiDots(dots)
    }];
    if (!s.anyEv) opt.series.push({
      name: 'growth alone, $0 saved', type: 'line', data: WI.paths0[gi],
      symbol: 'none', color: cssv('--ideal'),
      lineStyle: { width: 2, type: 'dotted' },
      emphasis: { disabled: true }, z: 1,
      markPoint: wiDots(coastDots)
    });
    wiChart.setOption(opt, true);
    bandsUpdate(s, pot);
  }
  function bandsUpdate(s, pot) {
    var S = WI.surv;
    if (!S || !wiEls.bands) return;
    var ri = s.ri, si = s.si;
    var B = S.bands[ri];
    if (wiEls.bandLede) {
      wiEls.bandLede.innerHTML = pot > 0
        ? ('At a ' + esc(WI.rates[ri]) + ' draw from a pot of ' +
           esc(WI.tg[s.pKey].target[ri][si]) + ': <b>' + S.survived[ri] +
           ' of ' + S.nSeq + '</b> starts finished the 40 years with money ' +
           'left · <b>' + B[B.length - 1][2] + '</b> ended 2×+ ' +
           'ahead · <b>' + B[B.length - 1][0] + '</b> ran out.')
        : 'The paycheck covers this spend — no pot is being drawn down.';
    }
    var gT = S.guardTrig[s.pKey][ri][si];
    var gC = S.guardCut[s.pKey][ri][si];
    var un = S.unspent[s.pKey][ri][si];
    if (wiEls.guard) {
      wiEls.guard.hidden = !(pot > 0);
      wiEls.guard.innerHTML = !(pot > 0) ? '' : (gT
        ? ('The rescue, sized on history’s worst cases: if the pot ever ' +
           'fell to <b>' + esc(gT) + '</b>, trimming spending to <b>' +
           esc(gC) + '</b> until it recovered would have saved every start ' +
           'year since 1871.')
        : ('Every one of the ' + S.nSeq + ' start years made it at this ' +
           'draw — no spending trim was ever needed.'));
    }
    if (wiEls.unspent) {
      wiEls.unspent.hidden = !(pot > 0 && un);
      wiEls.unspent.innerHTML = (pot > 0 && un)
        ? ('The flip side: in the median surviving run you’d end with ' +
           '<b>' + esc(un) + '</b> unspent after 40 years — oversaving ' +
           'is a risk too.')
        : '';
    }
    if (wiEls.bandTable) {
      var rows = '';
      for (var k = 4; k < B.length; k += 5) {
        rows += '<tr><td>' + (k + 1) + '</td><td>' + B[k][0] + '</td><td>' +
          B[k][1] + '</td><td>' + B[k][2] + '</td></tr>';
      }
      wiEls.bandTable.innerHTML = rows;
    }
    if (!bandChart) return;
    function seriesOf(idx, nm, colorVar) {
      var data = [[0, idx === 1 ? S.nSeq : 0]];  // at walk-away: all holding
      for (var k = 0; k < B.length; k++) data.push([k + 1, B[k][idx]]);
      return {
        name: nm, type: 'line', stack: 'starts', data: data, symbol: 'none',
        color: cssv(colorVar), lineStyle: { width: 0 },
        areaStyle: { color: cssv(colorVar), opacity: 1 },
        emphasis: { disabled: true }
      };
    }
    var o = baseOption();
    o.grid = { left: 56, right: 14, top: 12, bottom: 26 };
    o.xAxis = {
      type: 'value', min: 0, max: B.length,
      axisLabel: { color: cssv('--muted'), fontSize: 11, fontFamily: FONT,
                   formatter: function (v) { return S.bandX[String(v)] || ''; } },
      interval: 10, splitLine: { show: false },
      axisLine: { lineStyle: { color: cssv('--axis') } },
      axisTick: { show: false }
    };
    o.yAxis = {
      type: 'value', min: 0, max: S.nSeq, interval: S.bandY.step,
      axisLabel: { color: cssv('--muted'), fontSize: 11, fontFamily: FONT,
                   formatter: function (v) { return S.bandY.labels[String(v)] || ''; } },
      splitLine: { show: false }, axisLine: { show: false },
      axisTick: { show: false }
    };
    o.tooltip.formatter = function (ps) {
      var i = ps[0].dataIndex;         // 0 = walk-away day; B is years 1..40
      if (!i) return tipHtml({ t: 'at walk-away', rows:
        [[String(S.nSeq), 'starts, all holding']] });
      return tipHtml({ t: i + (i > 1 ? ' years in' : ' year in'),
                       rows: [[String(B[i - 1][0]), 'had run out'],
                              [String(B[i - 1][1]), 'still holding'],
                              [String(B[i - 1][2]), '2×+ ahead']] });
    };
    // depleted pinned to the baseline so the scary share reads instantly
    o.series = [seriesOf(0, 'ran out', '--band-dep'),
                seriesOf(1, 'still holding', '--band-hold'),
                seriesOf(2, '2×+ ahead', '--band-ahead')];
    bandChart.setOption(o, true);
  }
  function buildWi(elm) {
    if (!WI) return null;
    wiChart = echarts.init(elm, null, { renderer: 'svg' });
    wiUpdate();
    return wiChart;
  }
  function buildBand(elm) {
    if (!WI) return null;
    bandChart = echarts.init(elm, null, { renderer: 'svg' });
    if (wiChart) wiUpdate();
    return bandChart;
  }
  if (WI && wiEls.rate) {
    [wiEls.rate, wiEls.spend, wiEls.growth].forEach(function (elm) {
      elm.addEventListener('input', wiUpdate);
    });
    [wiEls.evHouse, wiEls.evPartner, wiEls.evCollege,
     wiEls.evHouseA, wiEls.evHouseY].forEach(function (elm) {
      if (elm) elm.addEventListener('change', wiUpdate);
    });
    if (wiEls.reset) wiEls.reset.addEventListener('click', function () {
      wiEls.rate.value = WI.def.ri;
      wiEls.spend.value = WI.def.si;
      wiEls.growth.value = WI.def.gi;
      if (wiEls.evHouse) wiEls.evHouse.checked = false;
      if (wiEls.evPartner) wiEls.evPartner.checked = false;
      if (wiEls.evCollege && !wiEls.evCollege.disabled)
        wiEls.evCollege.checked = false;
      if (wiEls.evHouseA) wiEls.evHouseA.value = String(WI.ev.houseDefA);
      if (wiEls.evHouseY) wiEls.evHouseY.value = String(WI.ev.houseDefY);
      wiUpdate();
    });
    if (wiEls.fold) {
      // phones start with the dials folded; the chart sizes itself when opened
      if (window.matchMedia && window.matchMedia('(max-width:640px)').matches)
        wiEls.fold.removeAttribute('open');
      wiEls.fold.addEventListener('toggle', function () {
        if (wiEls.fold.open) { syncCharts(); wiUpdate(); }
      });
    }
    if (wiEls.bands) wiEls.bands.addEventListener('toggle', function () {
      if (wiEls.bands.open) { syncCharts(); wiUpdate(); }
    });
  }

  // ---- the chart registry: build lazily, only what's visible ------------
  var builders = {
    'pace-chart': buildPace, 'trend-chart': buildTrend,
    'nw-chart': buildNW, 'map-chart': buildMap,
    'wi-chart': buildWi, 'band-chart': buildBand
  };
  var charts = {};
  function disposeAll() {
    for (var id in charts) if (charts[id]) charts[id].dispose();
    charts = {};
    wiChart = null;
    bandChart = null;
  }
  function syncCharts() {
    for (var id in builders) {
      var elm = byId(id);
      if (!elm || !isVisible(elm)) continue;
      if (charts[id]) charts[id].resize();
      else {
        var c = builders[id](elm);
        if (c) charts[id] = c;
      }
    }
  }

  // ---- the rooms: tab bar, hash deep-links, keyboard, print-expand ------
  var tabs = [].slice.call(document.querySelectorAll('.tab'));
  var rooms = {};
  tabs.forEach(function (t) { rooms[t.dataset.room] = byId('room-' + t.dataset.room); });
  var curRoom = tabs.length ? tabs[0].dataset.room : null;
  function openRoom(key, setHash) {
    if (!rooms[key]) key = tabs.length ? tabs[0].dataset.room : null;
    if (!key) return;
    curRoom = key;
    tabs.forEach(function (t) {
      var on = t.dataset.room === key;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      if (rooms[t.dataset.room]) rooms[t.dataset.room].hidden = !on;
    });
    if (setHash) {
      try { history.replaceState(null, '', '#' + key); }
      catch (e) { location.hash = key; }
    }
    syncCharts();
  }
  tabs.forEach(function (t) {
    t.addEventListener('click', function () { openRoom(t.dataset.room, true); });
  });
  var tabbar = byId('tabbar');
  if (tabbar) tabbar.addEventListener('keydown', function (ev) {
    var i = tabs.findIndex(function (t) { return t.dataset.room === curRoom; });
    var to = -1;
    if (ev.key === 'ArrowRight') to = (i + 1) % tabs.length;
    else if (ev.key === 'ArrowLeft') to = (i - 1 + tabs.length) % tabs.length;
    else if (ev.key === 'Home') to = 0;
    else if (ev.key === 'End') to = tabs.length - 1;
    if (to >= 0) {
      ev.preventDefault();
      openRoom(tabs[to].dataset.room, true);
      tabs[to].focus();
    }
  });
  window.addEventListener('hashchange', function () {
    var k = (location.hash || '').replace('#', '');
    if (rooms[k] && k !== curRoom) openRoom(k, false);
  });
  window.addEventListener('beforeprint', function () {
    for (var k in rooms) if (rooms[k]) rooms[k].hidden = false;
    syncCharts();
  });
  window.addEventListener('afterprint', function () { openRoom(curRoom, false); });

  // ---- boot -------------------------------------------------------------
  var boot = (location.hash || '').replace('#', '');
  openRoom(rooms[boot] ? boot : curRoom, false);
  [].slice.call(document.querySelectorAll('.pchip')).forEach(function (b) {
    b.addEventListener('click', function () {
      spState.p = b.dataset.p;
      renderSpending();
    });
  });
  renderSpending();
  eduUpdate();
  applyTheme(theme);

  var rt = null;
  window.addEventListener('resize', function () {
    clearTimeout(rt);
    rt = setTimeout(function () {
      for (var id in charts) charts[id].resize();
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
<main>
<div class="wrap">
<div class="tiles" role="group" aria-label="Today's verdicts">
  <div class="tile{{ ' glow' if g.spend.glow }}">
    <div class="tk">Spending</div>
    <div class="tv-big{{ ' ' + g.spend.cls if g.spend.cls }}">{{ g.spend.verdict }}</div>
    <div class="tfig num">{{ g.spend.fig }}</div>
    <div class="tsub">{{ g.spend.sub }}</div>
    {% if g.spend.streak %}<span class="streak">✦ {{ g.spend.streak }}</span>{% endif %}
  </div>
  <div class="tile{{ ' glow' if g.nw.glow }}">
    <div class="tk">Net worth</div>
    <div class="kv num">{{ g.nw.v }}</div>
    {% if g.nw.chip %}<span class="chip mini{{ ' ' + g.nw.chip.cls if g.nw.chip.cls }}">{{ g.nw.chip.body }}</span>{% endif %}
    {% if g.nw.spark %}
    <svg class="spark" viewBox="0 0 100 30" preserveAspectRatio="none" role="img"
         aria-label="Liquid net worth trend, {{ g.nw.spark.win }}">
      <polygon points="{{ g.nw.spark.area }}"></polygon>
      <polyline points="{{ g.nw.spark.points }}"></polyline></svg>
    {% endif %}
    <div class="tsub">{{ g.nw.sub }}</div>
  </div>
  <div class="tile">
    <div class="tk">Autopilot</div>
    <div class="tv-big{{ ' ' + g.auto.cls if g.auto.cls }}">{{ g.auto.verdict }}</div>
    {% if g.auto.dots %}
    <div class="dots" role="img" aria-label="{{ g.auto.aria }}">
      {% for d in g.auto.dots %}<i class="{{ d }}"></i>{% endfor %}
    </div>
    {% endif %}
    <div class="tsub">{{ g.auto.sub }}</div>
  </div>
  <div class="tile">
    <div class="tk">{{ g.edu.label }}</div>
    <div class="tv-big{{ ' ' + g.edu.cls if g.edu.cls }}">{{ g.edu.verdict }}</div>
    {% if g.edu.fig %}<div class="tfig num">{{ g.edu.fig }}</div>{% endif %}
    <div class="tsub">{{ g.edu.sub }}</div>
  </div>
</div>
<div class="next{{ ' allquiet' if nxt.quiet }}">
  <span class="nk">{{ nxt.label }}</span>
  <p class="nt">{{ nxt.text }}</p>
  {% if nxt.meta %}<span class="nm num">{{ nxt.meta }}</span>{% endif %}
</div>
<nav class="tabbar" id="tabbar" role="tablist" aria-label="Rooms">
  <button class="tab" role="tab" data-room="spending" id="tab-spending"
    aria-controls="room-spending" aria-selected="true">Spending</button>
  <button class="tab" role="tab" data-room="map" id="tab-map"
    aria-controls="room-map" aria-selected="false" tabindex="-1">Money map</button>
  <button class="tab" role="tab" data-room="goals" id="tab-goals"
    aria-controls="room-goals" aria-selected="false" tabindex="-1">Goals</button>
  <button class="tab" role="tab" data-room="autopilot" id="tab-autopilot"
    aria-controls="room-autopilot" aria-selected="false" tabindex="-1">Autopilot</button>
  {% if indy %}
  <button class="tab" role="tab" data-room="independence" id="tab-independence"
    aria-controls="room-independence" aria-selected="false" tabindex="-1">Independence</button>
  {% endif %}
</nav>
</div>

<section class="room wrap" id="room-spending" role="tabpanel" aria-labelledby="tab-spending">
{% if sp %}
<div class="grid g-spend">
<section class="card">
  {{ cardhead('Where the money goes', 'Click a category — I keep the receipts.') }}
  <div class="periodbar" role="group" aria-label="Time window">
    {% for pp in sp.periods %}
    <button class="pchip" type="button" data-p="{{ pp.key }}"
      aria-pressed="{{ 'true' if loop.first else 'false' }}">{{ pp.label }}</button>
    {% endfor %}
    <span class="periodwin num" id="period-win"></span>
  </div>
  <div class="catrail" id="catrail"></div>
  <button class="morecats" id="morecats" type="button" hidden></button>
  {{ tablewin(sp.table_caption, ['Category', 'This month', sp.six_lbl], sp.table_rows) }}
</section>
<section class="card drill">
  <div class="cardhead">
    <div><h2 class="ck">Up close</h2>
    <h3 id="drill-name"></h3></div>
    <span class="window num" id="drill-win"></span>
  </div>
  <div id="trend-chart" class="chart" role="img"
       aria-label="Monthly spend for the selected category, {{ sp.trendWin }}"></div>
  <p class="dwin">month by month · {{ sp.trendWin }}</p>
  <ul class="merch" id="merch"></ul>
  <p class="merchmore" id="merchmore" hidden></p>
</section>
</div>
{% endif %}
<div class="grid {{ 'g-pace' if (wins or ch) else 'g-solo' }}">
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
<div class="sidecol">
{% if ch %}
<section class="card">
  {{ cardhead(ch.title, 'The month’s cheshbon — the monthly review closes the book.', ch.window) }}
  <div class="statrow num">
    <div class="stat"><div class="l">in</div><div class="v">{{ ch.inc }}</div></div>
    <div class="stat"><div class="l">out</div><div class="v">{{ ch.exp }}</div></div>
    <div class="stat"><div class="l">net</div><div class="v {{ ch.net_cls }}">{{ ch.net }}</div></div>
  </div>
  {% if ch.payday_note %}<p class="barnotes" style="margin-top:8px">{{ ch.payday_note }}</p>{% endif %}
  {% if ch.closed %}<p class="goalfoot">{{ ch.closed.month }}, closed: in {{ ch.closed.inc }} · out {{ ch.closed.exp }} · net {{ ch.closed.net }}{{ ch.wink }}</p>{% endif %}
</section>
{% endif %}
{% if wins %}
<section class="card">
  {{ cardhead("Sara's finds", 'Treasure only — every dollar here was already found or saved.', wins.year) }}
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
</section>

<section class="room wrap" id="room-map" role="tabpanel" aria-labelledby="tab-map" hidden>
<div class="grid g-solo">
<section class="card">
  {{ cardhead('Where every dollar sits', 'The whole liquid pot, drawn to scale. Click a box to zoom in; the trail below climbs back out.', map.window if map else '') }}
  {% if map %}
  <div id="map-chart" class="chart" role="img"
       aria-label="Treemap of liquid assets by institution, account, and holding"></div>
  <p class="mapcap">{{ map.caption }}</p>
  {{ tablewin('Every liquid account, at the latest prices on file.',
              ['Account', 'Balance'], map.table_rows) }}
  {% else %}
  <div class="empty"><b>Nothing to map yet.</b> Import a statement and every
  dollar gets a box here, drawn to scale.</div>
  {% endif %}
</section>
</div>
<div class="grid g-nwt">
<section class="card">
  {{ cardhead('Net worth — the long line', nw.sub, nw.window) }}
  <p class="heromini num">{{ nw.liquid }}</p>
  <p class="herolab">liquid net worth — {{ nw.asof }}</p>
  {% if nw.delta %}<div class="chiprow"><span class="chip{{ ' ' + nw.delta.cls if nw.delta.cls }}">{{ nw.delta.body }}</span></div>{% endif %}
  {% if attr %}
  <div class="attr">
    {% for a in attr.rows %}
    {% if a.suppressed %}
    <p class="attr-sup"><span class="attr-win num">{{ a.window }}</span> · {{ a.suppressed }}</p>
    {% else %}
    <div class="attr-row">
      <p class="attr-line"><span class="attr-win num">{{ a.window }}</span>
        <b class="num{{ ' ' + a.cls if a.cls }}">{{ a.delta }}</b>
        <span class="attr-body num">— {{ a.body }}{{ a.note }}</span></p>
      {% if a.segs %}
      <div class="attrbar" role="img" aria-label="{{ a.aria }}">
        {% for s in a.segs %}<span class="attrseg {{ s.cls }}" style="width:{{ s.width }}%"></span>{% endfor %}
      </div>
      {% endif %}
    </div>
    {% endif %}
    {% endfor %}
  </div>
  {% endif %}
  {% if nw.has_chart %}
  <div id="nw-chart" class="chart" role="img" aria-label="Liquid net worth by month"></div>
  {{ tablewin('Liquid net worth at each month end; the endpoint is the headline at the latest prices.',
              ['Month', 'Liquid net worth', 'Basis'], nw.table_rows) }}
  {% else %}
  <div class="empty"><b>Not enough history for a line yet.</b> Two month-ends
  in the ledger and the curve appears.</div>
  {% endif %}
</section>
<section class="card">
  {{ cardhead('Vs the thesis', thesis.sub or 'The written target mix, scored against the live portfolio.', thesis.window or '') }}
  {% if thesis.nudge %}
  <div class="nudge">{{ thesis.nudge }}</div>
  {% else %}
  <div class="drift">
    {% for r in thesis.rows %}
    <div class="driftrow">
      <span class="dlabel">{{ r.label }}</span>
      <div class="dtrack"><span class="dfill{{ ' ' + r.state if r.state }}" style="width:{{ r.fill }}%"></span><span class="dtick" style="left:{{ r.tick }}%"></span></div>
      <span class="dnum num"><b class="{{ r.state }}">{{ r.now }}</b> <span class="dtarget">{{ r.target }} {{ r.band }}</span>{% if r.delta %} <span class="ddelta {{ r.state }}">{{ r.delta }}</span>{% endif %}</span>
    </div>
    {% endfor %}
  </div>
  {% if thesis.conc %}<p class="concline num">{{ thesis.conc }}</p>{% endif %}
  {% if thesis.notes %}<p class="goalfoot">{{ thesis.notes }}</p>{% endif %}
  {{ tablewin('Current allocation vs the written targets, over invested dollars.',
              ['Class', 'Value', 'Now', 'Target'],
              thesis.rows | map(attribute='trow') | list) }}
  {% endif %}
</section>
</div>
</section>

<section class="room wrap" id="room-goals" role="tabpanel" aria-labelledby="tab-goals" hidden>
<div class="grid {{ 'g-chesh' if env_rows else 'g-solo' }}">
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
  {% if edu.grid %}
  <div class="eduwhatif">
    <span class="ck">Turn the dial · what-if, not advice</span>
    <div class="dial" style="margin-top:10px">
      <label for="edu-c">monthly contribution</label>
      <span class="dval num" id="edu-c-v"></span>
      <input id="edu-c" type="range" min="0" max="{{ edu.grid_max }}" step="1" value="{{ edu.grid.def }}">
    </div>
    <p class="edu-out num" id="edu-arrive"></p>
    <p class="edu-out2 num" id="edu-cover" hidden></p>
    <p class="goalfoot">Straight-line at today's prices, market growth ignored —
    same math as the pace line above{% if edu.grid.paceS %} (you're at {{ edu.grid.paceS }} now){% endif %}.
    The dial is a toy; the 529's paperwork is the decision.</p>
  </div>
  {% endif %}
  {% endif %}
</section>
{% if env_rows %}
<section class="card">
  {{ cardhead('Projects', projects_sub) }}
  {% for e in env_rows %}{{ meterrow('#' + e.tag, e.width, e.amt, e.over) }}{% endfor %}
</section>
{% endif %}
</div>
</section>

<section class="room wrap" id="room-autopilot" role="tabpanel" aria-labelledby="tab-autopilot" hidden>
<div class="grid g-needs">
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
<div class="sidecol">
<section class="card">
  {{ cardhead('Needs you', needs.sub) }}
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
{% if moves or plumbing %}
<section class="card">
  {{ cardhead('Money that must move', 'Payments and transfers with a date already on them.', 'next ' ~ mustmove_days ~ ' days') }}
  {% if moves %}
  <ul class="moves">
    {% for mv in moves %}
    <li><span class="mvdate num{{ ' near' if mv.near }}">{{ mv.day_lbl }}</span>
    <span class="mvamt num">{{ mv.amt }}</span>
    <span class="mvtext">{{ mv.text }} · {{ mv.when }}</span></li>
    {% endfor %}
  </ul>
  {% else %}
  <p class="empty">Nothing for your hands — what's left is plumbing, below.</p>
  {% endif %}
  {% if plumbing %}
  <details class="tv"><summary>{{ plumbing|length }} plumbing move{{ 's' if plumbing|length != 1 }} — money shuffling between your own accounts</summary>
  <ul class="moves">
    {% for mv in plumbing %}
    <li><span class="mvdate num{{ ' near' if mv.near }}">{{ mv.day_lbl }}</span>
    <span class="mvamt num">{{ mv.amt }}</span>
    <span class="mvtext">{{ mv.text }} · {{ mv.when }}</span></li>
    {% endfor %}
  </ul></details>
  {% endif %}
</section>
{% endif %}
</div>
</div>
</section>

{% if indy %}
<section class="room wrap" id="room-independence" role="tabpanel" aria-labelledby="tab-independence" hidden>
<div class="grid g-solo">
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
    <p class="survline" id="wi-surv" hidden></p>
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
    <fieldset class="events">
      <legend class="evhead">Life events — flip what’s coming</legend>
      <label class="evrow"><input type="checkbox" id="ev-house">
        <span class="evtext">We buy a home —
          <select id="ev-house-a" class="evsel" aria-label="down payment">
            {% for a in wi.house_amts %}<option value="{{ loop.index0 }}"{{ ' selected' if loop.index0 == wi.house_def_a }}>{{ a }}</option>{% endfor %}
          </select> down, in
          <select id="ev-house-y" class="evsel" aria-label="purchase year">
            {% for y in wi.house_years %}<option value="{{ loop.index0 }}"{{ ' selected' if loop.index0 == wi.house_def_y }}>{{ y }}</option>{% endfor %}
          </select>
          <span class="evsrc">({{ wi.house_src }})</span></span></label>
      {% if wi.partner_lbl %}
      <label class="evrow"><input type="checkbox" id="ev-partner">
        <span class="evtext">{{ wi.partner_lbl }}</span></label>
      {% endif %}
      {% if wi.college_lbl %}
      <label class="evrow{{ ' evdone' if wi.college_covered }}"><input type="checkbox" id="ev-college"{{ ' disabled' if wi.college_covered }}>
        <span class="evtext">{{ wi.college_lbl }}</span></label>
      {% endif %}
      <p class="evsrc evnote" id="ev-note" hidden>With a life event on, the
      halfway/coast extras step aside — the curve, the target line and the
      history check carry the story.</p>
    </fieldset>
    <div class="wi-actions"><button id="wi-reset" class="themebtn" type="button">↺ reset to your numbers</button></div>
    <div id="wi-chart" class="chart" role="img"
         aria-label="Hypothetical projection of liquid net worth toward the dialed target"></div>
  </details>
  <details class="wi-bands" id="wi-bands">
    <summary><span class="ck">If you walked away with this — history’s verdict</span></summary>
    <p class="bandlede num" id="band-lede"></p>
    <div id="band-chart" class="chart chart-sm" role="img"
         aria-label="For each year of a 40-year retirement, how many historical start years had run out of money, were still holding, or were two times ahead or more"></div>
    <div class="riblegend bandlegend">
      <span class="li"><span class="sw band-dep" aria-hidden="true"></span>ran out</span>
      <span class="li"><span class="sw band-hold" aria-hidden="true"></span>still holding</span>
      <span class="li"><span class="sw band-ahead" aria-hidden="true"></span>2×+ ahead</span>
    </div>
    <p class="bandcap">{{ wi.bands_cap }}</p>
    <p class="guardline num" id="guard-line"></p>
    <p class="guardline num" id="unspent-line"></p>
    <details class="tv"><summary>View as table</summary>
    <table><caption>Of the {{ wi.n_seq }} historical starts ({{ wi.surv_window }}), how many had run out, were holding, or sat 2×+ ahead, by years into retirement — at the dialed withdrawal rate.</caption>
    <thead><tr><th>Years in</th><th>Ran out</th><th>Holding</th><th>2×+ ahead</th></tr></thead>
    <tbody id="band-table"></tbody></table></details>
  </details>
  <details class="wi-how"><summary>How it works</summary>
  <p class="wi-formula">{{ wi.formula }}</p></details>
  </div>
  {% endif %}
  {% endif %}
</section>
</div>
</section>
{% endif %}

<div class="wrap">
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
</div>
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
        # hero color follows the verdict tile's band: inside ±PACE_BAND of
        # typical the number stays neutral — green/red only when it means it
        band = PACE_BAND * pace.typical
        if pace.fallback:
            d = round(pace.spent - pace.typical)
            hero = (f"{m0(abs(d))} {'under' if d < 0 else 'over'}"
                    if d else "on pace")
            hero_cls = "good" if d < -band else ("bad" if d > band else "")
            herolab = (f"a typical month's total — {month_full} closed at "
                       f"{m0(pace.spent)} against a typical {m0(pace.typical)}")
        elif pace.pace_delta is not None:
            d = round(pace.pace_delta)
            hero = (f"{m0(abs(d))} {'under' if d < 0 else 'over'}"
                    if d else "on pace")
            hero_cls = "good" if d < -band else ("bad" if d > band else "")
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


def _spend_tile(pace: Pace, streak: int) -> dict:
    """Verdict first, number second: Under pace / On pace / Running hot,
    judged against the typical path with a ±PACE_BAND band. The streak chip
    appears only when ≥ 2 closed months ran under their own typical."""
    month_name = MONTH_ABBR[pace.cur[1]]
    tile = {"verdict": "Finding pace", "cls": "", "fig": "", "glow": False,
            "sub": f"needs {MIN_FULL_MONTHS} full months for a verdict",
            "streak": ""}
    if pace.typical is None:
        if pace.daily_cum:
            tile["fig"] = f"{m0(pace.spent)} spent so far"
        else:
            tile["sub"] = "import a statement and the verdict starts"
        return tile
    band = PACE_BAND * pace.typical
    if pace.fallback:
        d, vs, when = (pace.spent - pace.typical, "a typical month",
                       f"{month_name}, the latest imported month")
    elif pace.pace_delta is not None:
        d, vs, when = (pace.pace_delta, "the typical path",
                       f"through {month_name} {pace.through_day}")
    else:
        d, vs, when = 0.0, "the typical path", f"{month_name} · nothing spent yet"
    if d > band:
        tile.update(verdict="Running hot", cls="bad")
    elif d < -band:
        tile.update(verdict="Under pace", cls="good", glow=True)
    else:
        tile.update(verdict="On pace", cls="")
    tile["fig"] = (f"{m0(abs(round(d)))} {'under' if d < 0 else 'over'} {vs}"
                   if round(d) else f"right on {vs}")
    tile["sub"] = f"{when} · typical {month_name} runs {m0(pace.typical)}"
    if streak >= 2:
        tile["streak"] = f"{streak} closed months under typical"
    return tile


def _auto_tile(mach: dict) -> dict:
    """The machine, as one verdict + one dot per lane."""
    rows = mach["rows"]
    n = len(rows)
    if not n:
        return {"verdict": "Nothing wired", "cls": "", "dots": [], "aria": "",
                "sub": "tell Sara your standing orders and I'll watch them"}
    green = sum(1 for r in rows if r["dot"] == "ok")
    broken = sum(1 for r in rows if r["dot"] == "bad")
    watching = sum(1 for r in rows if r["dot"] == "watch")
    if broken:
        verdict, cls = (f"{broken} need{'s' if broken == 1 else ''} a look",
                        "bad")
        sub = "the machine wants a hand — details in Autopilot"
    elif watching:
        verdict, cls = f"{green} of {n} green", ""
        sub = f"{watching} watching for a first arrival"
    else:
        verdict, cls = f"All {n} green", "good"
        sub = "paychecks, invests, floors — all landing"
    return {"verdict": verdict, "cls": cls, "sub": sub,
            "dots": [r["dot"] for r in rows],
            "aria": f"{green} of {n} lanes green"}


def _humanize_verb(verb: str) -> str:
    """The Next line is the page's most human slot: strip a leading
    "Check `tools/run x`; " style command prefix (check-authored fix text)
    down to the plain-words action that follows it."""
    m = re.match(r"^(?:Check|Run)\s+`[^`]+`\s*[;,:]\s*(.+)$", verb.strip())
    if not m:
        return verb
    rest = m.group(1).strip()
    return rest[:1].upper() + rest[1:] if rest else verb


def _next_ctx(needs_state: str, cards: list[Card], more: int,
              moves_human: list[dict]) -> dict:
    """THE one next action under the tiles: the top alert, else the nearest
    dated obligation (deadline card or money that must move), else honest
    quiet. Everything else waits inside the rooms."""
    alerts = [c for c in cards if c.kind == "alert"]
    if alerts:
        return {"label": "Next", "quiet": False,
                "text": _codespans(_humanize_verb(alerts[0].verb)),
                "meta": alerts[0].why[:80]}
    dated_cards = [c for c in cards
                   if c.kind == "deadline" and c.days is not None]
    best_card = (min(dated_cards, key=lambda c: c.days or 0)
                 if dated_cards else None)
    best_move = (min(moves_human, key=lambda mv: mv["days"])
                 if moves_human else None)
    if best_card is not None and (best_move is None
                                  or (best_card.days or 0) <= best_move["days"]):
        return {"label": "Next", "quiet": False,
                "text": _codespans(best_card.verb), "meta": best_card.why}
    if best_move is not None:
        # the bullet text carries its own $ figure (must_move requires one)
        return {"label": "Next", "quiet": False,
                "text": _codespans(best_move["text"]),
                "meta": f"due {best_move['day_lbl']} · {best_move['when']}"}
    if needs_state == "none":
        return {"label": "First run", "quiet": True,
                "text": "Ask Sara to run the checks — this line fills itself.",
                "meta": ""}
    if cards:
        n = len(cards) + more
        return {"label": "All quiet", "quiet": True,
                "text": (f"Nothing urgent — {n} small thing"
                         f"{'s' if n != 1 else ''} idling in Autopilot."),
                "meta": ""}
    return {"label": "All quiet", "quiet": True,
            "text": "Nothing needs you today. Go be a person.", "meta": ""}


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
    """The 529 card (Goals room) AND its glance tile, one source of truth.
    `tile` = {label, verdict, cls, fig, sub}; `grid` = the what-if slider's
    precomputed table when a target exists."""
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
                "empty": empty, "grid": None,
                "tile": {"label": "College fund", "cls": "",
                         "verdict": ("Target set, no 529" if target
                                     else "No 529 yet"),
                         "fig": "",
                         "sub": "open one and the story starts here"}}
    total = sum(a.value for a in accounts)
    kids = ", ".join(sorted({a.kid for a in accounts}))
    college_year, _src = _college_year(today, accounts)
    title = f"{kids}’s 529" if len(accounts) == 1 else "The 529s"
    grid = edu_grid(total, target, pace, today, college_year)
    tile = {"label": title, "verdict": "", "cls": "",
            "fig": f"{m0(total)} saved", "sub": ""}
    ctx = {
        "title": title,
        "sub": "Education savings — the long game with a date on it.",
        "empty": "", "value": m0(total),
        "val_lbl": ("valued at cost — no market price on file yet"
                    if any(a.at_cost for a in accounts)
                    else "at the latest prices on file"),
        "fill": None, "pct": "", "of": "saved", "nudge": None, "foot": None,
        "perkid": [], "grid": grid,
        "grid_max": (len(grid["steps"]) - 1) if grid else 0, "tile": tile,
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
            late = college_year is not None and eta.year > college_year
            tile.update(verdict=f"On pace for ≈{eta.year}",
                        cls="warn" if late else "good",
                        sub=(f"college is {college_year} — worth a look" if late
                             else f"of {m0(target)} at ≈{m0(pace)}/mo"))
        elif total >= target:
            ctx["foot"] = "Past the target — time to raise it, or rest easy."
            tile.update(verdict="Past the target", cls="good",
                        sub=f"of {m0(target)} — raise it or rest easy")
        else:
            ctx["foot"] = ("No regular contributions detected yet — no arrival "
                           "date to forecast.")
            tile.update(verdict="No pace yet",
                        sub="no regular contributions detected")
    else:
        pace_bit = f" Contributions are running ≈{m0(pace)}/mo." if pace else ""
        ctx["nudge"] = (f"No target set yet — pick the college number with "
                        f"Sara and this card grows a finish line.{pace_bit}")
        tile.update(verdict="Needs a target", cls="warn",
                    sub="pick the college number with Sara")
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



# ------------------------------------------------- net-worth attribution
PRICE_FRESH_DAYS = 10   # a boundary valued on prices older than this is suppressed


STALE_SHARE_MAX = 2.0   # suppress once > this % of value rides stale prices


def _held_priced() -> list[tuple[str, float]]:
    """(commodity, units) currently held in liquid accounts whose value
    rides a moving price (constant-$1 money markets exempt; never-priced
    holdings sit at cost and the est flag owns that story)."""
    ph = price_history()
    excl = illiquid_currency_regex()
    rows = query("SELECT currency, sum(units(position)) AS u "
                 "WHERE account ~ '^(Assets|Liabilities)' GROUP BY currency")
    out = []
    for r in rows:
        cur = r["currency"] or ""
        if cur == "USD" or (excl and re.match(excl, cur)):
            continue
        units = _units(r["u"], cur)
        if abs(units) < 1e-6:
            continue
        pts = ph.get(cur)
        if not pts or all(abs(px - 1.0) < 1e-9 for _, px in pts):
            continue
        out.append((cur, units))
    return out


def _boundary_staleness(boundary: date, held: list[tuple[str, float]],
                        ph: dict, liquid: float) -> tuple[float, int, date | None]:
    """Price staleness at a valuation boundary, dollar-weighted: what share
    of liquid value rides a price older than PRICE_FRESH_DAYS, and how old
    is the freshest boundary-wide mark (worst age among still-fresh-enough
    holdings, for the "valued at <date> prices" note). Holdings with no
    price on or before the boundary don't count — they sit at cost and the
    est flag owns that story."""
    stale_value, worst, oldest = 0.0, 0, None
    for cur, units in held:
        pts = [(pd, px) for pd, px in ph.get(cur, []) if pd <= boundary]
        if not pts:
            continue
        pd_last, px_last = pts[-1]
        age = (boundary - pd_last).days
        if age > PRICE_FRESH_DAYS:
            stale_value += abs(units * px_last)
        elif age > worst:
            worst, oldest = age, pd_last
    share = 100.0 * stale_value / liquid if liquid else 0.0
    return share, worst, oldest


def _attribution_ctx(series: list[dict], asof: date | None) -> dict | None:
    """"Why it moved": this month and the last closed month, each split into
    markets vs saved vs spent. Saved/spent come from the ledger's booked
    postings; the market effect is the residual of the valuation change
    minus ALL booked flows — so the parts reconcile to the dollar by
    construction. A month whose boundary valuation leans on cost basis or
    stale prices is suppressed, never mislabeled."""
    if len(series) < 2:
        return None
    ph = price_history()
    held = _held_priced()
    liquid_now = series[-1]["v"] or 1.0
    by_ym = {(pt["d"].year, pt["d"].month): i for i, pt in enumerate(series)}
    cur_ym = (series[-1]["d"].year, series[-1]["d"].month)
    prev_ym = (cur_ym[0] - 1, 12) if cur_ym[1] == 1 else (cur_ym[0], cur_ym[1] - 1)
    rows = []
    for ym, closed in ((cur_ym, False), (prev_ym, True)):
        i = by_ym.get(ym)
        if not i:                       # absent, or no prior point to diff from
            continue
        p0, p1 = series[i - 1], series[i]
        name = MONTH_FULL[ym[1]]
        window = (f"{name}, closed" if closed
                  else f"{MONTH_ABBR[ym[1]]} 1–{p1['d'].day} so far")
        if p0["est"] or p1["est"]:
            which = "opening" if p0["est"] else "closing"
            rows.append({"window": window, "suppressed":
                         f"{name} can’t be split honestly — its {which} "
                         f"valuation leans on cost basis (no dated prices "
                         f"yet). A price mark unlocks it."})
            continue
        share0, age0, old0 = _boundary_staleness(p0["d"], held, ph, liquid_now)
        share1, _, _ = _boundary_staleness(p1["d"], held, ph, liquid_now)
        if max(share0, share1) > STALE_SHARE_MAX:
            rows.append({"window": window, "suppressed":
                         f"{name} can’t be split honestly — "
                         f"{max(share0, share1):.0f}% of holdings ride "
                         f"prices older than {PRICE_FRESH_DAYS} days at its "
                         f"boundary. A fresh price mark unlocks it."})
            continue
        inc, exp = month_in_out(ym)
        dmv = p1["v"] - p0["v"]
        flow = p1["flow"]
        market = dmv - flow
        other = flow - (inc - exp)
        bits = [f"markets {delta0(market)}", f"saved {delta0(inc)}",
                f"spent {MINUS}{m0(exp)}" if round(exp) else "spent $0"]
        if abs(round(other)) >= 1:
            bits.append(f"other flows {delta0(other)}")
        note = ""
        if age0 > 2 and old0:
            note = f" Start valued at {mon_d(old0)} prices."
        mags = [abs(market), inc, exp]
        total = sum(mags) or 1.0
        segs = None
        if round(total):
            segs = [{"cls": kind, "width": f"{max(1.5, 100.0 * v / total):.1f}"}
                    for kind, v in (("mkt", abs(market)), ("in", inc),
                                    ("out", exp)) if round(v)]
        rows.append({
            "window": window, "suppressed": None,
            "delta": delta0(dmv),
            "cls": "good" if round(dmv) > 0 else ("bad" if round(dmv) < 0 else ""),
            "body": " · ".join(bits) + ".", "note": note, "segs": segs,
            "aria": f"{window}: liquid moved {delta0(dmv)} — " + ", ".join(bits),
        })
    if not rows or all(r.get("suppressed") for r in rows):
        # keep at most one quiet line when nothing is attributable
        rows = rows[:1]
    return {"rows": rows} if rows else None


# ---------------------------------------------------------- vs the thesis
def _thesis_ctx(view, asof: date | None) -> dict:
    """The drift strip: declared target mix vs the live portfolio, loud only
    when a class sits outside its band, plus the concentration line."""
    if view is None:
        return {"rows": [], "nudge":
                "No target mix declared yet. Tell Sara the household’s "
                "target allocation (it becomes [allocation_targets] in "
                "rules.toml) and this card starts scoring drift against it."}
    if view.invested <= 0:
        return {"rows": [], "nudge":
                "Targets are declared, but no classed holdings are on file "
                "yet — import an investment statement and the drift strip "
                "wakes up."}
    scale = max(max(r.share_pct, r.target_pct) for r in view.rows) or 1.0
    rows = []
    for r in view.rows:
        state = "over" if (r.out_of_band and r.drift_pts > 0) else (
            "under" if r.out_of_band else "")
        rows.append({
            "label": r.label, "state": state,
            "now": f"{r.share_pct:.0f}%", "value": m0(r.value),
            "target": f"target {r.target_pct:g}",
            "band": f"±{r.band_pts:g}",
            "fill": f"{max(1.0, min(100.0, 100.0 * r.share_pct / scale)):.1f}",
            "tick": f"{max(0.0, min(100.0, 100.0 * r.target_pct / scale)):.1f}",
            "delta": ((("+" if r.drift_pts > 0 else MINUS)
                       + f"{abs(r.drift_pts):.0f} pts")
                      if r.out_of_band else ""),
            "trow": (r.label, m0(r.value), f"{r.share_pct:.0f}%",
                     f"{r.target_pct:g}% ±{r.band_pts:g}"),
        })
    notes = []
    if view.reserve_short > 0.5:
        notes.append(f"The {m0(view.reserve_usd)} cash reserve is short by "
                     f"{m0(view.reserve_short)}.")
    elif view.reserve_usd > 0:
        notes.append(f"Cash above the {m0(view.reserve_usd)} reserve: "
                     f"≈{m0(view.cash_above_reserve)} — outside the scored "
                     f"mix, waiting on the thesis’s own deployment rules.")
    if view.excluded_value > 0.5:
        notes.append(f"≈{m0(view.excluded_value)} rides its own glide path "
                     f"(excluded accounts) and stays out.")
    if view.unclassified:
        syms = ", ".join(s for s, _ in view.unclassified[:3])
        notes.append(f"Unclassed holdings ({syms}) sit outside the strip — "
                     f"map them in rules.toml [allocation_targets.map].")
    conc = []
    if view.top:
        sym, val, pct = view.top
        conc.append(f"Biggest single position: {sym} — {pct:.0f}% of liquid "
                    f"({m0(val)}).")
    if view.employer:
        syms, val, pct = view.employer
        conc.append(f"Employer stock: {syms} — "
                    f"{'under 1' if pct < 1 else f'{pct:.0f}'}% of liquid "
                    f"({m0(val)}).")
    return {
        "rows": rows, "nudge": None,
        "sub": (f"Invested dollars vs the written targets — scored over "
                f"≈{m0(view.invested)} invested."),
        "window": f"through {mon_d(asof)}" if asof else "",
        "notes": " ".join(notes), "conc": " ".join(conc),
        "any_out": any(r["state"] for r in rows),
    }


def _cheshbon_ctx(pace: Pace) -> dict:
    """This month's money in vs out and how last month closed. Categories
    live next door in the clickable rail, so this card stays three numbers.
    Follows the paced month so a stale ledger shows its latest real month,
    not zeros. A mid-month negative net is payday timing, not trouble — the
    closed month gets the honest wink instead."""
    ym = pace.cur
    inc, exp = month_in_out(ym)
    net = inc - exp
    through_lbl = (f"through {MONTH_ABBR[ym[1]]} {pace.through_day}"
                   if pace.through_day else "nothing imported yet")
    prev = (ym[0] - 1, 12) if ym[1] == 1 else (ym[0], ym[1] - 1)
    pinc, pexp = month_in_out(prev)
    closed, wink = None, ""
    if pinc or pexp:
        pnet = pinc - pexp
        closed = {"month": mon_yr(prev), "inc": m0(pinc), "exp": m0(pexp),
                  "net": delta0(pnet)}
        wink = (" — the book balances itself when you're not looking."
                if round(pnet) > 0 else ".")
    # a partial month's negative net is payday timing, not trouble — it stays
    # neutral with the reason named; only a full month earns red ink
    partial = not pace.fallback
    if round(net) > 0:
        net_cls = "pos"
    elif round(net) < 0 and not partial:
        net_cls = "neg"
    else:
        net_cls = ""
    return {
        "title": f"{MONTH_FULL[ym[1]]} money in / out",
        "window": f"{mon_yr(ym)} · {through_lbl}",
        "inc": m0(inc), "exp": m0(exp), "net": delta0(net),
        "net_cls": net_cls,
        "payday_note": ("paydays land later in the month — the net settles"
                        if partial and round(net) < 0 else ""),
        "closed": closed, "wink": wink,
    }


def _moneymap_ctx(balances: list[tuple[str, float]], liquid: float,
                  asof: date | None) -> dict | None:
    """The Money-map card: treemap payload + its window + the no-JS table
    (every liquid account, liabilities included)."""
    data = moneymap_data(balances, liquid)
    if not data:
        return None
    return {**data,
            "window": f"through {mon_d(asof)}" if asof else "",
            "table_rows": [(a, m0(v)) for a, v in balances]}


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
    # the Independence room is opt-in (show_walkaway_room's one rule); when
    # it's off, none of the walk-away math runs and none of it ships
    indy = show_walkaway_room(goals)
    wa = (walkaway(liquid, paper, true_spend_baseline(today, totals), goals)
          if indy else None)
    edu_accounts = education_accounts()
    edu_pace = education_pace(edu_accounts) if edu_accounts else None
    alloc = allocation_view()
    if alloc and alloc.stock_pct > 0:
        stock_pct, stock_src = alloc.stock_pct, "your declared targets"
    else:
        stock_pct, stock_src = DEFAULT_STOCK_PCT, "the skill default"
    wig = wi_ctx = None
    if wa and wa.baseline:
        save = net_savings_baseline(wa.baseline.months)
        wig = whatif_grid(liquid, wa.baseline, save, today, goals,
                          edu_accounts, edu_pace, stock_pct, stock_src)
        b = wa.baseline
        ev = wig["_ev"]
        if ev.partner_monthly:
            partner_lbl = Markup(
                "One paycheck keeps coming — <b>{}</b>, ≈{}/mo net into the "
                "ledger (the smaller stream, median over the baseline "
                "window)").format(ev.partner_label, m0(ev.partner_monthly))
        else:
            partner_lbl = None
        college_lbl = None
        if ev.college_year and ev.college_lump > 0:
            college_lbl = Markup(
                "College paid from the pot — <b>≈{} more in {}</b> ({}; the "
                "529’s current path covers ≈{} of the ≈{} cost)").format(
                m0(ev.college_lump), ev.college_year, ev.college_src,
                m0(ev.college_529_path), m0(ev.college_cost))
        elif ev.college_year:
            college_lbl = Markup(
                "College in {} — the 529’s current path (≈{}) already covers "
                "the ≈{} target; nothing more from the pot").format(
                ev.college_year, m0(ev.college_529_path), m0(ev.college_cost))
        surv = wig["surv"]
        wi_ctx = {
            "max_ri": wig["nRates"] - 1, "max_si": wig["nSpends"] - 1,
            "max_gi": wig["nGrowths"] - 1,
            "def_ri": wig["def"]["ri"], "def_si": wig["def"]["si"],
            "def_gi": wig["def"]["gi"],
            "liquid": m0(liquid),
            "house_amts": wig["ev"]["houseAmts"],
            "house_years": wig["ev"]["houseYears"],
            "house_def_a": ev.house_def_a, "house_def_y": ev.house_def_y,
            "house_src": ev.house_src,
            "partner_lbl": partner_lbl, "college_lbl": college_lbl,
            "college_covered": (ev.college_year is not None
                                and ev.college_lump <= 0),
            "n_seq": surv["nSeq"], "surv_window": surv["window"],
            "surv_mix": surv["mix"],
            "bands_cap": (f"Replaying every complete {RETIREMENT_YEARS}-year "
                          f"stretch of markets since 1871 ({surv['window']}) "
                          f"on a {surv['mix']}. Each column splits those "
                          f"starts by where they stood that many years into "
                          f"retirement; spending keeps its purchasing power "
                          f"(real dollars). Counts, not forecasts."),
            "formula": (f"Target = a year of spending ÷ the withdrawal rate. "
                        f"The spend dial starts at your true burn, "
                        f"≈{m0(b.burn * 12)}/yr — what the last "
                        f"{len(b.months)} full months actually cost, "
                        f"annualized. The solid curve compounds today's "
                        f"liquid ({m0(liquid)}) monthly at the chosen real "
                        f"growth and adds your median net savings, "
                        f"≈{m0(save)}/mo ({len(b.months)} full months, "
                        f"{window_label(b.months)}); the dotted twin saves "
                        f"$0 — growth alone. Life events are one honest "
                        f"arithmetic move each: a house down payment leaves "
                        f"the curve in its year, college subtracts what the "
                        f"529’s path won’t cover, and “one paycheck keeps "
                        f"coming” shrinks the target to cover only the "
                        f"spending the paycheck doesn’t — the replay then "
                        f"assumes event money was set aside before anyone "
                        f"walked away. The history check replays "
                        f"{surv['window']} on a {surv['mix']}: spend the "
                        f"dialed % of the pot in year one, the same real "
                        f"dollars every year after. Counts, not promises — "
                        f"the past is a witness, not a guarantee. "
                        f"Hypothetical only — taxes, raises, and market "
                        f"reality not included; the thesis, not this toy, "
                        f"sets policy."),
        }
    series, baseline_cut = networth_series(liquid, asof)
    cards, more, needs_state = needs_you(today)
    ch = _cheshbon_ctx(pace)
    sp = spending_data(pace)
    mapd = _moneymap_ctx(balances, liquid, asof)
    all_moves = must_move(today)
    moves = [mv for mv in all_moves if not mv["plumbing"]]
    plumbing = [mv for mv in all_moves if mv["plumbing"]]
    edu = _education_ctx(edu_accounts, edu_pace, goals, today)
    mach = _machine_ctx(lane_status(today))
    nw = _networth_ctx(series, baseline_cut, liquid, asof)
    spark = _sparkline(series)
    glance = {
        "spend": _spend_tile(pace, under_streak(totals, pace.cur)),
        "nw": {"v": m0(liquid), "chip": nw["delta"], "spark": spark,
               "sub": ("liquid · " + nw["asof"]),
               "glow": bool(nw["delta"] and nw["delta"]["cls"] == "good")},
        "auto": _auto_tile(mach),
        "edu": edu["tile"],
    }

    names = household("names")
    hour = now.hour
    daypart = "morning" if hour < 12 else ("afternoon" if hour < 18 else "evening")
    checks_stamp = ""
    if (cd := findings_date()):
        # a hand-mangled findings stamp must never crash the whole page
        with contextlib.suppress(ValueError):
            checks_stamp = f" · checks from {date.fromisoformat(cd).strftime('%b %-d')}"

    island = {"pace": _pace_chart_data(pace), "nw": _nw_chart_data(series, asof),
              "whatif": ({k: v for k, v in wig.items() if k != "_ev"}
                         if wig else None),
              "spending": ({k: v for k, v in sp.items()
                            if not k.startswith("table_") and k != "six_lbl"}
                           if sp else None),
              "map": {"tree": mapd["tree"]} if mapd else None,
              "edu": edu.get("grid")}
    # '<' escaped so no payee can smuggle a </script> into the island
    island_json = json.dumps(island, separators=(",", ":")).replace("<", "\\u003c")
    css = (CSS_TEMPLATE
           .replace("__INTER_R__", _b64(_asset("Inter-Regular.woff2", "Inter Regular")))
           .replace("__INTER_S__", _b64(_asset("Inter-SemiBold.woff2", "Inter SemiBold"))))

    return _ENV.from_string(PAGE_TEMPLATE).render(
        greet=f"Good {daypart}, {names}" if names else f"Good {daypart}",
        sara=sara_line(pace, needs_state, cards, more, daypart),
        stamp=(f"Generated {today.strftime('%a %b %-d')}, "
               f"{now.strftime('%-I:%M %p').lower()}"),
        ledger_stamp=(f"Ledger through {mon_d(asof)}" if asof
                      else "Ledger empty"),
        checks_stamp=checks_stamp,
        g=glance,
        nxt=_next_ctx(needs_state, cards, more, moves),
        sp=sp,
        map=mapd,
        p=_pace_ctx(pace),
        mach=mach,
        needs={"state": needs_state, "cards": cards, "more": more,
               "sub": "Anything that wants a decision or a quick hand this week."},
        moves=moves,
        plumbing=plumbing,
        mustmove_days=MUSTMOVE_DAYS,
        indy=indy,
        wa=_walkaway_ctx(wa, liquid, asof),
        wi=wi_ctx,
        min_months=MIN_FULL_MONTHS,
        edu=edu,
        wins=_wins_ctx(saras_wins(today), today),
        nw=nw,
        attr=_attribution_ctx(series, asof),
        thesis=_thesis_ctx(alloc, asof),
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
