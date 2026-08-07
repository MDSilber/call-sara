# pyright: strict
#!/usr/bin/env python3
"""Cash-flow forecast — the next N days of KNOWN flows, per account and household.

Usage:  tools/run forecast.py [days]          (default 60)

EVERY dollar and date this prints is a PROJECTION (the ~ prefix on each flow
is the reminder): recurring streams inferred from cadence patterns in the
ledger, plus dated facts/ bullets that carry a dollar amount. It is a floor
on known flows, not a spending simulator:

  projects:      payroll and other cadence-locked income, rent/housing,
                 utilities, subscriptions, card autopays, standing transfers,
                 dated one-offs with a $ amount (estimated taxes, tuition)
  does NOT:      irregular spend (groceries, dining, shopping), anything seen
                 fewer than 3 times (a just-set-up autobuy is invisible until
                 it has history), income that isn't on a locked rhythm
  exception:     a card whose rules.toml [[accounts]] entry declares bill_day
                 projects its autopay DAY-EXACTLY even with zero payment
                 history (see declared_autopay_streams)
  balances:      $1-NAV money-market positions count as cash in an account's
                 starting balance (_money_market_cash) — settlement sweeps
                 must not read as a crunch

The cadence machinery generalizes checks.py's subscription detector to ALL
regular streams — income + expense + transfer — on cash (USD) accounts.
Read-only: never writes to the vault.
"""
from __future__ import annotations

import calendar
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta

from sara.vault import (amount, dated_bullets, illiquid_currency_regex,
                   money, query, rules)
from sara.advisor.checks import (FIXED_DRIFT_TOLERANCE, _closed_accounts,
                    _cycle_anchors, _cycle_day, _matches_segments,
                    _normalize_merchant)

# ---------------------------------------------------------------- tuning
DEFAULT_DAYS = 60             # two rent+paycheck cycles: long enough to catch a rent/tax
                              # pileup, short enough that cadence drift hasn't compounded
STREAM_MIN_OCCURRENCES = 3    # same bar as the subscription radar: two hits is coincidence,
                              # three on a rhythm is a standing flow
STREAM_GAP_CONFORMANCE = 0.75  # >=75% of a stream's gaps must sit in ONE cadence window —
                              # tolerates a single skipped/late cycle, rejects irregular spend
INCOME_MAX_OFFCYCLE_GAPS = 1  # income projects only when cadence-LOCKED: at most ONE gap
                              # outside the window across the whole history (a holiday-shifted
                              # payday). A ratio would let a 44-paycheck history carry 4 skips;
                              # bonuses/reimbursements never lock like this — don't bank on them
INCOME_CADENCES = {"weekly", "biweekly", "semimonthly", "monthly"}  # payroll rhythms; quarterly
                              # /yearly income (dividends, bonuses) varies too much to project
CADENCE_WINDOWS = [           # (name, lo, hi) in days between occurrences; windows are wide
    ("weekly", 5, 9),         #   because posting dates wobble around weekends/holidays
    ("biweekly", 12, 17),     #   12-17 also covers semi-monthly (1st/15th gaps run 13-16)
    ("monthly", 25, 35),      #   same window the subscription radar uses
    ("quarterly", 80, 100),   #   true quarterly billing; note IRS estimated-tax dates are
                              #   UNEVEN (61-122d gaps) and won't match — facts bullets carry those
    ("yearly", 350, 380),     #   same window the subscription radar uses
]
CADENCE_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12,  # month-stepped cadences project
                  "declared": 1}  # on the anchor day-of-month (rent posts the 1st, not
                              # last+30d); "declared" = a rules.toml bill_day autopay,
                              # monthly by construction with an explicit anchor_day
ACTIVE_DAYS = {               # a stream must have fired this recently to still be "live":
    "weekly": 21,             #   3 missed weeks = the flow ended (or the feed died —
    "biweekly": 35,           #   coverage() owns that conversation)
    "semimonthly": 35,        #   ~2 missed periods
    "monthly": 60,            #   2 cycles — matches the subscription radar
    "quarterly": 130,         #   ~1.4 cycles
    "yearly": 400,            #   renewal up to ~3 weeks overdue — matches the radar
}
AMOUNT_LOOKBACK = 6           # variable streams project the median of their last 6 hits —
                              # recent enough to track a price change, wide enough to smooth
MIN_STREAM_AMOUNT = 5         # ignore sub-$5 streams ($1 cloud storage): cosmetic, not cash flow
SEMIMONTHLY_MIN_HITS = 4      # need 2 full months of paired paydays before trusting the
                              # two-days-of-month pattern over plain 15-day stepping
DECLARED_SPEND_CYCLES = 3     # a declared-bill_day card with no qualifying autopay stream
                              # projects the MEDIAN spend of its last 3 closed statement
                              # cycles — enough to smooth one fat cycle without reaching
                              # into history so old the card's habits have changed

# One-off bullets: a dollar amount alone isn't enough — plenty of dated notes
# mention amounts that never touch a bank account (an RSU grant, a brokerage
# rebalance). A bullet joins the MATH only when its wording also says which way
# cash moves: ONEOFF_INFLOW -> +, ONEOFF_MONEYISH (a payment shape) -> −.
# Anything else with an amount is listed as ambiguous and excluded.
ONEOFF_AMOUNT = re.compile(r"[~≈]?\$\s?(\d[\d,]*(?:\.\d{1,2})?)\s*([kKmM])?\b")
ONEOFF_INFLOW = re.compile(r"refund|reimburs|payout|proceeds|bonus|deposit|arrives",
                           re.I)
# Payment-shaped wording; also the filter for amount-less bullets worth listing
# as unquantified, so a "$?" tax deadline isn't silently dropped.
ONEOFF_MONEYISH = re.compile(r"\btax(?!-)|payment|\bpay\b|premium|tuition|invoice|bill\b"
                             r"|\bdue\b|\bowe|\bwire\b|\brent\b", re.I)
# Chore bullets ("verify the paystub shows ≈ $675/period") carry amounts that are
# facts to check, not flows to project — a leading to-do verb disqualifies the bullet.
ONEOFF_CHORE = re.compile(r"^(verify|check|confirm|review|nudge|remind|ask|email|call|file"
                          r"|follow.?up)\b", re.I)


def _merchant(label):
    """The subscription radar's normalizer, plus: strip SHORT digit runs (2-4).

    Payroll payees carry a rotating run number ("ACME-OSV PAYROLL240" →
    "...PAYROLL515") that survives _normalize_merchant's 5+-digit rule, which
    would make every paycheck its own merchant and no stream would ever form.
    No left \b — the run is usually GLUED to the word. Short digit runs are
    refs, not identity; single digits stay ("7 eleven").
    """
    return " ".join(re.sub(r"\d{2,4}\b", " ", _normalize_merchant(label)).split())


# ---------------------------------------------------------------- ledger
def _cash_postings():
    """Every USD posting on an Assets/Liabilities account, classified.

    kind is the transaction's shape, joined via the query id: any Income leg
    -> "income", else any Expenses leg -> "expense", else "transfer" (both
    legs are cash accounts, or the counterleg is a non-USD position — a
    401k/529/brokerage buy looks like a transfer out of the cash universe,
    which is exactly how a forecast should treat it).
    """
    kind = {}
    for r in query("SELECT id, account WHERE account ~ '^(Income|Expenses)'"):
        k = "income" if (r["account"] or "").startswith("Income") else "expense"
        if kind.get(r["id"]) != "income":  # income outranks expense (gross payroll
            kind[r["id"]] = k              # carries Expenses:Taxes legs too)
    posts = []
    for r in query("SELECT id, date, payee, narration, account, number "
                   "WHERE account ~ '^(Assets|Liabilities)' AND currency = 'USD'"):
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        label = (r["payee"] or "").strip() or (r["narration"] or "").strip()
        # pad-generated postings count toward the balance but must never look
        # like a recurring merchant — blank merchant keeps them out of streams
        pad = label.lower().startswith("(padding")
        posts.append({
            "account": r["account"], "date": d, "label": label,
            "merchant": "" if pad else _merchant(label),
            "amt": round(amount(r["number"]), 2),
            "kind": kind.get(r["id"], "transfer")})
    return posts


def _cadence_of(dates):
    """(cadence name, step days) for sorted distinct dates, or (None, None).

    A stream conforms when >= STREAM_GAP_CONFORMANCE of its gaps sit inside
    one window; the step is the median of the IN-window gaps (out-of-window
    gaps are skipped cycles, not rhythm). Biweekly-window streams whose hits
    all land on <= 2 days-of-month upgrade to "semimonthly" (1st/15th payroll
    projects on those days instead of drifting by +15d steps).
    """
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    if not gaps:
        return None, None
    for name, lo, hi in CADENCE_WINDOWS:
        inside = [g for g in gaps if lo <= g <= hi]
        if len(inside) >= STREAM_GAP_CONFORMANCE * len(gaps):
            if (name == "biweekly" and len(dates) >= SEMIMONTHLY_MIN_HITS
                    and len({d.day for d in dates}) <= 2):
                return "semimonthly", None
            inside.sort()
            return name, inside[len(inside) // 2]
    return None, None


def _offcycle_gaps(dates, lo, hi):
    """How many gaps in the history fall OUTSIDE the cadence window."""
    gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
    return sum(1 for g in gaps if not lo <= g <= hi)


def _stream(account, merchant, occurrences, today):
    """Qualify one candidate stream. occurrences: {date: (amt, label, kind)}.

    Returns the stream dict or None. Conservative by construction: minimum
    occurrences, gap conformance, a live-recently test, and the tighter
    income lock all have to pass.
    """
    dates = sorted(occurrences)
    if len(dates) < STREAM_MIN_OCCURRENCES:
        return None
    cadence, step = _cadence_of(dates)
    if not cadence:
        return None
    if (date.today() if today is None else today) - dates[-1] > timedelta(
            days=ACTIVE_DAYS[cadence]):
        return None  # went quiet — ended, paused, or a stale feed; don't project
    amts = [occurrences[d][0] for d in dates]
    kinds = Counter(occurrences[d][2] for d in dates)
    kind = kinds.most_common(1)[0][0]
    if kind == "income":
        window = next((lo, hi) for n, lo, hi in CADENCE_WINDOWS
                      if n == (cadence if cadence != "semimonthly" else "biweekly"))
        if (cadence not in INCOME_CADENCES
                or _offcycle_gaps(dates, *window) > INCOME_MAX_OFFCYCLE_GAPS):
            return None
    recent = amts[-AMOUNT_LOOKBACK:]
    proj = sorted(recent)[len(recent) // 2]  # median of recent hits; exact streams
    if abs(proj) < MIN_STREAM_AMOUNT:        # (rent, subs) have identical amts anyway
        return None
    return {"account": account, "merchant": merchant, "label": occurrences[dates[-1]][1],
            "kind": kind, "cadence": cadence, "step": step, "amount": proj,
            "dates": dates, "last": dates[-1], "n": len(dates)}


def recurring_streams(posts, today=None):
    """Generalized recurring streams over all cash postings, two passes.

    Pass 1 keys on (account, merchant, exact amount) — the subscription
    radar's trick, which cleanly separates a $3,500 standing transfer from
    the variable interest paid by the same bank. Pass 2 re-groups the
    postings pass 1 didn't claim by (account, merchant, sign) and projects
    the median — that's what catches utilities, card autopays, and payroll
    whose net wobbles. Both passes face the same qualification gates.
    """
    skip = _closed_accounts()
    by_exact, by_key = {}, {}
    for p in posts:
        if not p["merchant"] or p["account"] in skip:
            continue
        if _matches_segments(p["account"], ("transfer",)):
            continue  # the own-transfer clearing account nets to ~0 by design
        by_exact.setdefault((p["account"], p["merchant"], p["amt"]), {})[p["date"]] = (
            p["amt"], p["label"], p["kind"])
    streams, claimed = [], set()
    for (account, merchant, amt), occ in by_exact.items():
        s = _stream(account, merchant, occ, today)
        if s:
            streams.append(s)
            claimed.add((account, merchant, amt))
    for p in posts:
        key = (p["account"], p["merchant"], p["amt"] >= 0)
        if (not p["merchant"] or p["account"] in skip
                or _matches_segments(p["account"], ("transfer",))
                or (p["account"], p["merchant"], p["amt"]) in claimed):
            continue
        day = by_key.setdefault(key, {}).setdefault(p["date"], [0.0, p["label"], p["kind"]])
        day[0] += p["amt"]  # same-day swipes at one merchant hit cash together
    for (account, merchant, _sign), occ in by_key.items():
        s = _stream(account, merchant, {d: tuple(v) for d, v in occ.items()}, today)
        if s:
            streams.append(s)
    return streams


# ------------------------------------------------- declared card cycles
def _declared_cards():
    """rules.toml [[accounts]] entries declaring a card cycle (OPTIONAL keys
    bill_day / statement_close / autopay_from on Liabilities accounts) ->
    {ledger_account: {"bill", "close", "from", "label"}}. bill_day is the
    trigger — statement_close alone belongs to checks.coverage(). Malformed
    day values parse to None and drop the entry: a typo'd hint must never
    crash a read-only tool."""
    out = {}
    for a in rules().get("accounts", []):
        acct = a.get("ledger_account") or ""
        bill = _cycle_day(a.get("bill_day"))
        if not acct.startswith("Liabilities") or bill is None:
            continue
        out[acct] = {"bill": bill,
                     "close": _cycle_day(a.get("statement_close")) or bill,
                     "from": a.get("autopay_from") or None,
                     "label": a.get("institution") or acct.split(":")[-1]}
    return out


def _cycle_spend(card_posts, close_day, today):
    """~estimated autopay amount for a card with no autopay stream yet: the
    median NET non-transfer spend over its last DECLARED_SPEND_CYCLES CLOSED
    statement cycles (anchored on close_day; a cycle is closed only when data
    reaches past its close date, so a lagging feed can't undercount). A
    first-cycle card (nothing closed yet) uses its spend so far — that's what
    lets a brand-new declared card project its first autopay. None when the
    card has spent nothing worth paying."""
    charges = [p for p in card_posts if p["kind"] != "transfer"]
    if not charges:
        return None
    first = min(p["date"] for p in charges)
    newest = max(p["date"] for p in card_posts)
    # anchors from one cycle before the first charge through the newest data
    # (62d back guarantees an anchor precedes `first`, so windows cover it)
    anchors = _cycle_anchors(close_day, first - timedelta(days=62), max(newest, today))
    closed = [a for a in anchors if a <= newest]
    spends = []
    for lo, hi in zip(closed, closed[1:]):
        if hi < first:
            continue  # cycle closed before the card existed — absence, not history
        spends.append(-sum(p["amt"] for p in charges if lo < p["date"] <= hi))
    spends = spends[-DECLARED_SPEND_CYCLES:]
    if not spends:  # first statement hasn't closed — the balance building now
        spends = [-sum(p["amt"] for p in charges if p["date"] > closed[-1])]
    est = sorted(spends)[len(spends) // 2]
    return est if est >= MIN_STREAM_AMOUNT else None


def declared_autopay_streams(streams, posts, asof, today):
    """Declared card-cycle metadata as a HINT layer over inference: each
    [[accounts]] card with a bill_day takes over that ONE card's autopay
    projection; every other stream stays purely inferred.

      amount — from the inferred autopay (the largest transfer-in stream on
               the card) when one exists: the DECLARED day replaces the
               inferred dates, nothing else changes. Else from the trailing
               statement-cycle spend (_cycle_spend), labeled ~est — so a
               brand-new card with zero autopay history still projects.
      dates  — bill_day, month-stepped, via an explicit anchor_day that
               _future_dates prefers over the last-hit day-of-month.
      funding — when autopay_from is declared, the same flow projects as an
               outflow on the funding account, and the inferred funding-side
               twin (same merchant, opposite sign — the two legs of one
               transaction share their payee) retires so the payment is
               never projected twice. Inference qualifies the two sides
               symmetrically (same dates and |amounts|), so a matched-here-
               but-not-there mismatch can't double count.

    Cards absent from the ledger contribute nothing — there is no balance to
    project from; the forecast stays a projection OF the ledger.
    """
    cards = _declared_cards()
    if not cards:
        return streams
    skip = _closed_accounts()
    out = list(streams)
    for card, c in sorted(cards.items()):
        if card in skip or card not in asof:
            continue
        # newest REAL payment into the card (any transfer-in posting): the
        # declared day in that month counts as satisfied, so a payment that
        # posted early (the 20th against a declared 25th) never re-projects
        last_paid = max((p["date"] for p in posts if p["account"] == card
                         and p["kind"] == "transfer" and p["amt"] > 0),
                        default=None)
        inferred = max((s for s in out if s["account"] == card
                        and s["kind"] == "transfer" and s["amount"] > 0),
                      key=lambda s: s["amount"], default=None)
        if inferred is not None:
            amt, label = inferred["amount"], inferred["label"]
            out.remove(inferred)  # declared date WINS over inferred date
        else:
            amt = _cycle_spend([p for p in posts if p["account"] == card],
                               c["close"], today)
            if amt is None:
                continue  # nothing spent, nothing due — no autopay to project
            label = f"{c['label']} autopay ~est(cycle spend)"
        sides = [(card, amt)]
        if c["from"] and c["from"] in asof:
            if inferred is not None:
                twin = next((s for s in out if s["account"] == c["from"]
                             and s["kind"] == "transfer" and s["amount"] < 0
                             and s["merchant"] == inferred["merchant"]), None)
                if twin is not None:
                    out.remove(twin)
            sides.append((c["from"], -amt))
        for account, amount_ in sides:
            # seed `last` at the newest SATISFIED bill day: the latest declared
            # day the account's ledger already covers (absence inside covered
            # history is data, not lag — same floor semantics as every stream),
            # pushed forward to the last real payment's month when that payment
            # posted before its declared day. Projection starts with the NEXT
            # one; a lagging feed's due-but-unseen autopay still shows as †.
            # (The funding side reuses the card's payment date — the two legs
            # share a transaction, so its month is the same.)
            anchor = _cycle_anchors(c["bill"], asof[account] - timedelta(days=62),
                                    asof[account])[-1]
            if last_paid is not None:
                anchor = max(anchor, _add_months(last_paid, 0, c["bill"]))
            out.append({"account": account, "merchant": "", "label": label,
                        "kind": "transfer", "cadence": "declared", "step": None,
                        "amount": amount_, "dates": [anchor], "last": anchor,
                        "n": 0, "anchor_day": c["bill"]})
    return out


# ------------------------------------------------- money-market cash
MMF_PRICE_BAND = (0.98, 1.02)  # a $1-NAV fund's latest price sits inside this band;
                               # real stock/bond funds never hold that peg


def _money_market_cash():
    """{account: usd} of $1-NAV money-market positions (VMFXX/VUSXX-style) —
    settlement cash wearing a fund ticker. Counted into starting balances:
    a sweep account holding seven figures of MMF is not about to crunch just
    because its RESIDUAL USD can't cover a declared auto-invest. A commodity
    qualifies only when its latest dated price is within MMF_PRICE_BAND of
    $1.00; unpriced or illiquid holdings never do."""
    from sara.advisor.webview import price_history  # deferred: forecast stays standalone-fast
    prices = price_history()
    excl = illiquid_currency_regex()
    lo, hi = MMF_PRICE_BAND
    out = {}
    for r in query("SELECT account, currency, sum(number) AS u "
                   "WHERE account ~ '^Assets' AND currency != 'USD' "
                   "GROUP BY account, currency"):
        sym = r["currency"] or ""
        if excl and re.match(excl, sym):
            continue
        marks = prices.get(sym)
        if not marks or not lo <= marks[-1][1] <= hi:
            continue
        units = amount(r["u"])
        if units > 0:
            out[r["account"]] = out.get(r["account"], 0.0) + units * marks[-1][1]
    return out


# ------------------------------------------------------------- projection
def _add_months(d, months, anchor_day):
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(anchor_day, calendar.monthrange(y, m)[1]))


def _future_dates(stream, floor, end):
    """Projected occurrence dates in (floor, end].

    floor is the account's newest ledger date, NOT today: when the feed lags,
    a flow due between last-import and today still belongs in the projection
    (the starting balance predates it). Month-stepped cadences anchor on the
    last hit's day-of-month; semimonthly projects both observed days-of-month;
    weekly/biweekly step by the stream's own median gap.
    """
    out, last = [], stream["last"]
    if stream["cadence"] == "semimonthly":
        days = sorted({d.day for d in stream["dates"]})
        d = last.replace(day=1)
        for k in range((end - last).days // 28 + 2):  # every month the horizon can touch
            for day in days:
                nxt = _add_months(d, k, day)
                if last < nxt and floor < nxt <= end:
                    out.append(nxt)
        return out
    months = CADENCE_MONTHS.get(stream["cadence"])
    anchor = stream.get("anchor_day") or last.day  # declared card cycles carry their own
    #        anchor ("last" -> 31); _add_months clamps it to short months
    for k in range(1, (end - last).days + 2):  # generous bound; the break below governs
        nxt = (_add_months(last, months * k, anchor) if months
               else last + timedelta(days=stream["step"] * k))
        if nxt > end:
            break
        if nxt > floor:
            out.append(nxt)
    return out


def known_oneoffs(today, end):
    """Dated facts/ bullets inside the horizon, three buckets.

    quantified: amount + clear cash direction — in the math. ambiguous: amount
    but no payment/receipt wording (an RSU note, a rebalance) — listed,
    excluded. unquantified: payment-shaped but no amount — listed, excluded.
    Household-level only — a bullet doesn't say which account pays, so
    per-account minimums exclude one-offs (stated in the output).
    """
    quantified, ambiguous, unquantified, seen = [], [], [], set()
    for d, text, relpath in dated_bullets():
        if not (today <= d <= end):
            continue
        key = (d, " ".join(text.lower().split())[:80])
        if key in seen:  # the same event often lives in two facts files
            continue
        seen.add(key)
        if ONEOFF_CHORE.match(text):
            continue
        m = ONEOFF_AMOUNT.search(text)
        entry = {"date": d, "text": text, "source": str(relpath)}
        if m:
            v = float(m.group(1).replace(",", ""))
            v *= {"k": 1e3, "m": 1e6}.get((m.group(2) or "").lower(), 1)
            if ONEOFF_INFLOW.search(text):
                quantified.append({**entry, "amount": v})
            elif ONEOFF_MONEYISH.search(text):
                quantified.append({**entry, "amount": -v})
            else:
                ambiguous.append({**entry, "amount": v})
        elif ONEOFF_MONEYISH.search(text):
            unquantified.append(entry)
    return quantified, ambiguous, unquantified


def build_forecast(days=DEFAULT_DAYS, today=None):
    """The whole projection as data; main() renders it, checks.py reads it.

    Returns {"today", "end", "accounts": [...], "household": {...}}. Each
    account: start balance (from the ledger, as of its newest posting), flows
    sorted by date, projected minimum + date, floor. Household: flow totals
    by kind, one-offs, uncommitted surplus, warns.
    """
    today = today or date.today()
    end = today + timedelta(days=days)
    posts = _cash_postings()
    balances, asof = {}, {}
    for p in posts:
        balances[p["account"]] = balances.get(p["account"], 0.0) + p["amt"]
        asof[p["account"]] = max(asof.get(p["account"], date.min), p["date"])
    mmf = _money_market_cash()  # $1-NAV funds spend like cash — count them
    for acct, extra in mmf.items():
        if acct in balances:  # a pure-MMF account has no cash flows to project
            balances[acct] += extra
    streams = recurring_streams(posts, today)
    streams = declared_autopay_streams(streams, posts, asof, today)
    floors = {a: float(t) for a, t in rules().get("fixed_balances", {}).items()}
    flows_by_account = {}
    for s in streams:
        for d in _future_dates(s, asof[s["account"]], end):
            flows_by_account.setdefault(s["account"], []).append({
                "date": d, "amount": s["amount"], "label": s["label"],
                "kind": s["kind"], "cadence": s["cadence"], "overdue": d < today})
    accounts = []
    for acct in sorted(set(flows_by_account) | (set(floors) & set(balances))):
        flows = flows_by_account.get(acct, [])
        # same-day outflows apply before inflows — the conservative intraday minimum
        flows.sort(key=lambda f: (f["date"], f["amount"]))
        bal = start = round(balances.get(acct, 0.0), 2)
        lo, lo_date = start, min(asof.get(acct, today), today)
        for f in flows:
            bal = round(bal + f["amount"], 2)
            f["running"] = bal
            if bal < lo:
                lo, lo_date = bal, f["date"]
        accounts.append({"account": acct, "start": start, "asof": asof.get(acct),
                         "flows": flows, "min": lo, "min_date": lo_date,
                         "end_balance": bal, "floor": floors.get(acct),
                         "mmf": round(mmf.get(acct, 0.0), 2)})
    oneoffs, ambiguous, unquantified = known_oneoffs(today, end)
    totals = {"income": 0.0, "expense": 0.0, "transfer": 0.0}
    for a in accounts:
        for f in a["flows"]:
            totals[f["kind"]] += f["amount"]
    oneoff_total = sum(o["amount"] for o in oneoffs)
    warns = []
    for a in accounts:
        drivers = sorted((f for f in a["flows"]
                          if f["amount"] < 0 and f["date"] <= a["min_date"]),
                         key=lambda f: f["amount"])[:3]
        named = ", ".join(f"{f['label']} ~{money(f['amount'])} ({f['date']})"
                          for f in drivers) or "no projected outflows (low start)"
        # only projections that CROSS a line warn; already-under-now belongs to
        # the fixed-balance / reconciliation checks, not the forecast
        if a["account"].startswith("Assets") and a["min"] < 0 <= a["start"]:
            warns.append({"account": a["account"], "kind": "below_zero",
                          "min": a["min"], "date": a["min_date"], "floor": None,
                          "drivers": named})
        elif (a["floor"] is not None
              and a["min"] < a["floor"] - FIXED_DRIFT_TOLERANCE <= a["start"]):
            warns.append({"account": a["account"], "kind": "below_floor",
                          "min": a["min"], "date": a["min_date"], "floor": a["floor"],
                          "drivers": named})
    household = {"start": sum(a["start"] for a in accounts),
                 "income": totals["income"], "expense": totals["expense"],
                 "transfer_net": totals["transfer"], "oneoffs": oneoffs,
                 "oneoff_total": oneoff_total, "ambiguous": ambiguous,
                 "unquantified": unquantified,
                 "surplus": sum(totals.values()) + oneoff_total, "warns": warns}
    return {"today": today, "end": end, "accounts": accounts, "household": household}


# -------------------------------------------------------------------- CLI
def main(argv):
    try:
        days = int(argv[0]) if argv else DEFAULT_DAYS
    except ValueError:
        sys.exit(__doc__)
    fc = build_forecast(days=days)
    h = fc["household"]
    print(f"Cash-flow forecast — next {days} days ({fc['today']} → {fc['end']})")
    print("ALL figures below are PROJECTIONS (~) from cadence patterns + dated facts —")
    print("estimates to plan around, not statements. Irregular spend is NOT projected.\n")
    if not fc["accounts"]:
        print("No regular streams detected yet — a stream needs "
              f"{STREAM_MIN_OCCURRENCES}+ occurrences on a steady rhythm. "
              "Import more history and re-run.")
        return
    overdue_seen = False
    for a in fc["accounts"]:
        asof = f" (ledger through {a['asof']})" if a["asof"] else ""
        mmf_note = f", incl. {money(a['mmf'])} money-market" if a["mmf"] else ""
        print(f"{a['account']}   start {money(a['start'])}{mmf_note}{asof}")
        for f in a["flows"]:
            mark = "†" if f["overdue"] else " "
            overdue_seen = overdue_seen or f["overdue"]
            print(f"  {f['date']}{mark} {'~' + money(f['amount']):>11}  "
                  f"{f['label'][:34]:<34} {f['kind'] + '/' + f['cadence']:<19} "
                  f"→ {money(f['running'])}")
        if not a["flows"]:
            print("  (no regular flows detected — balance held flat)")
        at = ("at start — no projected dip" if a["min"] >= a["start"]
              else f"on {a['min_date']}")
        print(f"  projected minimum ~{money(a['min'])} {at}"
              + (f"   [floor {money(a['floor'])}]" if a["floor"] is not None else "")
              + f" · ends ~{money(a['end_balance'])}\n")
    if overdue_seen:
        print("† projected before today — the ledger's feed lags; expect it on the next import.\n")
    print(f"Household ({len(fc['accounts'])} forecasted accounts, start {money(h['start'])})")
    print(f"  projected income      ~{money(h['income'])}")
    print(f"  projected expenses    ~{money(h['expense'])}")
    print(f"  transfers, net        ~{money(h['transfer_net'])}   "
          "(moves between forecasted accounts cancel; this is what leaves them, "
          "e.g. into investments)")
    if h["oneoffs"]:
        print(f"  one-offs from facts   ~{money(h['oneoff_total'])}   "
              "(household-level — per-account minimums above EXCLUDE these)")
        for o in h["oneoffs"]:
            print(f"    {o['date']}  {'~' + money(o['amount']):>11}  {o['text'][:64]}  "
                  f"[{o['source']}]")
    print(f"  = uncommitted surplus ~{money(h['surplus'])} over {days} days")
    print("  minimums: " + " · ".join(
        f"{a['account'].split(':')[-1]} {money(a['min'])} "
        f"({a['min_date'] if a['min'] < a['start'] else 'start'})"
        for a in fc["accounts"]))
    for w in h["warns"]:
        line = (f"below $0 on {w['date']} (~{money(w['min'])})" if w["kind"] == "below_zero"
                else f"below its {money(w['floor'])} floor on {w['date']} (~{money(w['min'])})")
        print(f"  WARN: {w['account']} projected {line} — driven by {w['drivers']}")
    if h["ambiguous"]:
        print("  dated amounts that don't read as cash payments — NOT in the math "
              "(reword the bullet if one is):")
        for u in h["ambiguous"]:
            print(f"    {u['date']}  ~{money(u['amount'])}?  {u['text'][:60]}  "
                  f"[{u['source']}]")
    if h["unquantified"]:
        print("  dated but unquantified — NOT in the math (add a $ amount to the "
              "bullet to project it):")
        for u in h["unquantified"]:
            print(f"    {u['date']}  {u['text'][:70]}  [{u['source']}]")


if __name__ == "__main__":
    main(sys.argv[1:])
