#!/usr/bin/env python3
"""The verified builders behind every Sara surface — data and context, no page.

Split out of home.py when the static rooms retired (2026-08-07): home.py now
renders only the one-viewport print glance, the Sara App server renders the
rooms live, and BOTH assemble their payloads from this module — the same
spend pace, needs-you cards, education story, money map, and chart data, so
no two surfaces can disagree. digest.py and summary.py read here too
(via home.py's re-exports for the server's import path).

Money honesty: every dollar is computed and formatted here in Python —
whole dollars, true minus U+2212, ≈ on derived or projected figures, every
window labelled. Payees, findings text, facts bullets, lane names, and
account names are bank-controlled DATA; context strings are plain str
(escaped by the consumer's autoescape) and the few intentional-HTML
fragments are built with Markup.format, which escapes their arguments.
"""
import math
import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import NamedTuple

from markupsafe import Markup, escape

from sara.vault import (REPORTS, VAULT, account_owners, amount, dated_bullets,
                   illiquid_currency_regex, owner_label, query)
from sara.advisor.webview import (MONTH_ABBR, _units, action_queue, code_spans,
                     deadline_items, month_label, nice_ticks, parse_findings,
                     price_history, queue_lane)
from sara.advisor.checks import LANE_REINVEST, goals as goals_config

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
EDU_MAX_YEARS = 40              # slider horizon; beyond = "40+ yrs out"
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


                                  # None until 24 full months exist


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


SPEND_ASOF_LOOKBACK = 60  # an account "funds spending" if an expense hit it this recently


def _spend_asof(today: date) -> date | None:
    """The day spending data is complete THROUGH: every account that funded
    an expense in the last SPEND_ASOF_LOOKBACK days contributes its own
    newest posting date, and the MINIMUM wins. A lagging card feed caps the
    window — its swipes past that day haven't landed, so counting the bank's
    fresher days as 'covered' would understate the month."""
    floor_d = today - timedelta(days=SPEND_ASOF_LOOKBACK)
    exp_ids = {r["id"] for r in query(
        f"SELECT id WHERE account ~ '^Expenses' AND date >= {floor_d.isoformat()}")}
    if not exp_ids:
        return None
    funders = {r["account"] for r in query(
        f"SELECT id, account WHERE account ~ '^(Assets|Liabilities)' "
        f"AND date >= {floor_d.isoformat()} AND number < 0")
        if r["id"] in exp_ids}
    if not funders:
        return None
    last = {}
    for r in query("SELECT account, max(date) AS d "
                   "WHERE account ~ '^(Assets|Liabilities)' GROUP BY account"):
        try:
            last[r["account"]] = date.fromisoformat(r["d"])
        except (TypeError, ValueError):
            continue
    dates = [last[a] for a in funders if a in last]
    return min(dates) if dates else None


def spend_pace(today: date, asof: date | None,
               totals: list[tuple[YM, float]], month: YM | None = None) -> Pace:
    """Compute the Pace for the calendar month (or `month`, the stale-ledger
    fallback). The median path beats a straight line because a rent-shaped
    day 1 shouldn't false-alarm; honesty about ledger lag means the actual
    line ends at the day spending coverage is COMPLETE through — the minimum
    of the spend-funding accounts' own newest postings — never at 'today'
    and never at a fresher account's date while a card feed lags."""
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
    # through-day: the day spending coverage is complete through — the ledger's
    # reach capped by the laggiest spend-funding account (feed lag stays visible)
    through_day = None
    cand = min(asof, date(cur[0], cur[1], ndays)) if asof else None
    if cand and month is None:
        cand = min(cand, today)
        spend_cov = _spend_asof(today)
        if spend_cov is not None:
            cand = min(cand, spend_cov)
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


def _as_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


HOUSE_YEAR_OFFSETS = (-2, 0, 2)          # ...and its three purchase years
COLLEGE_AGE = 18
COLLEGE_FALLBACK_COST = 400_000  # today's-dollar private-college placeholder


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
    """Median monthly 529 contribution over the last 6 contribution months —
    money the household PUT IN, never growth. Prefers the Equity:*529*
    pass-through convention (external money by construction); falls back to
    cost-basis inflows on the 529 asset accounts, skipping each account's
    opening-snapshot month AND any posting marked as a reinvested dividend
    (reinvestment is the market compounding, not a contribution)."""
    rows = query("SELECT year, month, sum(convert(position,'USD')) AS v "
                 "WHERE account ~ '^Equity' AND account ~ '529' "
                 "GROUP BY year, month ORDER BY year, month")
    monthly = [abs(amount(r["v"])) for r in rows if abs(amount(r["v"])) >= 1]
    if not monthly:
        for a in accounts:
            acct = a.account.replace("'", "''")
            rows = query(f"SELECT year, month, payee, sum(cost(position)) AS v "
                         f"WHERE account = '{acct}' AND number > 0 "
                         f"GROUP BY year, month, payee ORDER BY year, month")
            by_month: dict[tuple[int, int], float] = {}
            for r in rows:
                if LANE_REINVEST.search(r["payee"] or ""):
                    continue
                try:
                    ym = (int(r["year"]), int(r["month"]))
                except (TypeError, ValueError):
                    continue
                by_month[ym] = by_month.get(ym, 0.0) + amount(r["v"])
            vals = [by_month[ym] for ym in sorted(by_month)]
            monthly += [v for v in vals[1:] if v >= 1]  # [0] = opening snapshot
    return median(monthly[-6:]) if monthly else None


def findings_date() -> str | None:
    """The `_Generated YYYY-MM-DD` stamp inside findings.md, for the header."""
    p = REPORTS / "findings.md"
    if not p.exists():
        return None
    m = re.search(r"_Generated (\d{4}-\d{2}-\d{2}) ", p.read_text())
    return m.group(1) if m else None


def _queue_card(f: dict) -> Card:
    n = f.get("count", 1)
    why = f["title"] if n == 1 else f"{f['title']} · +{n - 1} more like it"
    return Card(f["severity"], f["fix"] or f["title"], why, f["severity"])


def needs_you(today: date) -> tuple[list[Card], int, str]:
    """Top verb cards, at most NEEDS_CARDS, by the queue's priority lanes:
    money-wrong findings lead, dated obligations within DEADLINE_CARD_DAYS
    come second, everything else (bookkeeping, feed health) last. The queue
    arrives merged and capped from webview.action_queue — one counted card
    per check. Returns (cards, more_count, state); state 'none' = checks
    never ran, 'ok' = ran and nothing needs a human, 'cards' = the list."""
    findings, _, errors = parse_findings()
    if findings is None:
        return [], 0, "none"
    queue = action_queue(findings)
    try:
        horizon = int(goals_config().get("deadline_horizon_days") or 45)
    except (TypeError, ValueError):
        horizon = 45
    deadlines = deadline_items(today, horizon)

    cards = [_queue_card(f) for f in queue if queue_lane(f["check"]) == 0]
    for dl in sorted(deadlines, key=lambda d: d["days"]):
        if dl["days"] <= DEADLINE_CARD_DAYS:
            when = "today" if dl["days"] == 0 else (
                "tomorrow" if dl["days"] == 1 else f"in {dl['days']} days")
            cards.append(Card("deadline", dl["text"],
                              f"due {dl['date'].strftime('%a %b %-d')} · {when}",
                              "deadline", dl["days"]))
    cards += [_queue_card(f) for f in queue if queue_lane(f["check"]) != 0]
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

    owners = account_owners()   # empty pre-owner ledger: no chips anywhere

    def _own(who: set) -> str | None:
        """One unambiguous owner label for a node, or nothing at all."""
        return owner_label(next(iter(who))) if len(who) == 1 and None not in who else None

    groups: dict[str, dict] = {}
    for acct, v in assets:
        inst, leaf = _acct_parts(acct)
        g = groups.setdefault(inst, {"name": _disp(inst), "value": 0.0,
                                     "kids": {}, "who": set()})
        g["value"] += v
        g["who"].add(owners.get(acct))
        node = g["kids"].setdefault(leaf, {"name": _disp(leaf), "value": 0.0,
                                           "hold": [], "who": set()})
        node["value"] += v
        node["who"].add(owners.get(acct))
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
            if (kid_own := _own(node["who"])):
                kid["own"] = kid_own
            if children and len(children) > 1:
                kid["children"] = children
            kids.append(kid)
        top = {"name": g["name"], "value": round(g["value"], 2),
               "amt": m0(g["value"]), "pct": pcts(g["value"]),
               "cvar": f"--map-{min(gi + 1, MAP_GROUP_VARS + 1)}",
               "children": kids}
        if (g_own := _own(g["who"])):
            top["own"] = g_own
        tree.append(top)
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
            if need > EDU_MAX_YEARS * 12:
                arrive.append(f"{EDU_MAX_YEARS}+ yrs out at this pace")
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


SMALL_NUMS = ["zero", "One", "Two", "Three", "Four", "Five", "Six",
              "Seven", "Eight", "Nine"]


def sara_line(pace: Pace, cards_state: str, cards: list[Card], more: int,
              daypart: str = "morning") -> str:
    """One warm, honest sentence for the hero — a verdict, never directions.
    The Next line and the rooms sit right below it; pointing at them from
    up here read like a tooltip, and Sara is a person. A `daypart` of
    "night" (after ten) turns "this evening" into "tonight"."""
    over = (pace.pace_delta or 0) > PACE_BAND * (pace.typical or float("inf"))
    under = (pace.pace_delta or 0) < -PACE_BAND * (pace.typical or 0)
    when = "tonight" if daypart == "night" else f"this {daypart}"
    today_word = ("tonight" if daypart == "night"
                  else "this week" if daypart == "week" else "today")
    if cards_state == "none":
        return "First morning here — ask me to run the checks and I'll start watching for you."
    n_alerts = sum(1 for c in cards if c.kind == "alert")
    if n_alerts:
        n_lbl = SMALL_NUMS[n_alerts] if n_alerts < 10 else str(n_alerts)
        verb = "wants" if n_alerts == 1 else "want"
        thing = "thing" if n_alerts == 1 else "things"
        return f"{n_lbl} {thing} {verb} a decision {when}."
    if cards:
        n = len(cards) + more
        s = "s" if n != 1 else ""
        lead = "Spending is running hot, and a" if over else "A"
        return f"{lead} few small thing{s} could use your hands — none of it urgent."
    if pace.typical is None:
        return "All quiet. A few more months of history and I can show your typical pace."
    if over:
        return "Nothing needs your hands — but spending is running ahead of typical."
    if under:
        return ("Nothing needs you, and spending is running under typical. "
                "I checked twice.")
    return f"All quiet. Spending is on pace, and nothing needs your hands {today_word}."


def _codespans(text: str) -> Markup:
    """Escape, then `x` -> <code>x</code> — findings text is untrusted."""
    return Markup(code_spans(str(escape(text))))


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
    days_in = ("" if pace.fallback or not pace.through_day
               else f" ({pace.through_day} days in)" if pace.through_day > 1
               else " (1 day in)")
    tile["sub"] = f"{when}{days_in} · typical {month_name} runs {m0(pace.typical)}"
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


def _next_ctx(needs_state: str, cards: list[Card], more: int,
              moves_human: list[dict]) -> dict:
    """THE one next action under the tiles: the top alert, else the nearest
    dated obligation (deadline card or money that must move), else honest
    quiet. Everything else waits inside the rooms."""
    alerts = [c for c in cards if c.kind == "alert"]
    if alerts:
        return {"label": "Next", "quiet": False,
                "text": _codespans(alerts[0].verb),
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
                "On pace for <b>≈{} {}</b> at ≈{}/mo you put in (median of "
                "recent months; reinvested dividends don’t count), ignoring "
                "market growth.").format(
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
        pace_bit = (f" You’re putting in ≈{m0(pace)}/mo (reinvested dividends "
                    f"don’t count)." if pace else "")
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
                "target allocation and this card starts scoring drift "
                "against it."}
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
    (every liquid account, liabilities included; an Owner column joins it
    once the ledger carries `owner:` metadata)."""
    data = moneymap_data(balances, liquid)
    if not data:
        return None
    owners = account_owners()
    rows = ([(a, m0(v), owner_label(owners.get(a)) or "—") for a, v in balances]
            if owners else [(a, m0(v)) for a, v in balances])
    return {**data,
            "window": f"through {mon_d(asof)}" if asof else "",
            "has_owners": bool(owners),
            "table_rows": rows}


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
