# pyright: strict
#!/usr/bin/env python3
"""Shared read-side builders: findings, the needs-you queue, price history,
and the net-worth series every page trusts.

No page is generated here (the static dashboard retired 2026-08-07 — Sara
App is the daily driver, home.py the print glance). This module is the one
place the needs-you queue is shaped and the one place ledger prices become
a valuation, so Sara Home, the digest, summary.json, and the app server can
never disagree:

  parse_findings / action_queue — reports/findings.md -> the max-3 queue
  price_history / networth_series — dated prices (explicit directives PLUS
      prices implied by transaction costs and @ annotations) -> month-end
      liquid net worth, endpoint pinned to the headline
  deadline_items / milestone_state — dated facts and goal milestones

Honesty rules for the series: months that predate the ledger's opening
balances are dropped (they'd misstate the household), months leaning on
cost basis for lack of a dated price are flagged est, and the final point
IS the headline number so hero and curve always agree.
"""
from __future__ import annotations

import re
from calendar import monthrange
from datetime import date, datetime

from sara.vault import (REPORTS, VAULT, amount, dated_bullets,
                   illiquid_currency_regex, query)
from sara.advisor.dismissals import filter_findings
from sara.advisor.checks import goals as goals_config
from sara.advisor.types import YM, Finding, Money, Payload

MAX_CURVE_POINTS = 24      # two years of month-ends is a curve; more is wallpaper
PRICE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+price\s+(\S+)\s+([\d.,]+)\s+USD\b", re.M)
MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def month_label(y: int, m: int) -> str:
    return f"{MONTH_ABBR[m]} {y}"


def nice_ticks(lo: float, hi: float, n: int = 4) -> list[float]:
    """~n clean tick values covering [lo, hi]."""
    span = (hi - lo) or 1.0
    raw = span / max(n, 1)
    mag = 10 ** len(str(int(abs(raw)))) / 10 if raw >= 1 else 1.0
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), 10 * mag)
    first = step * (lo // step)
    ticks: list[float] = []
    t = first
    while t <= hi + step:
        if t >= lo - 1e-9:
            ticks.append(round(t, 6))
        t += step
    ticks = ticks[:n + 2]
    if len(ticks) >= 2 and ticks[-1] - hi > 0.6 * step:
        ticks.pop()  # a top gridline far above the data is dead air
    return ticks


# ------------------------------------------------------------------- data
def latest_ledger_date() -> date | None:
    rows = query("SELECT max(date) AS d WHERE account ~ '^(Assets|Liabilities)'")
    try:
        return datetime.strptime(rows[0]["d"], "%Y-%m-%d").date()
    except (IndexError, KeyError, TypeError, ValueError):
        return None


# Implicit prices — the acquisition marks transactions themselves carry
# (`10 VTSAX {29.00 USD}` costs, `@ 29.00 USD` / `@@ 290.00 USD`
# annotations), booked at the transaction's date. The same idea as
# beancount's implicit_prices plugin, extracted textually so months with
# buys are valued at market from day one instead of falling back to cost.
_TXN_HEADER = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(?:txn\b|[*!])")
_AT_PRICE = re.compile(r"\s(-?[\d,]+(?:\.\d+)?)\s+([A-Z][A-Z0-9._-]*)\s[^;]*?"
                       r"@(@?)\s*([\d,]+(?:\.\d+)?)\s+USD(?![\w.])")
_COST_PRICE = re.compile(r"\s(-?[\d,]+(?:\.\d+)?)\s+([A-Z][A-Z0-9._-]*)\s+"
                         r"\{\{?\s*([\d,]+(?:\.\d+)?)\s+USD")


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _implicit_prices(text: str, prices: dict[str, list[tuple[date, Money]]]) -> None:
    """Append (date, price) marks implied by the file's transactions."""
    when = None
    for line in text.splitlines():
        m = _TXN_HEADER.match(line)
        if m:
            try:
                when = date.fromisoformat(m.group(1))
            except ValueError:
                when = None
            continue
        if when is None or not line[:1].isspace() or line.lstrip().startswith(";"):
            continue
        at = _AT_PRICE.search(line)
        if at:
            units, sym, total_flag, px = at.groups()
            if sym != "USD" and abs(_num(units)) > 1e-9:
                per = _num(px) / abs(_num(units)) if total_flag else _num(px)
                prices.setdefault(sym, []).append((when, per))
            continue
        cost = _COST_PRICE.search(line)
        if cost and cost.group(2) != "USD":
            prices.setdefault(cost.group(2), []).append((when, _num(cost.group(3))))


def price_history() -> dict[str, list[tuple[date, Money]]]:
    """{symbol: [(date, usd_price)…]} — explicit price directives plus the
    implicit marks transactions carry (costs and @ prices), so a month with
    a buy is valued at market even before the first price directive lands.
    Sorted by date; on a same-date tie the explicit directive wins."""
    implicit: dict[str, list[tuple[date, Money]]] = {}
    explicit: dict[str, list[tuple[date, Money]]] = {}
    ledger_dir = VAULT / "ledger"
    if ledger_dir.is_dir():
        for f in sorted(ledger_dir.rglob("*.beancount")):
            try:
                txt = f.read_text()
            except OSError:
                continue
            _implicit_prices(txt, implicit)
            for m in PRICE_RE.finditer(txt):
                explicit.setdefault(m.group(2), []).append(
                    (date.fromisoformat(m.group(1)), _num(m.group(3))))
    out: dict[str, list[tuple[date, Money]]] = {}
    for sym in implicit.keys() | explicit.keys():
        marks = implicit.get(sym, []) + explicit.get(sym, [])
        out[sym] = sorted(marks, key=lambda t: t[0])  # stable: explicit last
    return out


def units_of(cell: str | None, currency: str) -> float:
    """Sum every `<number> <currency>` run in a bean-query inventory cell
    (a cell can hold several lots: '340 AGGRX {29.00 USD}, 10 AGGRX {…}')."""
    total = 0.0
    for m in re.finditer(rf"(-?\d[\d,]*(?:\.\d+)?)\s+{re.escape(currency)}(?![.\w])",
                         cell or ""):
        total += float(m.group(1).replace(",", ""))
    return total


COST_SHARE_EST = 0.10      # >10% of a month's value on cost fallback = "at cost"
BASELINE_SHARE = 0.95      # curve starts once 95% of opening-balance dollars exist


def baseline_complete_date() -> date | None:
    """The date by which ≥95% (dollar-weighted) of opening-balance value had
    been booked, or None if the ledger has no Equity:Opening postings. Before
    this date at least one account is missing its baseline, so a month-end
    total there is missing money — not history."""
    rows = query("SELECT date, sum(convert(position, 'USD')) AS v "
                 "WHERE account ~ '^Equity:Opening' GROUP BY date ORDER BY date")
    dated: list[tuple[date, Money]] = []
    for r in rows:
        try:
            dated.append((date.fromisoformat(r["date"]), abs(amount(r["v"]))))
        except (KeyError, TypeError, ValueError):
            continue
    total = sum(v for _, v in dated)
    if not total:
        return None
    run = 0.0
    for d, v in dated:
        run += v
        if run >= BASELINE_SHARE * total:
            return d
    return dated[-1][0]


def networth_series(liquid_now: Money, asof: date | None) -> tuple[list[Payload], int]:
    """[{d, v, est}…] month-end liquid net worth. One ledger query: monthly
    deltas per currency in units + cost; valued at the latest dated price on
    or before each month end, else cost basis. est=True marks a month leaning
    on cost fallback for >10% of its value (drawn dashed, labelled at cost).
    Months before the opening-balance baseline are dropped. The final point
    IS liquid_now (the headline, at latest prices) so hero and curve can
    never disagree."""
    excl = illiquid_currency_regex()
    where = ("account ~ '^(Assets|Liabilities)'"
             + (f" AND NOT currency ~ '{excl.replace(chr(39), chr(39) * 2)}'" if excl else ""))
    rows = query(f"SELECT year, month, currency, sum(position) AS units, "
                 f"sum(cost(position)) AS cost WHERE {where} "
                 f"GROUP BY year, month, currency ORDER BY year, month")
    per_month: dict[YM, dict[str, list[float]]] = {}  # ym -> currency -> [units, cost]
    for r in rows:
        try:
            ym = (int(r["year"]), int(r["month"]))
        except (TypeError, ValueError):
            continue
        cur = r["currency"]
        d_units = amount(r["units"], "USD") if cur == "USD" else units_of(r["units"], cur)
        d_cost = amount(r["cost"], "USD")
        u, c = per_month.setdefault(ym, {}).setdefault(cur, [0.0, 0.0])
        per_month[ym][cur] = [u + d_units, c + d_cost]
    months = sorted(per_month)
    if not months:
        return [], 0
    prices = price_history()
    cum: dict[str, list[float]] = {}  # currency -> [units, cost]
    points: list[Payload] = []
    for y, m in months:
        flow = 0.0  # the month's booked flow: postings at cost, all currencies
        for cur, (du, dc) in per_month[(y, m)].items():
            u, c = cum.setdefault(cur, [0.0, 0.0])
            cum[cur] = [u + du, c + dc]
            flow += du if cur == "USD" else dc
        d = date(y, m, monthrange(y, m)[1])
        total, at_cost = 0.0, 0.0
        for cur, (u, c) in cum.items():
            if cur == "USD":
                total += u
            elif abs(u) > 1e-9:
                px = [p for pd, p in prices.get(cur, []) if pd <= d]
                total += u * px[-1] if px else c
                if not px:
                    at_cost += abs(c)
        points.append({"d": d, "v": round(total, 2), "flow": round(flow, 2),
                       "est": at_cost > COST_SHARE_EST * max(abs(total), 1.0)})
    # the last month is partial (data reaches asof, not month end): date it
    # honestly and pin it to the headline valuation — the headline is the
    # latest-prices number by definition, never a cost estimate
    if asof:
        points[-1]["d"] = min(points[-1]["d"], asof)
    points[-1]["v"] = liquid_now
    points[-1]["est"] = False
    baseline = baseline_complete_date()
    cut = 0
    if baseline:
        kept = [p for p in points if p["d"] >= baseline]
        cut, points = len(points) - len(kept), kept
    return points[-MAX_CURVE_POINTS:], cut


def parse_findings() -> tuple[list[Finding] | None, str, list[str]]:
    """reports/findings.md -> (findings, counts_line, errors). Text is DATA."""
    path = REPORTS / "findings.md"
    if not path.exists():
        return None, "", []
    text = path.read_text()
    counts = re.search(r"^\*\*(.+?)\*\*$", text, re.M)
    errors: list[str] = []
    err = re.search(r"^## Check errors\n(.*)", text, re.M | re.S)
    if err:
        errors = [ln[2:].strip() for ln in err.group(1).splitlines() if ln.startswith("- ")]
        text = text[:err.start()]
    findings: list[Finding] = []
    for block in re.split(r"^### ", text, flags=re.M)[1:]:
        lines = block.strip().splitlines()
        title = re.sub(r"^[^\w$~(]+", "", lines[0]).strip()  # drop the icon
        sev, check = "info", ""
        detail: list[str] = []
        for line in lines[1:]:
            m = re.match(r"^_(.+?) · (alert|watch|info)_\s*$", line)
            if m:
                check, sev = m.group(1), m.group(2)
            elif line.strip():
                detail.append(line.strip())
        findings.append({"title": title, "severity": sev, "check": check,
                         "detail": " ".join(detail)})
    return findings, (counts.group(1) if counts else ""), errors


# ------------------------------------------------------------- action queue
# Civilian words only — `fix` renders verbatim in the needs-you queue (voice
# gate in checks.py's docstring): what a household member DOES, no commands,
# no config keys. `how` is the operator route (an agent or the operator page
# may show it); it is never the fix. Checks absent here fall back to the
# finding's own first sentence, which the voice gate keeps civilian too.
FIX_BY_CHECK = {
    "review-queue": {
        "fix": "Teach Sara the rule — Activity room, or just tell her — and "
               "she'll re-file the history too.",
        "how": "tools/run query.py uncategorized → [[payee_rules]] in rules.toml "
               "→ tools/run recategorize.py --write",
    },
    "projected-shortfall": {
        "fix": "Move money into that account before the crunch date, or push the "
               "payment past it — ask Sara which is cheaper.",
        "how": "tools/run forecast.py",
    },
    "lanes": {
        "fix": "A standing transfer didn't run. Log in and check it — or if you "
               "changed the plan, tell Sara so she stops watching for the old one.",
        "how": "check the institution's standing order; fix rules.toml [[lanes]]",
    },
    "fixed-balance": {
        "fix": "This account holds a set level on purpose — move the difference "
               "back this week; Sara knows where it goes.",
        "how": "sweep/top up to the rules.toml [fixed_balances] amount",
    },
    "subscriptions": {
        "fix": "Unwanted? Cancel it. Wanted? Downgrade, go annual, or make the "
               "retention call.",
    },
    "coverage": {
        "fix": "Download a fresh statement from the bank and drop it in — Sara "
               "files it.",
        "how": "pull a fresh export from the institution; tools/run importers/…",
    },
    "anomaly": {
        "fix": "Verify the charge; dispute or get the refund if it's not right.",
    },
    "concentration": {
        "fix": "Too many eggs in one basket. If you can sell, trim now; if it's "
               "locked up, your plan already says when.",
        "how": "apply the THESIS.md selling rule / diversification call",
    },
    "reconciliation": {
        "fix": "Pull a fresh statement so the books match the bank again.",
        "how": "re-import the account or correct the balance assertion",
    },
}

# Priority lanes for the queue: money that is WRONG outranks everything;
# feed health ranks last (the Connections room is its real home). The
# middle band is bookkeeping that blocks a review. Dated deadlines join in
# needs_you (builders.py), between money-wrong and the rest.
QUEUE_MAX = 3
_LANE_MONEY = {"lanes", "fixed-balance", "transfers-drift", "catch-all-lump",
               "projected-shortfall", "anomaly", "subscriptions", "cash-drag",
               "concentration"}
_LANE_FEED = {"coverage", "reconciliation", "plaid_freshness"}
LINK_BY_CHECK = {"review-queue": "activity", "coverage": "connections"}


def queue_lane(check: str) -> int:
    """0 = money is wrong (leads), 1 = bookkeeping, 2 = feed health (last)."""
    if check in _LANE_MONEY:
        return 0
    return 2 if check in _LANE_FEED else 1


FIX_FALLBACK_CHARS = 140  # room for one whole civilian sentence with dollars in it


def fix_line(f: Finding) -> str:
    entry = FIX_BY_CHECK.get(f["check"])
    if entry:
        return entry["fix"]
    first = re.split(r"(?<=[.!?]) ", f["detail"].strip(), maxsplit=1)[0]
    if len(first) > FIX_FALLBACK_CHARS:  # cut on a word, never mid-dollar
        first = first[:FIX_FALLBACK_CHARS].rsplit(" ", 1)[0].rstrip(",;—- ") + "…"
    return first


def action_queue(findings: list[Finding] | None) -> list[Finding]:
    """The needs-you queue: at most QUEUE_MAX rows. Open alerts + watches
    (deadlines excluded — they become dated cards in needs_you), dismissed
    and decided findings filtered at the shared chokepoint. Same-check
    findings merge into ONE counted row (worst severity leads); rows rank by
    lane (money-wrong first, feed health last), alerts before watches inside
    a lane. Overflow drops — badges and rooms carry the rest. Each row:
    the finding + fix (civilian sentence), count, and optionally how (the
    operator route) and link (the room that owns the remedy)."""
    if not findings:
        return []
    rows = [f for f in filter_findings(findings)
            if f["severity"] in ("alert", "watch")
            and f["check"] != "deadlines"]
    groups: dict[str, list[Finding]] = {}
    for f in rows:
        groups.setdefault(f["check"], []).append(f)
    out: list[Finding] = []
    for check, group in groups.items():
        group.sort(key=lambda f: 0 if f["severity"] == "alert" else 1)
        row = {**group[0], "fix": fix_line(group[0]), "count": len(group)}
        entry = FIX_BY_CHECK.get(check, {})
        if entry.get("how"):
            row["how"] = entry["how"]
        if check in LINK_BY_CHECK:
            row["link"] = LINK_BY_CHECK[check]
        out.append(row)
    out.sort(key=lambda r: (queue_lane(r["check"]),
                            0 if r["severity"] == "alert" else 1))
    return out[:QUEUE_MAX]


def deadline_items(today: date, horizon_days: int) -> list[Payload]:
    """Dated `- YYYY-MM-DD — text` bullets across facts/ falling inside the
    horizon, freshest read (not via findings.md), with days-remaining."""
    out: list[Payload] = []
    for d, text, relpath in dated_bullets():
        days = (d - today).days
        if 0 <= days <= horizon_days:
            out.append({"date": d, "days": days, "text": text, "src": str(relpath)})
    return out


def milestone_state(liquid: Money) -> Payload | None:
    """None unless facts/goals configures milestone_net_worth_above; else
    {targets, crossed, next, pct} for a progress meter."""
    g = goals_config()
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(g.get("milestone_net_worth_above") or ""))
    targets = sorted(float(x) for x in nums)
    if not targets:
        return None
    crossed_raw = re.findall(r"-?\d+(?:\.\d+)?",
                             str(g.get("milestone_net_worth_above_crossed") or ""))
    crossed = sorted(float(x) for x in crossed_raw)
    nxt = next((t for t in targets if liquid < t), None)
    pct = min(100.0, 100.0 * liquid / nxt) if nxt else 100.0
    return {"targets": targets, "crossed": crossed, "next": nxt, "pct": pct}


# ------------------------------------------------------------------ marks
def code_spans(escaped: str) -> str:
    """`x` -> <code>x</code>, applied strictly AFTER escaping."""
    return re.sub(r"`([^`]{1,80})`", r"<code>\1</code>", escaped)
