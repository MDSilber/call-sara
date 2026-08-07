"""The Spending room under the owner lens, computed live from analytics.duckdb.

summary.json bakes ONE household spend payload at report time, so
``/api/spend?owner=<person|joint>`` cannot ride the snapshot. This module
rebuilds the same JSON shape per owner with parameterized SQL, mirroring the
snapshot's semantics number for number (tools/home.py is the reference):

- the pace card compares the month's day-by-day cumulative expense path
  against the MEDIAN day-by-day path of the owner's last PACE_WINDOW full
  months (``spend_pace``), including the stale-ledger fallback month;
- the category rail, merchant drill, and money in/out follow
  ``spending_data`` / ``cheshbon_ctx``;
- money leaves as display strings with named windows, numbers ride only as
  chart geometry — the same contract the snapshot keeps.

Owner semantics are dbq's: an Income/Expenses posting belongs to the owner
of its FUNDING account (``other_account``), so transit never shows and an
unknown owner simply matches nothing. Sara's wins stay household (notes are
not owner-attributable) and are overlaid by the route from the snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from statistics import median

from .dbq import MONTH_ABBR, owner_clause
from .readmodel import DB, MINUS, money0

# mirrored from tools/home.py — the snapshot's pace constants
PACE_WINDOW = 6           # "a typical month" = median of the last 6 full months
MIN_FULL_MONTHS = 3       # fewer than this and a median is a guess
PACE_BAND = 0.10          # within ±this fraction of typical = "On pace"
TREND_MONTHS = 6          # category rail: trend window, current month included
MERCHANTS_TOP = 8         # merchant rows per category per period
CATS_VISIBLE = 10         # clickable category rows before the fold
MONTH_FULL = ["", "January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]

YM = tuple[int, int]


@dataclass(frozen=True, slots=True)
class Pace:
    """tools/home.py's Pace, computed per owner from the analytics DB."""
    cur: YM
    ndays: int
    through_day: int | None
    daily_cum: list[float]
    spent: float
    typical: float | None
    typical_window: list[YM]
    ideal: list[float]
    typical_by_now: float | None
    left: float | None
    pace_delta: float | None
    fallback: bool


# ------------------------------------------------------------- primitives
def _f(v: object) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return 0.0


def _s(v: object) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def _ym(v: object) -> YM | None:
    if isinstance(v, date):
        return (v.year, v.month)
    s = _s(v)
    if len(s) >= 7 and s[4] == "-":
        try:
            return (int(s[:4]), int(s[5:7]))
        except ValueError:
            return None
    return None


def _delta0(x: float) -> str:
    """home.delta0: signed whole dollars, ± for a true zero."""
    r = round(x)
    sign = "+" if r > 0 else (MINUS if r < 0 else "±")
    return f"{sign}${abs(r):,.0f}"


def _mon_yr(ym: YM) -> str:
    return f"{MONTH_ABBR[ym[1]]} {ym[0]}"


def _window_label(months: list[YM]) -> str:
    if not months:
        return ""
    a, b = months[0], months[-1]
    if a == b:
        return _mon_yr(a)
    if a[0] == b[0]:
        return f"{MONTH_ABBR[a[1]]}–{MONTH_ABBR[b[1]]} {a[0]}"
    return f"{_mon_yr(a)} – {_mon_yr(b)}"


def _month_days(ym: YM) -> int:
    y, m = ym
    if m == 12:
        return 31
    return (date(y, m + 1, 1) - date(y, m, 1)).days


def _last_months(cur: YM, n: int) -> list[YM]:
    """The n calendar months ending AT cur, oldest first."""
    y, m = cur
    out: list[YM] = []
    for back in range(n - 1, -1, -1):
        yy, mm = y, m - back
        while mm < 1:
            yy, mm = yy - 1, mm + 12
        out.append((yy, mm))
    return out


def _nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    """webview.nice_ticks: ~n clean tick values covering [lo, hi]."""
    span = (hi - lo) or 1.0
    raw = span / max(n, 1)
    mag = 10 ** len(str(int(abs(raw)))) / 10 if raw >= 1 else 1.0
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw),
                10 * mag)
    first = step * (lo // step)
    ticks: list[float] = []
    t = first
    while t <= hi + step:
        if t >= lo - 1e-9:
            ticks.append(round(t, 6))
        t += step
    ticks = ticks[:n + 2]
    if len(ticks) >= 2 and ticks[-1] - hi > 0.6 * step:
        ticks.pop()
    return ticks


def _yaxis(lo: float, hi: float) -> dict[str, object]:
    ticks = _nice_ticks(lo, hi)
    step = ticks[1] - ticks[0] if len(ticks) >= 2 else (hi - lo or 1.0)
    labels = {str(int(t)) if float(t).is_integer() else str(t): money0(t)
              for t in ticks}
    return {"min": ticks[0], "max": ticks[-1], "step": step, "labels": labels}


def _cap(owner: str) -> str:
    return owner[:1].upper() + owner[1:]


# ------------------------------------------------------------------ data
def _ledger_asof() -> date | None:
    """webview.latest_ledger_date's twin: the ledger's reach, judged by the
    last Assets/Liabilities posting (feed lag stays visible)."""
    row = DB.one(
        "SELECT max(p.date) AS d FROM postings p "
        "WHERE p.account LIKE 'Assets:%' OR p.account LIKE 'Liabilities:%'")
    d = row["d"] if row else None
    return d if isinstance(d, date) else None


def _monthly_totals(owner: str) -> list[tuple[YM, float]]:
    """Every month with expense activity for this owner — the pace window's
    raw material (home.monthly_expense_totals, owner-sliced)."""
    clause, params = owner_clause(owner, "p.other_account")
    rows = DB.rows(f"""
        SELECT date_trunc('month', p.date)::DATE AS month,
               sum(p.amount_home) AS v
        FROM postings p
        WHERE p.account LIKE 'Expenses:%'{clause}
        GROUP BY ALL ORDER BY month
    """, params)
    out: list[tuple[YM, float]] = []
    for r in rows:
        ym = _ym(r["month"])
        if ym is not None:
            out.append((ym, _f(r["v"])))
    return out


def _daily_by_month(owner: str, start: date) -> dict[YM, dict[int, float]]:
    clause, params = owner_clause(owner, "p.other_account")
    rows = DB.rows(f"""
        SELECT p.date AS d, sum(p.amount_home) AS v
        FROM postings p
        WHERE p.account LIKE 'Expenses:%' AND p.date >= $start{clause}
        GROUP BY ALL ORDER BY d
    """, {"start": start.isoformat(), **params})
    out: dict[YM, dict[int, float]] = {}
    for r in rows:
        d = r["d"]
        if not isinstance(d, date):
            continue
        days = out.setdefault((d.year, d.month), {})
        days[d.day] = days.get(d.day, 0.0) + _f(r["v"])
    return out


def _spend_pace(owner: str, today: date, asof: date | None,
                totals: list[tuple[YM, float]],
                month: YM | None = None) -> Pace:
    """home.spend_pace, owner-sliced: the median path of the owner's window
    months vs their cumulative actual, honest about the ledger's reach."""
    cur = month or (today.year, today.month)
    ndays = _month_days(cur)
    window = [(ym, v) for ym, v in totals if ym < cur][-PACE_WINDOW:]
    win_months = [ym for ym, _ in window]

    start = (date(win_months[0][0], win_months[0][1], 1) if win_months
             else date(cur[0], cur[1], 1))
    by_month_day = _daily_by_month(owner, start)

    typical: float | None = None
    ideal: list[float] = []
    if len(window) >= MIN_FULL_MONTHS:
        cums: dict[YM, list[float]] = {}
        for ym in win_months:
            run = 0.0
            cs: list[float] = []
            for d in range(1, _month_days(ym) + 1):
                run += by_month_day.get(ym, {}).get(d, 0.0)
                cs.append(run)
            cums[ym] = cs
        # a month shorter than day d contributes its total from then on
        ideal = [round(median(cums[ym][min(d, len(cums[ym])) - 1]
                              for ym in win_months), 2)
                 for d in range(1, ndays + 1)]
        typical = ideal[-1]

    daily = by_month_day.get(cur, {})
    through_day: int | None = None
    cand = min(asof, date(cur[0], cur[1], ndays)) if asof else None
    if cand and month is None:
        cand = min(cand, today)
    if cand and cand >= date(cur[0], cur[1], 1):
        through_day = cand.day
    elif daily:
        through_day = max(daily)

    cum: list[float] = []
    run = 0.0
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


def _paced(owner: str, today: date, asof: date | None,
           totals: list[tuple[YM, float]]) -> Pace:
    """assemble._paced: the calendar month, falling back to the latest
    imported month when the ledger is stale."""
    pace = _spend_pace(owner, today, asof, totals)
    if pace.through_day is None and totals:
        last_m = max(ym for ym, _ in totals)
        if last_m < pace.cur:
            pace = _spend_pace(owner, today, asof, totals, month=last_m)
    return pace


def _under_streak(totals: list[tuple[YM, float]], cur: YM) -> int:
    """home.under_streak: consecutive closed months under their own typical."""
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


# ------------------------------------------------------------ card builders
def pace_ctx(pace: Pace, owner: str) -> dict[str, object]:
    """home.pace_ctx, with the card titled for the person it paces."""
    cur_lbl = _mon_yr(pace.cur)
    month_name = MONTH_ABBR[pace.cur[1]]
    month_full = MONTH_FULL[pace.cur[1]]
    through_lbl = (f"through {month_name} {pace.through_day}"
                   if pace.through_day else "no activity imported yet")
    window = (f"{cur_lbl} · {through_lbl}"
              + (" · latest imported month" if pace.fallback else ""))
    title = f"{_cap(owner)}’s spending"
    if pace.typical is None and not pace.daily_cum:
        return {"empty": True, "title": title, "window": cur_lbl,
                "sub": f"Spending vs {_cap(owner)}’s typical month."}

    hero_cls = ""
    if pace.typical is not None and pace.left is not None:
        sub = (f"typical = the median path of {_cap(owner)}’s last "
               f"{len(pace.typical_window)} full months "
               f"({_window_label(pace.typical_window)}).")
        band = PACE_BAND * pace.typical
        if pace.fallback:
            d = round(pace.spent - pace.typical)
            hero = (f"{money0(abs(d))} {'under' if d < 0 else 'over'}"
                    if d else "on pace")
            hero_cls = "good" if d < -band else ("bad" if d > band else "")
            herolab = (f"a typical month's total — {month_full} closed at "
                       f"{money0(pace.spent)} against a typical "
                       f"{money0(pace.typical)}")
        elif pace.pace_delta is not None:
            d = round(pace.pace_delta)
            hero = (f"{money0(abs(d))} {'under' if d < 0 else 'over'}"
                    if d else "on pace")
            hero_cls = "good" if d < -band else ("bad" if d > band else "")
            herolab = (("with" if d == 0 else "") +
                       f" the typical path through {month_name} "
                       f"{pace.through_day} (≈{money0(pace.typical_by_now or 0)} "
                       f"by now) — a typical {month_full} runs "
                       f"{money0(pace.typical)}").strip()
        else:
            hero = money0(pace.left)
            herolab = (f"left of a typical {month_full} "
                       f"({money0(pace.typical)}) — nothing spent on record yet")
    else:
        sub = "no typical-month baseline yet."
        hero = money0(pace.spent)
        herolab = (f"spent so far — {MIN_FULL_MONTHS}+ full months unlock the "
                   f"typical-month line")

    return {
        "empty": False, "title": title, "window": window, "sub": sub,
        "hero": hero, "hero_cls": hero_cls, "herolab": herolab,
        "lag_note": ("" if pace.daily_cum else
                     f"No {month_name} activity imported yet — the solid line "
                     f"starts with the next import."),
        "table_caption": (f"Cumulative spend, {cur_lbl} ({through_lbl}), vs "
                          f"the median path of the window months."),
        "table_rows": [(f"{month_name} {i}", money0(v),
                        "≈" + money0(pace.ideal[i - 1]) if pace.ideal else "—")
                       for i, v in enumerate(pace.daily_cum, 1)],
    }


def _pace_chart(pace: Pace) -> dict[str, object] | None:
    """home._pace_chart_data: labels, geometry, preformatted tooltips."""
    if not pace.daily_cum and not pace.ideal:
        return None
    month_name = MONTH_ABBR[pace.cur[1]]
    days = [f"{month_name} {d}" for d in range(1, pace.ndays + 1)]
    actual: list[float | None] = list(pace.daily_cum)
    actual += [None] * (pace.ndays - len(pace.daily_cum))
    tips: list[dict[str, object]] = []
    for i in range(pace.ndays):
        rows: list[list[str]] = []
        if i < len(pace.daily_cum):
            rows.append([money0(pace.daily_cum[i]), "spent so far"])
        if pace.ideal:
            rows.append(["≈" + money0(pace.ideal[i]), "typical by now"])
        if i < len(pace.daily_cum) and pace.ideal:
            rows.append([_delta0(pace.daily_cum[i] - pace.ideal[i]),
                         "vs typical"])
        tips.append({"t": days[i], "rows": rows})
    spread = (pace.daily_cum or [0.0]) + (pace.ideal or [0.0])
    now: dict[str, object] | None = None
    if pace.daily_cum:
        idx = len(pace.daily_cum) - 1
        now = {"xy": [idx, pace.daily_cum[-1]], "label": money0(pace.spent),
               "side": "left" if idx > pace.ndays * 0.72 else "right"}
    return {"days": days, "actual": actual, "ideal": pace.ideal, "tips": tips,
            "y": _yaxis(min(0.0, min(spread)), max(spread) or 1.0),
            "xint": max(0, round(pace.ndays / 7) - 1), "now": now}


def _tile(pace: Pace, streak: int) -> dict[str, object]:
    """home.spend_tile: verdict first, number second, ±PACE_BAND band."""
    month_name = MONTH_ABBR[pace.cur[1]]
    tile: dict[str, object] = {
        "verdict": "Finding pace", "cls": "", "fig": "", "glow": False,
        "sub": f"needs {MIN_FULL_MONTHS} full months for a verdict",
        "streak": ""}
    if pace.typical is None:
        if pace.daily_cum:
            tile["fig"] = f"{money0(pace.spent)} spent so far"
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
    tile["fig"] = (f"{money0(abs(round(d)))} {'under' if d < 0 else 'over'} {vs}"
                   if round(d) else f"right on {vs}")
    tile["sub"] = f"{when} · typical {month_name} runs {money0(pace.typical)}"
    if streak >= 2:
        tile["streak"] = f"{streak} closed months under typical"
    return tile


# ------------------------------------------------- rail + drill + cheshbon
def _rooms(owner: str, pace: Pace) -> dict[str, object] | None:
    """home.spending_data, owner-sliced: per-period category rollups with
    their top merchants, plus each category's monthly trend."""
    cur = pace.cur
    months = _last_months(cur, TREND_MONTHS)
    prev = months[-2] if len(months) >= 2 else None
    start = date(months[0][0], months[0][1], 1)
    clause, params = owner_clause(owner, "p.other_account")
    rows = DB.rows(f"""
        SELECT split_part(p.account, ':', 2) AS cat,
               coalesce(nullif(trim(p.payee), ''), '(no payee)') AS payee,
               date_trunc('month', p.date)::DATE AS month,
               sum(p.amount_home) AS v
        FROM postings p
        WHERE p.account LIKE 'Expenses:%' AND p.date >= $start{clause}
        GROUP BY ALL
    """, {"start": start.isoformat(), **params})
    keep = set(months)
    by_cat: dict[str, dict[YM, float]] = {}
    merch: dict[tuple[str, str], dict[str, float]] = {}
    for r in rows:
        ym = _ym(r["month"])
        if ym is None or ym not in keep:
            continue
        cat = _s(r["cat"]) or "Other"
        v = _f(r["v"])
        by_cat.setdefault(cat, {})
        by_cat[cat][ym] = by_cat[cat].get(ym, 0.0) + v
        payee = _s(r["payee"])
        for period in (("cur",) if ym == cur else ()) + \
                      (("prev",) if ym == prev else ()) + ("six",):
            d = merch.setdefault((period, cat), {})
            d[payee] = d.get(payee, 0.0) + v
    if not by_cat:
        return None

    cat_names = sorted(by_cat, key=lambda c: -sum(by_cat[c].values()))
    keeps: list[tuple[str, set[YM]]] = [
        ("cur", {cur}), ("prev", {prev} if prev else set()), ("six", keep)]
    per_totals = {p: {c: sum(v for ym, v in by_cat[c].items() if ym in sel)
                      for c in cat_names}
                  for p, sel in keeps}
    partial = (pace.through_day is not None
               and pace.through_day < pace.ndays)
    through = (f"{MONTH_ABBR[cur[1]]} 1–{pace.through_day}"
               if pace.through_day else "nothing imported yet")

    cats: list[dict[str, object]] = []
    for c in cat_names:
        series = [round(by_cat[c].get(ym, 0.0), 2) for ym in months]
        tips: list[dict[str, object]] = []
        for mi, ym in enumerate(months):
            t = _mon_yr(ym)
            if ym == cur and partial:
                t += f" · {through} (partial)"
            tips.append({"t": t, "rows": [[money0(series[mi]), c]]})
        per: dict[str, object] = {}
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
                "amt": money0(tot),
                "pct": (f"{100.0 * tot / period_sum:.0f}%"
                        if period_sum else "—"),
                "w": round(max(1.5, 100.0 * tot / period_max), 1),
                "merch": [[n, money0(v)] for n, v in pairs[:MERCHANTS_TOP]],
                "more": max(0, len(pairs) - MERCHANTS_TOP),
            }
        cats.append({"name": c, "series": series, "tips": tips,
                     "y": _yaxis(0.0, max(max(series), 1.0)), "per": per})

    order = {p: sorted(
        (ci for ci, c in enumerate(cat_names) if per_totals[p][c] > 0.005),
        key=lambda ci: -per_totals[p][cat_names[ci]])
        for p in ("cur", "prev", "six")}
    month_name = MONTH_ABBR[cur[1]]
    partial_note = f" · {month_name} partial" if partial else ""
    periods: list[dict[str, object]] = [
        {"key": "cur", "label": "This month",
         "win": f"{_mon_yr(cur)} · {through}",
         "total": money0(sum(v for v in per_totals["cur"].values() if v > 0))},
        {"key": "six", "label": f"{len(months)} months",
         "win": _window_label(months) + partial_note,
         "total": money0(sum(v for v in per_totals["six"].values() if v > 0))},
    ]
    if prev and any(v > 0.005 for v in per_totals["prev"].values()):
        periods.insert(1, {
            "key": "prev", "label": "Last month", "win": _mon_yr(prev),
            "total": money0(sum(v for v in per_totals["prev"].values() if v > 0))})
    return {
        "periods": periods, "cats": cats, "order": order,
        "months": [MONTH_ABBR[m] for _, m in months],
        "trendWin": _window_label(months) + partial_note,
        "partialIdx": len(months) - 1 if partial else -1,
        "visible": CATS_VISIBLE,
    }


def _month_in_out(owner: str, ym: YM) -> tuple[float, float]:
    """home.month_in_out, owner-sliced: (income, expenses), both positive."""
    clause, params = owner_clause(owner, "p.other_account")
    rows = DB.rows(f"""
        SELECT CASE WHEN p.account LIKE 'Income:%' THEN 'inc' ELSE 'exp' END AS r,
               sum(p.amount_home) AS v
        FROM postings p
        WHERE (p.account LIKE 'Income:%' OR p.account LIKE 'Expenses:%')
          AND date_trunc('month', p.date) = $month::DATE{clause}
        GROUP BY ALL
    """, {"month": f"{ym[0]:04d}-{ym[1]:02d}-01", **params})
    vals = {_s(r["r"]): _f(r["v"]) for r in rows}
    return -vals.get("inc", 0.0), vals.get("exp", 0.0)


def _cheshbon(owner: str, pace: Pace) -> dict[str, object]:
    """home.cheshbon_ctx, owner-sliced: this month's in/out and how last
    month closed, payday-timing honesty included."""
    ym = pace.cur
    inc, exp = _month_in_out(owner, ym)
    net = inc - exp
    through_lbl = (f"through {MONTH_ABBR[ym[1]]} {pace.through_day}"
                   if pace.through_day else "nothing imported yet")
    prev = (ym[0] - 1, 12) if ym[1] == 1 else (ym[0], ym[1] - 1)
    pinc, pexp = _month_in_out(owner, prev)
    closed: dict[str, str] | None = None
    wink = ""
    if pinc or pexp:
        pnet = pinc - pexp
        closed = {"month": _mon_yr(prev), "inc": money0(pinc),
                  "exp": money0(pexp), "net": _delta0(pnet)}
        wink = (" — the book balances itself when you're not looking."
                if round(pnet) > 0 else ".")
    partial = not pace.fallback
    if round(net) > 0:
        net_cls = "pos"
    elif round(net) < 0 and not partial:
        net_cls = "neg"
    else:
        net_cls = ""
    return {
        "title": f"{MONTH_FULL[ym[1]]} money in / out",
        "window": f"{_mon_yr(ym)} · {through_lbl}",
        "inc": money0(inc), "exp": money0(exp), "net": _delta0(net),
        "net_cls": net_cls,
        "payday_note": ("paydays land later in the month — the net settles"
                        if partial and round(net) < 0 else ""),
        "closed": closed, "wink": wink,
    }


# ---------------------------------------------------------------- assembly
def build(owner: str, now: datetime | None = None) -> dict[str, object]:
    """The full /api/spend payload for one owner — same shape as the
    snapshot's, every dollar recomputed through the lens."""
    now = now or datetime.now()
    today = now.date()
    asof = _ledger_asof()
    totals = _monthly_totals(owner)
    pace = _paced(owner, today, asof, totals)
    return {
        "owner": owner,
        "pace": pace_ctx(pace, owner),
        "pace_chart": _pace_chart(pace),
        "tile": _tile(pace, _under_streak(totals, pace.cur)),
        "rooms": _rooms(owner, pace),
        "cheshbon": _cheshbon(owner, pace),
        "wins": None,  # household-only; the route overlays the snapshot's
    }
