"""Assemble the app's GET payloads from the EXISTING verified builders.

Every number here is produced by tools/home.py, webview.py, checks.py,
reports.py, or allocation.py — the same functions the static pages render.
This module only reshapes their output into JSON: money arrives as display
strings (m0/delta0 formatted, true minus, window-labelled), and plain
numbers appear ONLY as chart geometry. The frontend renders strings and
never does money math — the same contract the data island enforces.

The builders themselves are untyped modules; this file is the declared
boundary onto them (same posture as the plaid-python boundary in
pyproject.toml), so the three Unknown-propagation diagnostics are off HERE
ONLY — every other strict check still applies, and everything downstream
of this module is fully typed.
"""
# The _ctx/_tile builders are the same assembly surface summary.py already
# consumes; their underscore means "not part of the page template's context",
# not "not for assembly" — hence reportPrivateUsage off here too.
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false
# pyright: reportUnknownMemberType=false, reportUnknownLambdaType=false
# pyright: reportPrivateUsage=false
import html as html_mod
import re
from datetime import date, datetime, timedelta
from typing import cast

from sara.advisor.allocation import allocation_view
from sara.advisor.checks import goals as goals_config
from sara.advisor.checks import lane_status
from sara.advisor.dismissals import active_ids, finding_id, load_dismissals
from sara.advisor.home import (
    MINUS,
    MONTH_FULL,
    Card,
    Pace,
    _attribution_ctx,
    _auto_tile,
    _cheshbon_ctx,
    _education_ctx,
    _machine_ctx,
    _moneymap_ctx,
    _networth_ctx,
    _next_ctx,
    _nw_chart_data,
    _pace_chart_data,
    _pace_ctx,
    _sparkline,
    _spend_tile,
    _wins_ctx,
    education_accounts,
    education_pace,
    findings_date,
    m0,
    mon_d,
    monthly_expense_totals,
    must_move,
    needs_you,
    sara_line,
    saras_wins,
    spend_pace,
    spending_data,
    under_streak,
)
from sara.advisor.reports import liquid_balances, paper_value
from sara.advisor.webview import (
    MONTH_ABBR,
    _units,
    action_queue,
    latest_ledger_date,
    milestone_state,
    networth_series,
    parse_findings,
    price_history,
)
from sara.vault import (
    REPORTS,
    VAULT,
    amount,
    household,
    illiquid_currency_regex,
    query,
    shadow_currency,
)

ACTIVITY_UNCAT = ("Expenses:Uncategorized", "Expenses:FIXME")
GOAL_KEYS = ("education_target",)
SPARK_MIN_POINTS = 4   # fewer month-ends than this draw noise, not a shape
SINCE_DAYS = 7
ASK_529 = ("goals", "set-college-target")  # the one-time Goals question card

# ------------------------------------------------------------- sanitizing
_CODE_TAG = re.compile(r"<code>(.*?)</code>", re.S)
_ANY_TAG = re.compile(r"<[^>]+>")


def _text(value: object) -> str:
    """Markup -> plain text; <code>x</code> keeps its backticks so the
    frontend can re-render code spans safely on its own side."""
    s = _CODE_TAG.sub(r"`\1`", str(value))
    return html_mod.unescape(_ANY_TAG.sub("", s))


def clean(obj: object) -> object:
    """Builder output -> JSON-safe: dates to ISO, Markup to text, tuples to
    lists, floats rounded (chart geometry keeps 2 decimals of honesty)."""
    if isinstance(obj, dict):
        d = cast(dict[object, object], obj)
        return {str(k): clean(v) for k, v in d.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v) for v in cast("list[object] | tuple[object, ...]", obj)]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, float):
        return round(obj, 2)
    if isinstance(obj, str):
        return obj if type(obj) is str else _text(obj)
    if obj is None or isinstance(obj, (bool, int)):
        return obj
    return _text(obj)


def _feed_money(v: float, *, income: bool = False) -> str:
    """Receipt-grade money for the Activity feed: cents shown when the
    posting has them, whole dollars when it doesn't (the reports' m0 rounds
    aggregates; a feed row is a receipt and keeps its 6.75). Income rows
    lead with +; a refund on an expense account reads with the true minus."""
    signed = -v if income else v
    mag = abs(signed)
    body = (f"${mag:,.2f}" if abs(mag - round(mag)) >= 0.005
            else f"${round(mag):,.0f}")
    if income:
        return ("+" if signed >= 0 else MINUS) + body
    return body if signed >= 0 else MINUS + body


def _card(c: Card) -> dict[str, object]:
    return {"kind": c.kind, "verb": _text(c.verb), "why": _text(c.why),
            "meta": c.meta, "days": c.days}


# ------------------------------------------------------- shared per-request
def _paced(today: date, asof: date | None,
           totals: list[tuple[tuple[int, int], float]]) -> Pace:
    """The calendar month's pace, falling back to the latest imported month
    when the ledger is stale — the same two steps build_page takes."""
    pace = spend_pace(today, asof, totals)
    if pace.through_day is None and totals:
        last_m = max(ym for ym, _ in totals)
        if last_m < pace.cur:
            pace = spend_pace(today, asof, totals, month=last_m)
    return pace


def _daypart(now: datetime) -> str:
    return ("morning" if now.hour < 12
            else "afternoon" if now.hour < 18 else "evening")


# Sara's line distinguishes late evening ("tonight"); the greeting keeps the
# plain three dayparts — "Good night" would read as a sign-off.
SARA_DAYPARTS = ("morning", "afternoon", "evening", "night")


def _sara_daypart(now: datetime) -> str:
    return "night" if now.hour >= 22 else _daypart(now)


def _friendly_date(iso: str | None) -> str | None:
    """'2026-08-06' -> 'Aug 6' (stamps read like a person wrote them)."""
    if not iso:
        return None
    try:
        return mon_d(date.fromisoformat(iso))
    except ValueError:
        return iso


def _milestones(liquid: float) -> dict[str, object] | None:
    """milestone_state, with the dollars formatted server-side."""
    ms = milestone_state(liquid)
    if not ms:
        return None
    nxt_raw = ms["next"]
    nxt = float(nxt_raw) if isinstance(nxt_raw, (int, float)) else None
    pct_raw = ms["pct"]
    pct = float(pct_raw) if isinstance(pct_raw, (int, float)) else 0.0
    crossed = ms["crossed"]
    return {
        "pct": round(pct, 1),
        "next": m0(nxt) if nxt is not None else None,
        "crossed": len(crossed) if isinstance(crossed, list) else 0,
        "label": (f"{pct:.0f}% of the way to {m0(nxt)}"
                  if nxt is not None else "every declared milestone crossed"),
    }


# ------------------------------------------------------------------ glance
def _event_short(text: str, limit: int = 40) -> str:
    """A must-move bullet's short name: the first clause, word-boundary
    trimmed ('DUOL vest: 170 sh gross …' -> 'DUOL vest')."""
    head = re.split(r"[:;(]", text, maxsplit=1)[0].strip()
    if len(head) <= limit:
        return head
    cut = head[:limit].rsplit(" ", 1)[0].rstrip(",.–- ")  # noqa: RUF001 — strips a trailing en dash too
    return f"{cut}…"


def spotlight_tile(edu_tile: dict[str, object],
                   today: date) -> dict[str, object]:
    """The glance's adaptive fourth tile. Priority: the biggest realized win
    this year -> the next dated money event -> the 529's status. The 529
    ASK (pick a target) lives in Goals now — up here it reads as status."""
    wins = saras_wins(today)
    if wins and wins["items"]:
        best = wins["items"][0]
        return {"kind": "win", "label": "Found money",
                "verdict": m0(best["amt"]) + ("/yr" if best["peryr"] else ""),
                "cls": "good", "fig": "",
                "sub": _text(best["label"])}
    moves = [mv for mv in must_move(today) if not mv["plumbing"]]
    if moves:
        mv = moves[0]
        return {"kind": "event", "label": "Coming up",
                "verdict": "", "cls": "", "fig": str(mv["amt"]),
                "sub": f"{_event_short(str(mv['text']))} · "
                       f"{mv['day_lbl']} · {mv['when']}"}
    tile: dict[str, object] = {
        "kind": "edu", **{k: _text(v) for k, v in edu_tile.items()}}
    if tile.get("verdict") == "Needs a target":  # the ask moved into Goals
        tile.update(verdict="", cls="", sub="no college target set yet")
    return tile


def _since_line(lanes: list[dict[str, object]], today: date) -> str | None:
    """What the machine landed while you were gone — one plain sentence
    from lane_status's own rows, or nothing when the week was quiet."""
    floor_d = today - timedelta(days=SINCE_DAYS)
    paychecks = 0
    invested = 0.0
    for r in lanes:
        last = r.get("last")
        if not isinstance(last, date) or last < floor_d or r["status"] != "ok":
            continue
        if r["kind"] == "deposit":
            paychecks += 1
        elif r["kind"] == "invest" and r.get("last_amount"):
            invested += float(cast(float, r["last_amount"]))
    bits: list[str] = []
    if paychecks:
        bits.append(f"{paychecks} paycheck{'s' if paychecks != 1 else ''} landed")
    if invested >= 1:
        bits.append(f"{m0(invested)} auto-invested")
    if not bits:
        return None
    return f"Since {mon_d(floor_d)}: {', '.join(bits)}."


def _autopilot_tile(mach: dict[str, object]) -> dict[str, object]:
    """_auto_tile with the copy in civilian words: every count carries a
    noun, and 'watching for a first arrival' says what is being watched."""
    tile = _auto_tile(mach)
    rows = cast("list[dict[str, object]]", mach["rows"])
    n = len(rows)
    if not n:
        return tile
    green = sum(1 for r in rows if r["dot"] == "ok")
    broken = sum(1 for r in rows if r["dot"] == "bad")
    watching = sum(1 for r in rows if r["dot"] == "watch")
    if broken:
        tile["sub"] = "a money move misfired — details in Autopilot"
    elif watching:
        tile["verdict"] = f"{green} of {n} landing"
        tile["sub"] = (f"{watching} new transfer"
                       f"{'s' if watching != 1 else ''} being watched")
    else:
        tile["verdict"] = "All landing"
        tile["sub"] = "paychecks, auto-invests, floors — all on schedule"
    return tile


def glance(now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now()
    today = now.date()
    balances, _unpriced = liquid_balances()
    liquid = sum(v for _, v in balances)
    asof = latest_ledger_date()
    goals = goals_config()
    totals = monthly_expense_totals()
    pace = _paced(today, asof, totals)
    cards, more, needs_state = needs_you(today)
    lanes = lane_status(today)
    mach = _machine_ctx(lanes)
    series, cut = networth_series(liquid, asof)
    nw = _networth_ctx(series, cut, liquid, asof)
    edu_accounts = education_accounts()
    edu = _education_ctx(edu_accounts,
                         education_pace(edu_accounts) if edu_accounts else None,
                         goals, today)
    moves = [mv for mv in must_move(today) if not mv["plumbing"]]
    names = household("names")
    daypart = _daypart(now)
    checks_from = _friendly_date(findings_date())
    return cast(dict[str, object], clean({
        "generated_at": now,
        "greet": f"Good {daypart}, {names}" if names else f"Good {daypart}",
        "sara": sara_line(pace, needs_state, cards, more, _sara_daypart(now)),
        # every daypart, so the snapshot-served glance greets in the
        # requester's part of day (sara/server/live.py picks one)
        "sara_by_daypart": {dp: sara_line(pace, needs_state, cards, more, dp)
                            for dp in SARA_DAYPARTS},
        "ledger_stamp": (f"Ledger through {mon_d(asof)}" if asof
                         else "Ledger empty"),
        "checks_stamp": (f"checks from {checks_from}" if checks_from else ""),
        "tiles": {
            "spend": _spend_tile(pace, under_streak(totals, pace.cur)),
            "networth": {
                "value": m0(liquid),
                "delta": ({"cls": nw["delta"]["cls"],
                           "text": _text(nw["delta"]["body"])}
                          if nw["delta"] else None),
                # under four month-ends a sparkline is noise — number + delta
                # carry the tile until the history earns a shape
                "spark": (_sparkline(series)
                          if len(series) >= SPARK_MIN_POINTS else None),
                "sub": "liquid · " + nw["asof"],
                "glow": bool(nw["delta"] and nw["delta"]["cls"] == "good"),
            },
            "autopilot": _autopilot_tile(mach),
            "spotlight": spotlight_tile(cast("dict[str, object]", edu["tile"]),
                                        today),
        },
        "since": _since_line(lanes, today),
        "next": _next_ctx(needs_state, cards, more, moves),
    }))


# ---------------------------------------------------------------- activity
def _activity_months() -> list[tuple[int, int]]:
    rows = query("SELECT year, month, count(*) AS n "
                 "WHERE account ~ '^(Income|Expenses)' GROUP BY year, month "
                 "ORDER BY year, month")
    out: list[tuple[int, int]] = []
    for r in rows:
        try:
            out.append((int(r["year"]), int(r["month"])))
        except (TypeError, ValueError):
            continue
    return out


def _cat_chip(account: str) -> str:
    """'Income:US:Salary:BrightPathLabs' -> 'Salary · BrightPathLabs' —
    the 2-letter country segment is plumbing, not identity (same rule the
    money map applies to account names)."""
    segs = account.split(":")[1:]
    if segs and re.fullmatch(r"[A-Z]{2}", segs[0]):
        segs = segs[1:]
    return " · ".join(segs) or account


def _expense_categories() -> list[dict[str, str]]:
    """Open Expenses/Income accounts — the teach-a-rule picker's choices."""
    rows = query("SELECT account, open_date(account) AS opened "
                 "GROUP BY account, opened ORDER BY account")
    out: list[dict[str, str]] = []
    for r in rows:
        acct = r["account"]
        if not acct.startswith(("Expenses:", "Income:")):
            continue
        if acct in ACTIVITY_UNCAT:
            continue
        out.append({"account": acct,
                    "label": acct.split(":", 1)[1].replace(":", " · ")})
    return out


def activity(month: str | None = None) -> dict[str, object]:
    """The transaction feed, one calendar month per page, newest first.
    Rows are the categorized legs (Expenses/Income postings); a row still
    sitting in the review bucket is flagged uncategorized and carries what
    the inline picker needs to teach a rule."""
    months = _activity_months()
    if not months:
        return {"months": [], "month": None, "days": [],
                "categories": [], "uncategorized_total": 0}
    sel = months[-1]
    if month:
        m = re.fullmatch(r"(\d{4})-(\d{2})", month)
        if m and (int(m.group(1)), int(m.group(2))) in months:
            sel = (int(m.group(1)), int(m.group(2)))
    rows = query(f"SELECT date, payee, narration, account, "
                 f"convert(position,'USD') AS v, "
                 f"entry_meta('classifier') AS clf "
                 f"WHERE account ~ '^(Income|Expenses)' "
                 f"AND year = {sel[0]} AND month = {sel[1]} ORDER BY date")
    by_day: dict[str, list[dict[str, object]]] = {}
    month_spend = 0.0
    month_in = 0.0
    uncat_month = 0
    for r in rows:
        v = amount(r["v"])
        acct = r["account"]
        uncat = acct in ACTIVITY_UNCAT
        income = acct.startswith("Income")
        if income:
            month_in += -v
        else:
            month_spend += v
        if uncat:
            uncat_month += 1
        by_day.setdefault(r["date"], []).append({
            "payee": (r["payee"] or "").strip() or "(no payee)",
            "narration": (r["narration"] or "").strip(),
            "account": acct,
            "category": "Uncategorized" if uncat else _cat_chip(acct),
            "amt": _feed_money(v, income=income),
            "kind": ("uncategorized" if uncat
                     else "income" if income else "expense"),
            # machine-classified provenance ("rule" / "plaid:X" / "haiku:0.91"),
            # "" for hand-categorized rows — fuel for a tiny chip in Activity
            "classifier": (r.get("clf") or "").strip(),
        })
    days: list[dict[str, object]] = []
    for iso in sorted(by_day, reverse=True):
        d = date.fromisoformat(iso)
        days.append({"date": iso,
                     "label": f"{d.strftime('%a')} {mon_d(d)}",
                     "rows": list(reversed(by_day[iso]))})
    uncat_rows = query("SELECT count(*) AS n WHERE account = "
                       "'Expenses:Uncategorized' OR account = 'Expenses:FIXME'")
    uncat_total = int(float(uncat_rows[0]["n"] or 0)) if uncat_rows else 0
    return cast(dict[str, object], clean({
        "months": [{"ym": f"{y:04d}-{m:02d}", "label": f"{MONTH_ABBR[m]} {y}"}
                   for y, m in reversed(months)],
        "month": f"{sel[0]:04d}-{sel[1]:02d}",
        "label": f"{MONTH_FULL[sel[1]]} {sel[0]}",
        "totals": {"spent": m0(month_spend), "received": m0(month_in),
                   "window": f"{MONTH_ABBR[sel[1]]} {sel[0]}"},
        "days": days,
        "categories": _expense_categories(),
        "uncategorized_month": uncat_month,
        "uncategorized_total": uncat_total,
    }))


# ------------------------------------------------------------------- spend
def spend(now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now()
    today = now.date()
    asof = latest_ledger_date()
    totals = monthly_expense_totals()
    pace = _paced(today, asof, totals)
    sp = spending_data(pace)
    island = ({k: v for k, v in sp.items()
               if not k.startswith("table_") and k != "six_lbl"}
              if sp else None)
    return cast(dict[str, object], clean({
        "pace": _pace_ctx(pace),
        "pace_chart": _pace_chart_data(pace),
        "tile": _spend_tile(pace, under_streak(totals, pace.cur)),
        "rooms": island,
        "cheshbon": _cheshbon_ctx(pace),
        "wins": _wins_ctx(saras_wins(today), today),
    }))


# ---------------------------------------------------------------- networth
def _cash_story() -> dict[str, object] | None:
    """One plain sentence about parked cash, from the allocation view the
    mix card already scores — the map room's whole cash surface."""
    alloc = allocation_view()
    if alloc is None or alloc.reserve_usd <= 0:
        return None
    if alloc.reserve_short > 0.5:
        return {"cls": "bad",
                "line": (f"Cash is {m0(alloc.reserve_short)} short of your "
                         f"{m0(alloc.reserve_usd)} reserve.")}
    if alloc.cash_above_reserve <= 0.5:
        return {"cls": "",
                "line": (f"Cash sits right at your {m0(alloc.reserve_usd)} "
                         f"reserve — nothing extra parked.")}
    return {"cls": "",
            "line": (f"{m0(alloc.cash_above_reserve)} parked in cash above "
                     f"your {m0(alloc.reserve_usd)} reserve.")}


def networth() -> dict[str, object]:
    today = date.today()
    balances, unpriced = liquid_balances()
    liquid = sum(v for _, v in balances)
    asof = latest_ledger_date()
    series, cut = networth_series(liquid, asof)
    paper = paper_value()
    return cast(dict[str, object], clean({
        "headline": _networth_ctx(series, cut, liquid, asof),
        "chart": _nw_chart_data(series, asof),
        "spark": _sparkline(series),
        "attribution": _attribution_ctx(series, asof),
        "map": _moneymap_ctx(balances, liquid, asof),
        "cash": _cash_story(),
        "paper": m0(paper) if paper else None,
        "unpriced": [a for a, _ in unpriced],
        "milestones": _milestones(liquid),
        "window": f"through {mon_d(asof)}" if asof else "ledger empty",
        "generated_for": today,
    }))



# ------------------------------------------------------------- investments
def investments(now: datetime | None = None) -> dict[str, object]:
    """Positions, allocation vs thesis, dividends YTD, contribution pace —
    every dollar from the same queries the reports run."""
    now = now or datetime.now()
    year = now.year
    asof = latest_ledger_date()
    prices = price_history()
    excl = illiquid_currency_regex()
    rows = query("SELECT currency, sum(position) AS units, "
                 "sum(convert(position,'USD')) AS usd "
                 "WHERE account ~ '^Assets' AND currency != 'USD' "
                 "GROUP BY currency ORDER BY currency")
    positions: list[dict[str, object]] = []
    paper_syms: list[str] = []
    invested = 0.0
    for r in rows:
        cell = r["usd"] or ""
        sym = r["currency"]
        units = _units(r["units"], sym)
        if abs(units) < 1e-9:
            continue
        if excl and re.match(excl, sym):
            # illiquid paper: never valued in dollars here — its prices are
            # marks in the shadow currency, and the thesis keeps it out of
            # every liquid figure. It rides the footnote, not the table.
            paper_syms.append(sym)
            continue
        usd = amount(cell) if "USD" in cell else None
        pts = prices.get(sym, [])
        last = pts[-1] if pts else None
        if usd:
            invested += usd
        positions.append({
            "symbol": sym,
            "units": f"{units:,.4f}".rstrip("0").rstrip("."),
            "value": m0(usd) if usd is not None else None,
            "valueN": round(usd, 2) if usd is not None else 0.0,
            "price": f"${last[1]:,.2f}" if last else None,
            "price_date": mon_d(last[0]) if last else None,
        })
    positions.sort(key=lambda p: -cast(float, p["valueN"]))
    for p in positions:
        v = cast(float, p["valueN"])
        p["share"] = (f"{100.0 * v / invested:.0f}%"
                      if invested and v else "—")

    div_rows = query(f"SELECT sum(convert(position,'USD')) AS v, count(*) AS n "
                     f"WHERE account ~ '^Income' AND account ~ 'Dividend' "
                     f"AND year = {year}")
    div_total = -amount(div_rows[0]["v"]) if div_rows and div_rows[0].get("v") else 0.0
    div_n = int(float(div_rows[0]["n"] or 0)) if div_rows else 0

    alloc = allocation_view()
    donut = None
    if alloc and alloc.invested > 0:
        def _drift(value: float, target_pct: float) -> str | None:
            # dollars to move to sit on target — the drift line the old
            # map-room strip carried, absorbed into the one mix card
            diff = value - target_pct / 100.0 * alloc.invested
            if abs(diff) < 0.5:
                return None
            return f"≈{m0(abs(diff))} {'over' if diff > 0 else 'under'}"
        donut = {
            "invested": m0(alloc.invested),
            "rows": [{"label": r.label, "value": round(r.value, 2),
                      "amt": m0(r.value), "pct": f"{r.share_pct:.0f}%",
                      "target": f"{r.target_pct:g}%",
                      "out": r.out_of_band,
                      "drift": (_drift(r.value, r.target_pct)
                                if r.out_of_band else None)}
                     for r in alloc.rows],
            "cash_above_reserve": (m0(alloc.cash_above_reserve)
                                   if alloc.reserve_usd > 0 else None),
        }
    ytd_win = f"Jan–{MONTH_ABBR[now.month]} {year}"  # noqa: RUF001 — window labels keep the en dash
    paper = paper_value()
    paper_note = None
    if paper_syms:
        paper_note = (f"Illiquid paper ({', '.join(paper_syms)}) stays out, "
                      f"per THESIS — worth ≈{m0(paper)} in "
                      f"{shadow_currency()} marks if it ever converts.")
    return cast(dict[str, object], clean({
        "window": f"through {mon_d(asof)}" if asof else "ledger empty",
        "positions": positions,
        "paper_note": paper_note,
        "invested_total": m0(invested),
        "allocation": donut,
        "dividends": {"ytd": m0(div_total), "count": div_n,
                      "window": ytd_win,
                      "note": ("no dividend income booked this year"
                               if div_total < 0.005 else "")},
    }))


# ------------------------------------------------------------------- goals
def ask_529(education: dict[str, object], goals: dict[str, object],
            today: date) -> dict[str, object] | None:
    """The one-time Goals question card: a 529 exists but no target does.
    'Not now' sticks through the dismissals chokepoint; setting the target
    retires the question for good."""
    if education.get("empty") or goals.get("education_target"):
        return None
    fid = finding_id(*ASK_529)
    return {"id": fid, "dismissed": fid in active_ids(today)}


def goals_payload(now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now()
    today = now.date()
    goals = goals_config()
    edu_accounts = education_accounts()
    edu = _education_ctx(edu_accounts,
                         education_pace(edu_accounts) if edu_accounts else None,
                         goals, today)
    balances, _ = liquid_balances()
    liquid = sum(v for _, v in balances)

    def _setting(key: str) -> dict[str, object]:
        raw = goals.get(key)
        return {"key": key, "value": raw if raw is not None else None}

    return cast(dict[str, object], clean({
        "education": edu,
        "ask": ask_529(cast("dict[str, object]", edu), goals, today),
        "milestones": _milestones(liquid),
        "settings": [_setting(k) for k in GOAL_KEYS],
        "window": "targets you set · balances at the latest prices",
    }))



# --------------------------------------------------------------- autopilot
def autopilot(now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now()
    today = now.date()
    mach = _machine_ctx(lane_status(today))
    findings, counts, errors = parse_findings()
    queue = action_queue(findings) if findings else []
    dismissed = load_dismissals()
    silenced = active_ids(today)
    all_moves = must_move(today)
    cards, more, needs_state = needs_you(today)
    uncat = query("SELECT count(*) AS n, sum(convert(position,'USD')) AS v "
                  "WHERE account = 'Expenses:Uncategorized'")
    n_uncat = int(float(uncat[0]["n"] or 0)) if uncat else 0
    v_uncat = amount(uncat[0]["v"]) if uncat and uncat[0].get("v") else 0.0
    return cast(dict[str, object], clean({
        "machine": mach,
        "needs": {"state": needs_state, "cards": [_card(c) for c in cards],
                  "more": more},
        "queue": [{**f, "id": finding_id(f["check"], f["title"]),
                   "fix": _text(f["fix"])} for f in queue],
        "dismissed": [{"id": fid,
                       "until": entry.get("until"),
                       "title": entry.get("title", ""),
                       "active": fid in silenced}
                      for fid, entry in sorted(dismissed.items())],
        "moves": [mv for mv in all_moves if not mv["plumbing"]],
        "plumbing": [mv for mv in all_moves if mv["plumbing"]],
        "review": {"count": n_uncat, "amount": m0(v_uncat),
                   "note": ("teach a rule from the Activity room"
                            if n_uncat else "review queue is clear")},
        "counts": counts,
        "errors": errors,
        "checks_from": _friendly_date(findings_date()),
        "findings_ran": findings is not None,
    }))


# --------------------------------------------------------------- freshness
def freshness(now: datetime | None = None) -> dict[str, object]:
    now = now or datetime.now()
    asof = latest_ledger_date()
    prices = price_history()
    latest_price: date | None = None
    for pts in prices.values():
        if pts and (latest_price is None or pts[-1][0] > latest_price):
            latest_price = pts[-1][0]
    acct_rows = query("SELECT account, max(date) AS d "
                      "WHERE account ~ '^(Assets|Liabilities)' "
                      "GROUP BY account ORDER BY account")
    accounts: list[dict[str, object]] = []
    for r in acct_rows:
        try:
            d = date.fromisoformat(r["d"])
        except (TypeError, ValueError):
            continue
        accounts.append({"account": r["account"], "last_posting": d,
                         "days_quiet": (now.date() - d).days})
    return cast(dict[str, object], clean({
        "generated_at": now,
        "vault": str(VAULT),
        "ledger_through": asof,
        "checks_from": findings_date(),
        "prices_through": latest_price,
        "accounts": accounts,
        "reports_dir": str(REPORTS),
    }))


# ------------------------------------------------------------ app snapshot
def app_snapshot(now: datetime | None = None) -> dict[str, object]:
    """Every ledger-derived room payload, materialized at report time.

    tools/summary.py embeds this under summary.json's ``app`` key — the CQRS
    write side. The server (sara.server.app) serves these payloads verbatim
    and overlays only the file-backed live parts (findings, dismissals,
    goals, facts calendars) plus the request-time greeting; it never parses
    the ledger on a GET. Activity/register/search come from
    reports/analytics.duckdb instead and are not snapshotted.
    """
    now = now or datetime.now()
    return {
        "schema": 1,
        "generated_at": now.isoformat(timespec="seconds"),
        "glance": glance(now),
        "spend": spend(now),
        "networth": networth(),
        "investments": investments(now),
        "goals": goals_payload(now),
        "autopilot": autopilot(now),
    }
