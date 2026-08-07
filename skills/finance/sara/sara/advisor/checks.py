#!/usr/bin/env python3
"""The planner's checks. Deterministic; each returns a list of findings.

A finding: {"check", "severity", "title", "detail"} — no side effects here.
severity: "alert" (act soon) | "watch" (note) | "info"
Run via run_checks.py, which writes reports/findings.md.
Nothing household-specific lives here: thresholds come from facts/goals,
tuning from rules.toml, dates from facts/, transactions from the ledger.
One deliberate exception to "no side effects": milestones() records crossed
milestones back into facts/goals/index.md so each fires exactly once.

THE VOICE GATE — every user-visible string (titles, details, queue fixes):
1. Sara is talking. Verdict first, why second; never brochure, never scold.
2. A remedy names WHO does WHAT by WHEN, with real dollars when the data has them.
3. No paths, commands, filenames, or config keys — "tell Sara" is how a human
   reaches them; the check name routes the work, Sara knows her own tools.
4. Plain words for mechanisms ("checked against a statement", "standing order")
   and human dates/money (Sep 6, $1,240/yr) — never ISO dates or raw paths.
5. The FIRST sentence of a detail must stand alone as the queue line
   (webview.fix_line falls back to it). Terminal output is operator-land —
   commands live there, and only there.
"""
import calendar
import re
from collections import Counter
from datetime import date, datetime, timedelta

from sara.vault import (VAULT, amount, dated_bullets, illiquid_currency_regex,
                   money, query, rules, shadow_currency)


def goals():
    """Parse the ```yaml block out of facts/goals/index.md (no yaml dependency)."""
    path = VAULT / "facts" / "goals" / "index.md"
    if not path.exists():
        return {}
    m = re.search(r"```yaml\n(.*?)```", path.read_text(), re.S)
    if not m:
        return {}
    out = {}
    for line in m.group(1).splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v in ("null", ""):
            out[k.strip()] = None
        else:
            try:
                out[k.strip()] = float(v)
            except ValueError:
                out[k.strip()] = v
    return out


def finding(check, severity, title, detail):
    return {"check": check, "severity": severity, "title": title, "detail": detail}


def bql_str(s):
    """Escape a value for interpolation inside a single-quoted BQL string —
    beanquery understands SQL-style doubled quotes, so rules.toml-sourced
    text can never break out of the '...' literal."""
    return str(s).replace("'", "''")


# ------------------------------------------------------------- concentration
def _thesis_declared_symbols():
    """Symbols the written plan already covers: tickers named anywhere in
    THESIS.md (a mention means a policy exists — the selling rule, a lane,
    an explicit call) plus rules.toml [allocation_targets] employer_stock.
    A declared position auto-resolves in concentration(): the plan says when
    to act, so the check has nothing to add."""
    text = ""
    path = VAULT / "THESIS.md"
    if path.exists():
        try:
            text = path.read_text()
        except OSError:
            text = ""
    stock = rules().get("allocation_targets", {}).get("employer_stock", [])
    named = {str(s).strip().upper() for s in stock if str(s).strip()}
    return named, text


def concentration():
    """Flag a single holding above the ceiling, as % of total (liquid+paper).

    Only UNDECLARED LIQUID positions fire. Illiquid paper is the thesis's
    act-at-a-liquidity-event class by construction, and any ticker the
    thesis names already has a policy — both auto-resolve silently."""
    ceiling = goals().get("concentration_ceiling_pct") or 15
    excl = illiquid_currency_regex()
    rows = query("SELECT currency, sum(convert(position, 'USD')) AS v "
                 "WHERE account ~ '^Assets' AND currency != 'USD' GROUP BY currency")
    values = {}
    for r in rows:
        if excl and re.match(excl, r["currency"] or ""):
            continue  # illiquid ones are valued in the shadow currency below
        v = amount(r["v"])
        if r["v"] and "USD" in r["v"] and v > 0:
            values[r["currency"]] = v
    cash = query("SELECT sum(convert(position, 'USD')) AS v "
                 "WHERE account ~ '^(Assets|Liabilities)' AND currency = 'USD'")
    liquid_total = sum(values.values()) + (amount(cash[0]["v"]) if cash else 0.0)
    paper = {}
    if excl:
        prefixes = rules().get("household", {}).get("illiquid_commodity_prefixes", [])
        for r in query(f"SELECT currency, sum(convert(position, '{bql_str(shadow_currency())}')) AS v "
                       f"WHERE account ~ '^Assets' AND currency ~ '{bql_str(excl)}' GROUP BY currency"):
            v = amount(r["v"], shadow_currency())
            if v > 0:
                issuer = next((px for px in prefixes if r["currency"].startswith(px)), r["currency"])
                paper[issuer] = paper.get(issuer, 0.0) + v
    total = liquid_total + sum(paper.values())
    if total <= 0:
        return []
    named, thesis_text = _thesis_declared_symbols()
    out = []
    for sym, v in values.items():  # liquid only — paper auto-resolves per the thesis
        pct = 100.0 * v / total
        if pct <= ceiling:
            continue
        if sym.upper() in named or re.search(rf"\b{re.escape(sym)}\b", thesis_text):
            continue  # the written plan already covers this position
        out.append(finding(
            "concentration", "watch",
            f"{sym} is {pct:.0f}% of net worth (ceiling {ceiling:.0f}%)",
            f"~{money(v)} sits in one position you could sell, against ~{money(total)} "
            f"total. That's a live diversification decision — trim it, or if the "
            f"written plan already covers this position, tell Sara and she'll stop "
            f"flagging it. Watch, not alarm."))
    return out


# ---------------------------------------------------------------- deadlines
def deadlines():
    horizon = int(goals().get("deadline_horizon_days") or 45)
    today = date.today()
    out = []
    seen = set()  # the same event often lives in two facts files (calendar + a note)
    for d, text, relpath in dated_bullets():
        days = (d - today).days
        key = (d, " ".join(text.lower().split())[:80])
        if 0 <= days <= horizon and key not in seen:
            seen.add(key)
            out.append(finding(
                "deadlines", "watch",
                f"In {days} days ({d.isoformat()}): {text}",
                f"From your notes ({relpath}). Does it need doing, or just knowing?"))
    return out


# -------------------------------------------------------------------- inbox
def inbox():
    """The drop zone must drain: anything sitting in inbox/ is a document
    waiting to be identified and filed (tools/run inbox.py). A watch, so
    the needs-you queue and the phone surface both nag until it's empty."""
    box = VAULT / "inbox"
    if not box.is_dir():
        return []
    files = [p for p in sorted(box.iterdir())
             if p.is_file() and not p.name.startswith(".")]
    if not files:
        return []
    oldest = min(datetime.fromtimestamp(p.stat().st_mtime).date() for p in files)
    age = (date.today() - oldest).days
    n = len(files)
    names = ", ".join(p.name for p in files[:3]) + (" …" if n > 3 else "")
    waiting = ("dropped today" if age <= 0
               else f"oldest has waited {age} day{'s' if age != 1 else ''}")
    return [finding(
        "inbox", "watch",
        f"{n} document{'s' if n != 1 else ''} waiting in the inbox to be filed",
        f"Ask Sara to file them — statements file themselves, PDFs she reads "
        f"first. Waiting: {names} ({waiting}).")]


# ------------------------------------------------------------------ anomaly
# Category-spike tuning (the second half of anomaly()):
SPIKE_FACTOR = 2             # a month has to DOUBLE its own typical spend before it's a review
                             # gate — anything tighter fires on ordinary lumpiness
SPIKE_TRAILING_MONTHS = 6    # the baseline window: long enough to smooth, short enough to track
                             # a household whose life changed this year
SPIKE_MIN_ACTIVE_MONTHS = 3  # a category needs 3 spending months of history before "typical"
                             # means anything; younger categories are skipped, not flagged
SPIKE_TOP_N = 3              # review gates, not an audit — surface the 3 biggest and stop


def anomaly():
    """Review gates: charges at rarely-seen payees, and categories running hot.

    Two independent signals under one check: (a) large charges at payees the
    household rarely uses (fraud / surprise), (b) any category whose latest
    month more than doubled its trailing median (creep / miscategorization /
    a planned one-off worth confirming).
    """
    return _rare_payee_charges() + _category_spikes()


def _rare_payee_charges():
    """Large charges at payees you rarely use — the fraud / surprise signal."""
    floor_amt = goals().get("anomaly_min_amount") or 400
    tune = rules().get("anomaly", {})
    ignore_acct = tune.get("ignore_account_regex", "Cash|Uncategorized")
    ignore_payee = tune.get("ignore_payee_regex", r"^(ATM |WITHDRAWAL|CHECK )")
    rows = query("SELECT date, payee, number, account "
                 f"WHERE account ~ '^Expenses' AND NOT account ~ '{bql_str(ignore_acct)}' "
                 "AND currency = 'USD' AND number > 0 ORDER BY date")
    rows = [r for r in rows if not re.match(ignore_payee, r["payee"] or "", re.I)]
    if not rows:
        return []
    freq = Counter((r["payee"] or "").strip().upper() for r in rows)
    last = max(datetime.strptime(r["date"], "%Y-%m-%d").date() for r in rows)
    out = []
    for r in rows:
        d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        if (last - d).days > 45:
            continue
        n = amount(r["number"])
        payee = (r["payee"] or "").strip()
        if n >= floor_amt and freq[payee.upper()] <= 2 and "TRANSFER" not in payee.upper():
            out.append(finding(
                "anomaly", "alert",
                f"{money(n)} at rarely-seen payee — merchant text: `{payee[:48]}` ({r['date']})",
                f"Category {r['account'].replace('Expenses:', '')}. Seen "
                f"{freq[payee.upper()]}x ever. Confirm it's yours — then tell Sara "
                f"it's fine and she won't flag this merchant again."))
    return out[-8:]  # cap noise


def _category_spikes():
    """Any category whose latest data month is >2x its trailing 6-month median.

    "Latest data month", not the calendar month — a feed that ends July 27
    should judge July, and a fresh calendar month with three days of data
    would never fire anyway. Known-lumpy categories are excluded by the same
    rules.toml [anomaly] ignore_account_regex the rare-payee signal uses (the
    household already lists what shouldn't be surprise-flagged: insurance,
    taxes, ...), plus anything with under SPIKE_MIN_ACTIVE_MONTHS of history.
    The month's total must also clear goals' anomaly_min_amount, so a $30
    category tripling never pages anyone. Median is taken over the ACTIVE
    trailing months (zero months excluded) — sparse categories would
    otherwise median to ~0 and everything would look like a spike.
    Severity info: a spike is context for the month review, not a page.
    A spike explained by an annual renewal (a yearly-cadence subscription
    stream billing into that category this month) is suppressed outright.
    """
    floor_amt = goals().get("anomaly_min_amount") or 400
    lumpy = rules().get("anomaly", {}).get("ignore_account_regex", "Cash|Uncategorized")
    rows = query("SELECT year, month, root(account, 2) AS cat, "
                 "sum(convert(position,'USD')) AS v "
                 "WHERE account ~ '^Expenses' GROUP BY year, month, cat")
    by_cat, months = {}, set()
    for r in rows:
        ym = (int(r["year"]), int(r["month"]))
        v = amount(r["v"])
        months.add(ym)
        if v > 0:
            by_cat.setdefault(r["cat"], {})[ym] = v
    if not months:
        return []
    cur = max(months)
    trailing = []
    y, m = cur
    for _ in range(SPIKE_TRAILING_MONTHS):
        m -= 1
        if m < 1:
            y, m = y - 1, 12
        trailing.append((y, m))
    scored = []
    renewed = _annual_renewal_categories(cur)
    for cat, series in by_cat.items():
        if lumpy and re.search(lumpy, cat):
            continue
        if cat in renewed:
            continue  # an annual renewal billed this month — expected, not a spike
        hist = sorted(series[ym] for ym in trailing if series.get(ym, 0) > 0)
        if len(hist) < SPIKE_MIN_ACTIVE_MONTHS:
            continue
        med = hist[len(hist) // 2]  # upper median — conservative, fires less
        curv = series.get(cur, 0.0)
        if curv >= floor_amt and curv > SPIKE_FACTOR * med:
            scored.append((curv - med, finding(
                "anomaly", "info",
                f"{cat.replace('Expenses:', '')} ran {curv / med:.1f}x its usual month: "
                f"{money(curv)} vs ~{money(med)} median",
                f"{cur[0]}-{cur[1]:02d} vs the median of its active months in the trailing "
                f"{SPIKE_TRAILING_MONTHS}. Scan the line items — planned one-off, price creep, "
                f"or a miscategorization. If this category is just lumpy (annual premiums, "
                f"taxes), tell Sara and she'll stop flagging it.")))
    scored.sort(key=lambda t: -t[0])
    return [f for _, f in scored[:SPIKE_TOP_N]]


def _annual_renewal_categories(cur):
    """Expense categories where a yearly-cadence subscription stream billed in
    month `cur` — a doubled month explained by a known annual renewal. Matched
    by normalized merchant against that month's postings."""
    annual = {s["merchant"] for s in _subscription_streams()
              if s["cadence"] == "yearly"
              and (s["last"].year, s["last"].month) == cur}
    if not annual:
        return set()
    start = date(cur[0], cur[1], 1)
    end = date(cur[0], cur[1], calendar.monthrange(*cur)[1])
    out = set()
    for r in query(f"SELECT payee, root(account, 2) AS cat WHERE account ~ '^Expenses' "
                   f"AND date >= {start.isoformat()} AND date <= {end.isoformat()}"):
        if _normalize_merchant(r["payee"]) in annual:
            out.add(r["cat"])
    return out


# ------------------------------------------------------------ review queue
REVIEW_SHARE_PCT = 5  # the finding fires only past this share of the current month's
                      # expense postings — below it, the Activity badge is the standing
                      # meter and stragglers never page anyone


def review_queue():
    """The month can't be reviewed honestly yet — the categorization gate.

    Counts Expenses:Uncategorized (this vault's convention) and Expenses:FIXME
    (the beancount-import convention) — importers park what they can't
    categorize there rather than stalling. A finding emits ONLY when more
    than REVIEW_SHARE_PCT of the current data month's expense postings are
    uncategorized; the everyday backlog count lives on the Activity badge.
    """
    rows = query("SELECT year, month, account, count(*) AS n, "
                 "sum(convert(position,'USD')) AS v "
                 "WHERE account ~ '^Expenses' GROUP BY year, month, account")
    per, cur_total, cur_uncat = {}, 0, 0
    cur = max(((int(r["year"]), int(r["month"])) for r in rows), default=None)
    if cur is None:
        return []
    for r in rows:
        c = int(float(r["n"] or 0))
        uncat = r["account"] in ("Expenses:Uncategorized", "Expenses:FIXME")
        if uncat:
            k = per.setdefault(r["account"], [0, 0.0])
            k[0] += c
            k[1] += amount(r.get("v"))
        if (int(r["year"]), int(r["month"])) == cur:
            cur_total += c
            cur_uncat += c if uncat else 0
    share = 100.0 * cur_uncat / cur_total if cur_total else 0.0
    if share <= REVIEW_SHARE_PCT:
        return []
    n = sum(c for c, _ in per.values())
    v = sum(x for _, x in per.values())
    return [finding(
        "review-queue", "watch",
        f"{share:.0f}% of this month's spending is uncategorized — "
        f"{n} transactions ({money(v)}) need names",
        "Teach Sara a rule per payee — Activity room, or just tell her — and "
        "she re-files the history too. Until then the month can't be "
        "reviewed honestly.")]


# -------------------------------------------------------------------- goals
def goals_status():
    """Retired as a findings source — the Goals room owns its own empty
    state, so unset targets never page anyone. Kept for compatibility."""
    return []


# ------------------------------------------------------------ subscriptions
# Recurring-charge radar. The constants, each with its reasoning:
SUB_MIN_OCCURRENCES = 3       # two same-amount hits is coincidence; three on a rhythm is a contract
SUB_MONTHLY_GAP = (25, 35)    # monthly billing wobbles around 30d (month lengths, weekend posting)
SUB_YEARLY_GAP = (350, 380)   # annual renewals drift up to ~2 weeks past 365d before they're "late"
SUB_GAP_CONFORMANCE = 0.75    # ≥75% of a stream's gaps must sit in ONE window — tolerates a single
                              # comped/skipped cycle without letting irregular spend masquerade
SUB_ACTIVE_DAYS = {"monthly": 60, "yearly": 400}  # "still billing": within ~2 cycles for monthly;
                              # a yearly sub stays live until its renewal is ~3 weeks overdue
SUB_TWIN_OVERLAP_DAYS = 45    # same-merchant streams co-billing ≥1.5 cycles = two real accounts
                              # (twin billing); a price change shows as one stream ending as the
                              # next starts, so overlap is the discriminator between the two
SUB_MERCHANT_MAX_LEN = 40     # normalized-payee cap: past 40 chars it's all statement ref noise
SUB_CYCLES_PER_YEAR = {"monthly": 12, "yearly": 1}
# Recurring COMMITMENTS are not cancelable subscriptions — keep rent, daycare, insurance,
# taxes, tuition, and debt service off the radar (they'd otherwise dominate the "burden"
# number and bury the Netflixes). Override via rules.toml [subscriptions] ignore_account_regex.
SUB_IGNORE_ACCOUNT_DEFAULT = "Housing|Rent|Mortgage|Childcare|Insurance|Taxes|Loan|Tuition|Interest"


def _cents(x):
    """money() rounds to whole dollars; subscription prices live in cents."""
    return f"${x:,.2f}"


def _normalize_merchant(payee):
    """Collapse statement noise so 'NETFLIX.COM 866-579-7172 #8842' and
    'Netflix' group together: lowercase, strip date-ish tokens, #refs, long
    (5+) digit ids, punctuation; cap at SUB_MERCHANT_MAX_LEN."""
    s = (payee or "").lower()
    s = re.sub(r"\b\d{1,4}[/.-]\d{1,2}(?:[/.-]\d{2,4})?\b", " ", s)  # 07/15, 2026-07-15
    s = re.sub(r"#\w+", " ", s)                                       # ref numbers
    s = re.sub(r"\d{5,}", " ", s)                                     # ids, phone fragments
    s = re.sub(r"[^a-z0-9 ]+", " ", s)                                # punctuation
    return " ".join(s.split())[:SUB_MERCHANT_MAX_LEN].strip()


def _subscription_streams():
    """Expense postings grouped into recurring streams.

    A stream is (normalized merchant, |amount|, funding account) — the
    funding account (the card/bank leg of the same transaction, joined via
    the query `id`) is part of the key so each spouse's Spotify is its own
    stream; that's what makes twin billing detectable at all. The one blind
    spot: two enrollments billing the same amount to the SAME card merge
    into one stream and go unseen. Same-day duplicates within a stream
    collapse (a literal double-charge is a dispute, not a subscription).
    """
    ignore = rules().get("subscriptions", {}).get(
        "ignore_account_regex", SUB_IGNORE_ACCOUNT_DEFAULT)
    funding = {}
    for r in query("SELECT id, account WHERE account ~ '^(Assets|Liabilities)' "
                   "AND number < 0"):
        funding.setdefault(r["id"], r["account"])
    groups = {}
    for r in query("SELECT id, date, payee, account, number "
                   "WHERE account ~ '^Expenses' AND number > 0 AND currency = 'USD'"):
        if ignore and re.search(ignore, r["account"] or ""):
            continue
        merchant = _normalize_merchant(r["payee"])
        if not merchant:
            continue
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        amt = round(amount(r["number"]), 2)
        groups.setdefault((merchant, amt, funding.get(r["id"], "")), set()).add(d)
    streams = []
    for (merchant, amt, fund), dateset in groups.items():
        dates = sorted(dateset)
        if len(dates) < SUB_MIN_OCCURRENCES:
            continue
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        cadence = next((name for name, (lo, hi) in
                        (("monthly", SUB_MONTHLY_GAP), ("yearly", SUB_YEARLY_GAP))
                        if sum(1 for g in gaps if lo <= g <= hi)
                        >= SUB_GAP_CONFORMANCE * len(gaps)), None)
        if not cadence:
            continue
        streams.append({
            "merchant": merchant, "amount": amt, "funding": fund, "cadence": cadence,
            "first": dates[0], "last": dates[-1], "n": len(dates),
            "active": (date.today() - dates[-1]).days <= SUB_ACTIVE_DAYS[cadence],
            "monthly": amt * SUB_CYCLES_PER_YEAR[cadence] / 12.0})
    return streams


def subscriptions():
    """Recurring-charge radar: twin billing, price creep, total burden."""
    streams = _subscription_streams()
    if not streams:
        return []
    out = []
    by_merchant = {}
    for s in streams:
        by_merchant.setdefault(s["merchant"], []).append(s)
    for merchant, ss in sorted(by_merchant.items()):
        act = sorted((s for s in ss if s["active"]), key=lambda s: s["monthly"])
        twin = next(((a, b) for i, a in enumerate(act) for b in act[i + 1:]
                     if (min(a["last"], b["last"]) - max(a["first"], b["first"])).days
                     >= SUB_TWIN_OVERLAP_DAYS), None)
        if twin:
            cheap, other = twin
            out.append(finding(
                "subscriptions", "alert",
                f"Paying twice for merchant text `{merchant}` — cancel one and keep "
                f"{money(cheap['monthly'] * 12)}/yr",
                f"Two live charges (merchant text: `{merchant}`): "
                f"{_cents(cheap['amount'])}/{cheap['cadence']} on "
                f"`{cheap['funding'] or 'unknown account'}` and {_cents(other['amount'])}/"
                f"{other['cadence']} on `{other['funding'] or 'unknown account'}`, overlapping "
                f"since {max(cheap['first'], other['first'])}. Unless these are deliberate "
                f"separate plans, one is a leak — cancel the cheaper "
                f"({_cents(cheap['monthly'])}/mo) or merge to a family plan."))
            continue  # a twin explains the multiple amounts; don't also call it creep
        if act:
            newest = max(ss, key=lambda s: s["last"])
            oldest = min(ss, key=lambda s: s["first"])
            overlap = (min(newest["last"], oldest["last"])
                       - max(newest["first"], oldest["first"])).days
            delta_yr = ((newest["amount"] - oldest["amount"])
                        * SUB_CYCLES_PER_YEAR[newest["cadence"]])
            if (newest is not oldest and newest["active"] and delta_yr > 0
                    and overlap < SUB_TWIN_OVERLAP_DAYS):
                out.append(finding(
                    "subscriptions", "watch",
                    f"Price creep at merchant text `{merchant}`: {_cents(oldest['amount'])} → "
                    f"{_cents(newest['amount'])} (+{money(delta_yr)}/yr)",
                    f"Same merchant, new recurring price since {newest['first']} (was "
                    f"{_cents(oldest['amount'])} through {oldest['last']}). Price hikes ride on "
                    f"inertia — worth a downgrade/annual-billing/retention pass before renewal."))
    active = [s for s in streams if s["active"]]
    if active:  # the total burden, one info line — the audit itself lives elsewhere
        total_mo = sum(s["monthly"] for s in active)
        top = sorted(active, key=lambda s: -s["monthly"])[:3]
        out.append(finding(
            "subscriptions", "info",
            f"{len(active)} active recurring charges ≈ {money(total_mo)}/mo "
            f"({money(total_mo * 12)}/yr)",
            "Largest: " + " · ".join(f"`{s['merchant']}` {_cents(s['monthly'])}/mo"
                                     for s in top) + "."))
    return out


# ----------------------------------------------------- reconciliation state
RECON_ACTIVE_TXN_DAYS = 90  # a transaction in the last 90d = statements still arriving; beyond
                            # that the account is dormant and coverage() owns the conversation
RECON_STALE_DAYS = 60       # ~two statement cycles without a balance assertion: drift (missed
                            # rows, fees, holds) accumulates silently past that
# Investment/retirement custodians reconcile on a slower cadence than banks/cards, so they're
# out of scope here. Matched per account SEGMENT (prefix or suffix, case-insensitive) — extend
# the tuple when a new account style shows up. "transfer" also skips wash/routing buckets.
RECON_SKIP_SEGMENTS = ("401k", "403b", "457", "529", "ira", "roth", "hsa", "brokerage",
                       "invest", "pension", "retire", "treasury", "transfer")


def _matches_segments(account, tokens):
    segs = [s.lower() for s in account.split(":")]
    return any(s == t or s.startswith(t) or s.endswith(t) for s in segs for t in tokens)


def _posting_dates(currency=None):
    """{account: sorted DISTINCT posting dates} for every Assets/Liabilities
    account. Distinct, because rhythm is about days with activity — an
    opening balance with five legs is one day of history, not five.
    With currency="USD", only cash postings count — that limits the map to
    bank/card-style accounts (a shares-only holding account never posts USD)."""
    out = {}
    cur = f" AND currency = '{currency}'" if currency else ""
    for r in query("SELECT account, date WHERE account ~ '^(Assets|Liabilities)'"
                   f"{cur} ORDER BY account, date"):
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        dates = out.setdefault(r["account"], [])
        if not dates or dates[-1] != d:
            dates.append(d)
    return out


BALANCE_DIRECTIVE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+balance\s+([A-Za-z][\w:-]*)", re.M)


def _newest_assertions():
    """{account: date of its newest `balance` assertion} across ledger/*.beancount.

    Read from the files, not bean-query (assertions are directives, not
    postings). Anchored at line start, so the importers' commented-out
    unverified suggestions don't count as anchors.
    """
    newest = {}
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            txt = f.read_text()
        except OSError:
            continue
        for m in BALANCE_DIRECTIVE.finditer(txt):
            try:
                d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            if d > newest.get(m.group(2), date.min):
                newest[m.group(2)] = d
    return newest




def _closed_accounts():
    """Accounts with a `close` directive — reconciliation/coverage skip them."""
    import re as _re
    out = set()
    for f in (VAULT / "ledger").glob("*.beancount"):
        out.update(_re.findall(r"^\d{4}-\d{2}-\d{2}\s+close\s+(\S+)", f.read_text(), _re.M))
    return out


def reconciliation():
    """Balance-assertion staleness — is the ledger still anchored to reality?

    Per active bank/card account: no assertion in ~two statement cycles →
    re-anchor. Assertions are what turn "the ledger says" into "the bank
    agrees". Never-asserted accounts stay quiet — the first import anchors
    them, and nagging about it earned nobody anything.
    """
    today = date.today()
    anchors = _newest_assertions()
    closed = _closed_accounts()
    out = []
    for account, dates in sorted(_posting_dates(currency="USD").items()):
        if _matches_segments(account, RECON_SKIP_SEGMENTS):
            continue
        if account in closed:
            continue  # a closed account needs no anchor going forward
        if (today - dates[-1]).days > RECON_ACTIVE_TXN_DAYS:
            continue
        anchor = anchors.get(account)
        if anchor is not None and (today - anchor).days > RECON_STALE_DAYS:
            out.append(finding(
                "reconciliation", "watch",
                f"{account} last matched the bank {(today - anchor).days}d ago — "
                f"pull a statement",
                f"Last matched the bank on {anchor.isoformat()}; transactions kept "
                f"flowing since, and drift (missed rows, fees, holds) piles up "
                f"quietly. Pull the newest statement and Sara re-anchors to its "
                f"closing balance."))
    return out


# ----------------------------------------------------------------- coverage
COV_STALE_FACTOR = 2        # a feed is "stale" once it's been silent for 2x the account's own
                            # rhythm — one missed cycle is normal life, two is a broken feed
COV_GAP_FLOOR_DAYS = 35     # even a daily-swipe card only proves staleness after a statement
                            # cycle of silence; below this the median gap is noise
COV_HOLE_DAYS = 45         # a gap inside history longer than ~1.5 months = a statement that
                            # was never imported (feeds don't skip a beat that long on their own)
COV_MAX_FINDINGS = 5        # cap the noise — most-active accounts first, the rest wait
# rules.toml [[accounts]] cadence overrides the guessed rhythm: how often data actually
# arrives for this account. Values -> the expected max quiet days between imports.
COV_CADENCE_DAYS = {"fast": 35, "monthly": 45, "quarterly": 100}

# Declared card-cycle metadata — rules.toml [[accounts]] bill_day / statement_close
# (1-31 or "last"). Parsed here and shared with forecast.py, which imports from
# this module (never the reverse: projected_shortfall's deferred import is what
# keeps the two acyclic).
CYCLE_LAST_DAY = 31  # "last" parses to 31; _cycle_anchors clamps to each month's real end


def _cycle_day(value):
    """Parse a declared bill_day / statement_close value: 1-31 or "last" ->
    anchor day, None when absent or malformed. A typo'd hint must never crash
    a read-only tool — the account just falls back to pure inference."""
    if isinstance(value, str) and value.strip().lower() == "last":
        return CYCLE_LAST_DAY
    try:
        day = int(value)
    except (TypeError, ValueError):
        return None
    return day if 1 <= day <= 31 else None


def _cycle_anchors(day, start, end):
    """Every declared cycle-day date in (start, end] — the statement closes /
    autopay days the calendar says belong to that window. Clamped to short
    months, so day 31 (and "last") lands on Feb 28/29, Apr 30, ..."""
    out = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        a = date(y, m, min(day, calendar.monthrange(y, m)[1]))
        if start < a <= end:
            out.append(a)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def coverage():
    """Stale feeds and month-sized holes — where the ledger stopped seeing.

    An account's typical rhythm is the median gap between its postings
    (floored at COV_GAP_FLOOR_DAYS, or set explicitly via rules.toml
    [[accounts]] cadence); silent for 2x that → stale feed. A gap wider than
    max(COV_HOLE_DAYS, rhythm) inside the history → a missed statement.
    A declared statement_close/bill_day beats median-gap inference for that
    account: ONE statement is expected per cycle, anchored on the declared
    day — stale once COV_STALE_FACTOR declared days pass with no data, a
    hole where an internal gap swallows a whole declared cycle.
    An optional [[accounts]] exported_through date counts as freshness even
    when the statement had no transactions (quiet months aren't stale).
    One finding per account (stale wins over hole), capped at
    COV_MAX_FINDINGS, most-active accounts first.
    """
    today = date.today()
    cfg = {a.get("ledger_account"): a for a in rules().get("accounts", [])
           if a.get("ledger_account")}
    candidates = []  # (activity, finding)
    for account, dates in _posting_dates().items():
        if _matches_segments(account, ("transfer",)):
            continue
        acfg = cfg.get(account, {})
        declared_raw = acfg.get("statement_close", acfg.get("bill_day"))
        declared = _cycle_day(declared_raw)
        if declared is None and len(dates) < 3:
            continue  # not enough history to know a rhythm (a declared cycle needs none)
        inst = acfg.get("institution") or (account.split(":")[-2]
                                           if account.count(":") >= 2 else account)
        last_seen = dates[-1]
        exported = acfg.get("exported_through")
        if isinstance(exported, str):
            try:
                exported = datetime.strptime(exported, "%Y-%m-%d").date()
            except ValueError:
                exported = None
        if isinstance(exported, date) and exported > last_seen:
            last_seen = exported
        if declared is not None:
            # Declared cadence beats median-gap inference for this account:
            # one statement per cycle, anchored on the declared day.
            day_name = ("the last day of the month"
                        if str(declared_raw).strip().lower() == "last"
                        else f"day {declared}")
            owed = _cycle_anchors(declared, last_seen, today)
            if len(owed) >= COV_STALE_FACTOR:
                candidates.append((len(dates), finding(
                    "coverage", "watch",
                    f"{account}: stale feed — {len(owed)} declared statement days "
                    f"({day_name}) passed with no data. Pull from `{inst}`.",
                    f"Statements should have closed on "
                    f"{', '.join(d.isoformat() for d in owed)} ({day_name} cycle) but "
                    f"the newest data is {last_seen.isoformat()}. The feed has probably "
                    f"stopped — download the latest from `{inst}` and drop it in; "
                    f"Sara files it.")))
            else:
                hole = next(((a, b) for a, b in
                             zip(reversed(dates[:-1]), reversed(dates[1:]))
                             if len(_cycle_anchors(declared, a, b)) > 1), None)
                if hole:
                    a, b = hole
                    candidates.append((len(dates), finding(
                        "coverage", "watch",
                        f"{account}: statement gap — a full declared cycle "
                        f"({day_name}) has no data",
                        f"No postings between {a.isoformat()} and {b.isoformat()}, a "
                        f"window holding more than one declared statement day "
                        f"({day_name}) — that statement looks never-imported. "
                        f"Re-download that window from `{inst}` — overlapping "
                        f"exports are safe, Sara dedupes.")))
            continue
        gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]))
        floor = COV_CADENCE_DAYS.get(str(acfg.get("cadence", "")).lower(), COV_GAP_FLOOR_DAYS)
        expected = max(gaps[len(gaps) // 2], floor)
        silent = (today - last_seen).days
        if silent > COV_STALE_FACTOR * expected:
            candidates.append((len(dates), finding(
                "coverage", "watch",
                f"{account}: stale feed — no data for {silent}d. Pull from `{inst}`.",
                f"Newest data {last_seen.isoformat()} vs a typical rhythm of ~{expected}d "
                f"between postings. The feed has probably stopped — download a fresh "
                f"statement from `{inst}` and drop it in; Sara files it.")))
            continue
        hole_floor = max(COV_HOLE_DAYS, expected)
        hole = next(((a, b) for a, b in zip(reversed(dates[:-1]), reversed(dates[1:]))
                     if (b - a).days > hole_floor), None)
        if hole:
            a, b = hole
            candidates.append((len(dates), finding(
                "coverage", "watch",
                f"{account}: {(b - a).days}d hole in its history — a statement is missing",
                f"No postings between {a.isoformat()} and {b.isoformat()} despite activity on "
                f"both sides; that window looks never-imported. Re-download that window "
                f"from `{inst}` — overlapping exports are safe, Sara dedupes.")))
    candidates.sort(key=lambda t: -t[0])
    return [f for _, f in candidates[:COV_MAX_FINDINGS]]


# --------------------------------------------------------------- milestones
def _liquid_net_worth():
    """Assets+Liabilities in USD, illiquid paper excluded — query.py's networth."""
    excl = illiquid_currency_regex()
    where = ("account ~ '^(Assets|Liabilities)'"
             + (f" AND NOT currency ~ '{bql_str(excl)}'" if excl else ""))
    rows = query(f"SELECT sum(convert(position,'USD')) AS v WHERE {where}")
    return amount(rows[0]["v"]) if rows and rows[0].get("v") else 0.0


def _yaml_number_list(v):
    """Numbers out of an inline yaml list rendered as a string ('[1, 2.5]')."""
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", str(v or ""))]


FIXED_DRIFT_TOLERANCE = 2000  # a fixed-balance account may breathe this much
                              # before a sweep/top-up is worth anyone's time


def fixed_balances():
    """Accounts the household holds at a FIXED dollar amount (rules.toml
    [fixed_balances]: account -> amount). Above tolerance -> sweep the excess
    to its destination; below -> top it up. The thesis decides the numbers;
    this check just notices drift."""
    conf = rules().get("fixed_balances", {})
    if not conf:
        return []
    out = []
    for account, target in conf.items():
        acct = str(account).replace("'", "")  # account names never contain quotes; strip so rules.toml text can't break out of the BQL string
        rows = query(f"SELECT sum(number) as v WHERE account = '{acct}' AND currency = 'USD'")
        bal = amount(rows[0].get("v")) if rows else 0.0
        drift = bal - float(target)
        if abs(drift) <= FIXED_DRIFT_TOLERANCE:
            continue
        direction = "over" if drift > 0 else "under"
        fix = ("move the excess on" if drift > 0
               else "top it up from the buffer")
        out.append(finding(
            "fixed-balance", "watch",
            f"{account} is ${abs(drift):,.0f} {direction} its fixed ${float(target):,.0f}",
            f"You hold this account at a set level on purpose — {fix} this week; "
            f"Sara knows the destination."))
    return out


# -------------------------------------------------------------------- lanes
# "The machine": standing money movements declared in rules.toml [[lanes]] —
# payroll deposits, auto-invest orders, fixed floors. The detector scans the
# ledger's recent window and says whether each lane actually ran; the home
# page renders lane_status() directly and the lanes() check turns broken
# lanes into findings, so page and findings always agree.
#
# A lane:
#   name    = "Alex's paycheck -> checking"      (required)
#   kind    = "deposit" | "invest" | "floor"      (required)
#   account = "Assets:US:..."                     (required)
#   amount  = 3950.00        approximate is fine; deposits match within ±35%
#                            unless `source` is given (then source decides and
#                            amount is display-only). invest/floor: declared
#                            program size / floor (floor may omit it to mirror
#                            [fixed_balances]).
#   cadence = "monthly" | "semimonthly" | "biweekly" | "per-paycheck"
#   day     = 26             optional, display only ("monthly on the 26th")
#   source  = "ACME.*PAYROLL"   optional payee regex (case-insensitive)
#   starts  = 2026-08-20     optional first-expected date: until one cadence
#                            period past it, a never-seen lane shows amber
#                            "watching for first arrival" instead of red
#   commodity = "VTSAX|VTIAX"   invest only: regex of commodities that count
LANE_PERIOD_DAYS = {"monthly": 31, "semimonthly": 16, "biweekly": 14,
                    "per-paycheck": 16}
LANE_WINDOW_PERIODS = 2   # "recent" = the last 2 cadence periods
LANE_AMOUNT_TOL = 0.35    # deposits: |posting − amount| within 35% counts
LANE_REINVEST = re.compile(r"REINVEST|DIVIDEND|\bDIV\b", re.I)


def _lane_date(value):
    """tomllib gives a datetime.date for a bare TOML date; accept a string too."""
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _lane_amount(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _lane_deposit_events(account, source, amt):
    """(date, amount) of USD arrivals that count for this lane, oldest first."""
    rows = query(f"SELECT date, payee, number WHERE account = '{bql_str(account)}' "
                 f"AND currency = 'USD' AND number > 0 ORDER BY date")
    out = []
    for r in rows:
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        n = amount(r["number"])
        if source:
            if not re.search(source, r["payee"] or "", re.I):
                continue
        elif amt is not None and abs(n - amt) > LANE_AMOUNT_TOL * amt:
            continue
        out.append((d, n))
    return out


def _lane_invest_events(account, source, commodity):
    """(date, usd_total) of purchase days — non-USD units bought, valued at
    cost, reinvested dividends excluded (override with an explicit source)."""
    rows = query(f"SELECT date, payee, number, currency, cost(position) AS c "
                 f"WHERE account = '{bql_str(account)}' AND currency != 'USD' "
                 f"AND number > 0 ORDER BY date")
    by_day = {}
    for r in rows:
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        payee = r["payee"] or ""
        if source:
            if not re.search(source, payee, re.I):
                continue
        elif LANE_REINVEST.search(payee):
            continue
        if commodity and not re.fullmatch(commodity, r["currency"] or ""):
            continue
        by_day[d] = by_day.get(d, 0.0) + amount(r["c"])
    return sorted(by_day.items())


def lane_status(today=None):
    """One row per [[lanes]] entry, in declared order.

    Row: {name, kind, account, cadence, day, amount, status, last,
    last_amount, expected, balance, floor, note} — status is one of
    ok | pending | overdue (deposit/invest), intact | below (floor),
    invalid (misdeclared lane; note says what's wrong)."""
    today = today or date.today()
    fixed = rules().get("fixed_balances", {})
    out = []
    for lane in rules().get("lanes", []):
        name = str(lane.get("name") or lane.get("account") or "unnamed lane")
        kind = str(lane.get("kind") or "")
        account_name = str(lane.get("account") or "")
        cadence = str(lane.get("cadence") or "monthly")
        amt = _lane_amount(lane.get("amount"))
        row = {"name": name, "kind": kind, "account": account_name,
               "cadence": cadence, "day": lane.get("day"), "amount": amt,
               "status": "invalid", "last": None, "last_amount": None,
               "expected": None, "balance": None, "floor": None, "note": ""}
        out.append(row)
        if kind not in ("deposit", "invest", "floor") or not account_name:
            row["note"] = "kind must be deposit/invest/floor and account is required"
            continue
        if kind == "floor":
            floor = amt if amt is not None else _lane_amount(fixed.get(account_name))
            if floor is None:
                row["note"] = ("no floor amount — set `amount` or declare the "
                               "account under [fixed_balances]")
                continue
            rows = query(f"SELECT sum(number) AS v WHERE account = "
                         f"'{bql_str(account_name)}' AND currency = 'USD'")
            bal = amount(rows[0].get("v")) if rows else 0.0
            row.update(floor=floor, balance=bal,
                       status="intact" if bal >= floor - 0.005 else "below")
            continue
        period = LANE_PERIOD_DAYS.get(cadence)
        if period is None:
            row["note"] = f"unknown cadence `{cadence}`"
            continue
        source = lane.get("source")
        if kind == "deposit":
            events = _lane_deposit_events(account_name, source, amt)
        else:
            events = _lane_invest_events(account_name, source, lane.get("commodity"))
        window_start = today - timedelta(days=LANE_WINDOW_PERIODS * period)
        recent = [e for e in events if e[0] >= window_start]
        starts = _lane_date(lane.get("starts"))
        if recent:
            row.update(status="ok", last=recent[-1][0], last_amount=recent[-1][1])
        elif events:
            row.update(status="overdue", last=events[-1][0],
                       last_amount=events[-1][1],
                       expected=events[-1][0] + timedelta(days=period))
        elif starts and today <= starts + timedelta(days=period):
            row.update(status="pending", expected=starts)
        else:
            row.update(status="overdue", expected=starts)
    return out


def lanes():
    """Findings for broken lanes: a standing order that didn't run is an
    alert (money is not where the household believes it is), a breached
    floor is an alert, a misdeclared lane is a watch. Amber
    watching-for-first-arrival lanes are page-only, not findings."""
    out = []
    for row in lane_status():
        if row["status"] == "overdue":
            exp = (f" — expected ~{row['expected'].isoformat()}"
                   if row["expected"] else "")
            seen = (f"Last ran {row['last'].isoformat()} "
                    f"({money(row['last_amount'])})." if row["last"]
                    else "Never seen in the ledger.")
            out.append(finding(
                "lanes", "alert",
                f"The machine: `{row['name']}` hasn't run{exp}",
                f"{seen} It's supposed to run {row['cadence']} into "
                f"`{row['account']}`. Check the standing order at the bank (and "
                f"that the account's statements are current) — or if you changed "
                f"the plan, tell Sara so she watches the right thing."))
        elif row["status"] == "below":
            out.append(finding(
                "lanes", "alert",
                f"The machine: {row['account']} is under its "
                f"{money(row['floor'])} floor ({money(row['balance'])})",
                "You keep this account above that floor on purpose. Top it up "
                "this week — or lower the floor if the plan changed."))
        elif row["status"] == "invalid":
            out.append(finding(
                "lanes", "watch",
                f"The machine: lane `{row['name']}` is misdeclared",
                f"Sara can't watch it as written ({row['note']}). Ask her to fix "
                f"the setup — an unwatchable lane protects nothing."))
    return out


# --------------------------------------------------------- projected shortfall
def projected_shortfall():
    """Any account whose projected minimum (forecast.py's default horizon)
    crosses below $0 (alert) or below its rules.toml [fixed_balances] floor
    (watch), with the crunch date and the flows that cause it.

    The projection is forecast.py's: cadence-detected recurring streams only,
    so every number here is an ESTIMATE — the finding is a reason to look,
    not a statement. Accounts already under the line today are the
    fixed-balance / reconciliation checks' business, not this one's.
    An account with a declared [[lanes]] invest program stays silent: its
    big projected outflow IS the plan, and the plan funds it (settlement
    cash, money-market sweeps) in ways cadence inference can't see.
    """
    from sara.advisor.forecast import DEFAULT_DAYS, build_forecast  # deferred: forecast
    # imports this module's helpers, so a top-level import would be circular
    warns = build_forecast()["household"]["warns"]
    lane_funded = {str(lane.get("account")) for lane in rules().get("lanes", [])
                   if lane.get("kind") == "invest" and lane.get("account")}
    out = []
    for w in warns:
        if w["account"] in lane_funded:
            continue  # the declared program owns this outflow and its funding
        if w["kind"] == "below_zero":
            out.append(finding(
                "projected-shortfall", "alert",
                f"{w['account']} projected to hit ~{money(w['min'])} around {w['date']}",
                f"{DEFAULT_DAYS}-day projection (estimates from spending patterns, not "
                f"a statement) crosses below $0. Biggest bills before the crunch: "
                f"`{w['drivers']}`. Move money in before {w['date']}, or push one of "
                f"those payments past it."))
        else:
            out.append(finding(
                "projected-shortfall", "watch",
                f"{w['account']} projected under its {money(w['floor'])} floor "
                f"(~{money(w['min'])} around {w['date']})",
                f"You hold this account at a set level; the {DEFAULT_DAYS}-day "
                f"projection (estimates from spending patterns) dips below it. "
                f"Biggest bills on the way: `{w['drivers']}`. Top it up ahead of "
                f"time, or move one of those payment dates."))
    return out


# ---------------------------------------------------------------- cash drag
CASH_DRAG_MIN_BALANCE = 10_000  # under this, moving the money earns lunch, not a finding
CASH_DRAG_MIN_GAP_PTS = 1.0     # a declared APY a full point under the hurdle is drag;
                                # closer than that is rate noise, not a decision


def _apy(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None  # a typo'd rate must never crash a read-only tool


def cash_drag():
    """Idle-cash drag: declared APYs vs the household's hurdle rate.

    Wholly opt-in and silent until the vault declares rates: rules.toml
    [cash_apy] maps account -> its stated APY (from the institution's own
    page — never guessed), and `cash_hurdle_apy` (facts/goals yaml, or a
    key inside [cash_apy]) names what parked cash SHOULD earn. Any declared
    account holding >= $10k at >= 1.0 point under the hurdle emits a watch:
    the drag is balance x gap, in $/yr. When the money moves, a dated note's
    `- [x] ... realized $N/yr` line turns the fix into a counted win.
    """
    conf = rules().get("cash_apy", {})
    declared = {str(k): _apy(v) for k, v in conf.items()
                if k != "cash_hurdle_apy"}
    hurdle = _apy(goals().get("cash_hurdle_apy"))
    if hurdle is None:
        hurdle = _apy(conf.get("cash_hurdle_apy"))
    if hurdle is None or not declared:
        return []
    out = []
    for account, apy in sorted(declared.items()):
        if apy is None:
            continue
        gap = hurdle - apy
        if gap < CASH_DRAG_MIN_GAP_PTS:
            continue
        rows = query(f"SELECT sum(number) AS v WHERE account = "
                     f"'{bql_str(account)}' AND currency = 'USD'")
        bal = amount(rows[0].get("v")) if rows else 0.0
        if bal < CASH_DRAG_MIN_BALANCE:
            continue
        drag = bal * gap / 100.0
        out.append(finding(
            "cash-drag", "watch",
            f"Idle-cash drag ~{money(drag)}/yr: {account} earns {apy:g}% "
            f"vs the {hurdle:g}% hurdle",
            f"Move the {money(bal)} earning {apy:g}% to a {hurdle:g}%-class "
            f"home and keep ~{money(drag)}/yr. HYSA, money market, or T-bills, "
            f"per the plan. If the account's rate changed, tell Sara the new "
            f"number — and tell her when the money moves, it goes on the wins "
            f"board."))
    return out


def milestones():
    """Fire once when liquid net worth first crosses a configured milestone.

    Config lives in the facts/goals/index.md yaml block as FLAT keys (the
    goals() parser is flat, so nested yaml would be awkward — this is the
    simplest correct shape, documented in that file where used):

        milestone_net_worth_above: [100000, 250000]
        milestone_net_worth_above_crossed: []

    Crossed values are recorded back into the *_crossed list (the module's
    one deliberate side effect) so each milestone fires exactly once; remove
    a value from the crossed list to re-arm it.
    """
    g = goals()
    targets = _yaml_number_list(g.get("milestone_net_worth_above"))
    if not targets:
        return []
    crossed = _yaml_number_list(g.get("milestone_net_worth_above_crossed"))
    nw = _liquid_net_worth()
    newly = sorted(t for t in targets if t not in crossed and nw >= t)
    if not newly:
        return []
    _record_crossed("milestone_net_worth_above_crossed", sorted(crossed + newly))
    return [finding(
        "milestones", "info",
        f"Milestone crossed: liquid net worth above {money(t)} (now {money(nw)})",
        "Noted, so it only fires once. Worth marking — and worth "
        "checking that allocation, insurance limits, and the thesis still fit at this size.")
        for t in newly]


def _record_crossed(key, values):
    """Rewrite the `<key>: [...]` inline list inside the goals yaml block,
    creating the line under milestone_net_worth_above if it doesn't exist.
    Touches nothing else in the file."""
    path = VAULT / "facts" / "goals" / "index.md"
    if not path.exists():
        return
    text = path.read_text()
    fmt = "[" + ", ".join(str(int(v)) if v == int(v) else str(v) for v in values) + "]"
    new, n = re.subn(rf"(?m)^(\s*{re.escape(key)}:\s*)\[[^\]]*\]",
                     lambda m: m.group(1) + fmt, text, count=1)
    if n == 0:
        new, n = re.subn(r"(?m)^(\s*)(milestone_net_worth_above:.*)$",
                         lambda m: m.group(0) + f"\n{m.group(1)}{key}: {fmt}", text, count=1)
    if n:
        path.write_text(new)


# Allocation drift retired as a findings source (2026-08-07): the mix and its
# drift live in the Investments room via allocation.allocation_view — a page
# meter, not a page. The data layer is untouched.


# ------------------------------------------------------------ plaid feeds
PLAID_WATCH_DAYS = 3
PLAID_ALERT_DAYS = 7


def plaid_freshness():
    """A configured Plaid item whose sync has gone quiet — the daemon died,
    the token broke, or the laptop never woke. Cursor timestamps live in
    $VAULT/.secrets/plaid-cursors.json, written only on successful --write,
    so staleness here means data genuinely is not landing. Watch past
    PLAID_WATCH_DAYS, alert past PLAID_ALERT_DAYS; a linked item that has
    NEVER synced is its own watch (the trust ramp stalled)."""
    import json
    sources = rules().get("sources", {})
    items = (sources.get("plaid", {}) or {}).get("items", {}) if isinstance(sources, dict) else {}
    if not isinstance(items, dict) or not items:
        return []
    cursor_file = VAULT / ".secrets" / "plaid-cursors.json"
    stamps = {}
    if cursor_file.is_file():
        try:
            stamps = json.loads(cursor_file.read_text()).get("items", {})
        except (ValueError, AttributeError):
            stamps = {}
    out = []
    today = date.today()
    for alias in sorted(items):
        synced_raw = (stamps.get(alias) or {}).get("last_synced", "")
        if not synced_raw:
            out.append(finding(
                "plaid_freshness", "watch",
                f"Plaid item `{alias}` is configured but has never synced",
                f"The {alias} connection is set up but has never pulled data. Ask "
                f"Sara to run the first sync — you read the report before anything "
                f"is written."))
            continue
        try:
            synced = datetime.fromisoformat(str(synced_raw)).date()
        except ValueError:
            continue
        silent = (today - synced).days
        if silent > PLAID_WATCH_DAYS:
            severity = "alert" if silent > PLAID_ALERT_DAYS else "watch"
            out.append(finding(
                "plaid_freshness", severity,
                f"Plaid item `{alias}` has not synced in {silent} days",
                f"Data stopped arriving {silent} days ago (last sync "
                f"{synced.isoformat()}). Ask Sara to sync it — if the connection "
                f"broke, she can repair it free. Never re-link from scratch; that "
                f"burns one of the ten lifetime links."))
    return out


def catch_all_lumps(rows=None):
    """Tripwire: no single row over $10K may sit in a catch-all account.

    Self-transfers and principal returns hiding in Income:US:Other or
    Expenses:Uncategorized silently corrupt every flow view — a lump that
    big always has a real name (transfer, maturity, opening equity).
    Exact +/- twins inside the same account net to zero first: a reconciled
    round-trip (a treasury buy and its payback, an advance and its refund)
    is already telling a complete story and never fires.
    """
    from sara.vault import amount, query
    LIMIT = 10_000
    q = ("SELECT date, account, payee, number AS amt "
         "WHERE account ~ 'Income:US:Other|Expenses:Uncategorized'")
    big = []
    for r in query(q):
        amt = amount(r.get("amt", "") or "")
        if abs(amt) >= LIMIT:
            big.append((r, amt))
    tally = Counter((r.get("account"), amt) for r, amt in big)
    netted = Counter()  # (account, amt) -> how many rows cancel against a twin
    for (acct, amt), n in tally.items():
        if amt > 0:
            k = min(n, tally.get((acct, -amt), 0))
            if k:
                netted[(acct, amt)] = k
                netted[(acct, -amt)] = k
    out = []
    for r, amt in big:
        key = (r.get("account"), amt)
        if netted.get(key, 0) > 0:
            netted[key] -= 1
            continue
        out.append(finding(
            "catch-all-lump", "alert",
            f"${abs(amt):,.0f} is filed as \"miscellaneous\" — money that big has a real name",
            f"{r.get('date', '?')} {(r.get('payee') or 'no payee')} sits in "
            f"{r.get('account', '?')}. A lump like this is a transfer, a "
            f"maturity, or opening money — tell Sara what it was and the "
            f"flow numbers get honest again."))
    return out


TRANSFERS_DRIFT_TOLERANCE = 1000  # in-flight money may wobble this much
                                  # between the send and the landing


def transfers_drift():
    """The in-flight account must hold exactly what's DECLARED to be mid-air.

    Assets:US:Transfers is where money waits between our own accounts — and
    where misfiled flows go to hide. rules.toml [transfers] in_flight (default
    0) declares today's legitimate in-transit total; when the parked balance
    strays more than $1,000 from it, something landed under the wrong name.
    """
    conf = rules().get("transfers", {})
    try:
        declared = float(conf.get("in_flight", 0) or 0)
    except (TypeError, ValueError):
        declared = 0.0
    rows = query("SELECT sum(number) AS v "
                 "WHERE account = 'Assets:US:Transfers' AND currency = 'USD'")
    bal = amount(rows[0].get("v")) if rows else 0.0
    drift = bal - declared
    if abs(drift) <= TRANSFERS_DRIFT_TOLERANCE:
        return []
    direction = "more" if drift > 0 else "less"
    return [finding(
        "transfers-drift", "alert",
        f"${abs(drift):,.0f} {direction} than the declared in-flight money is parked in Transfers",
        f"The in-flight account holds ${bal:,.2f}; the declared mid-transfer total is "
        f"${declared:,.2f}. The gap is real money wearing an \"in motion\" costume — "
        f"usually a transfer that landed under a different name, or income or spending "
        f"in disguise. Walk the newest Transfers rows with Sara and give each its real "
        f"name — and when a big sweep departs or lands, tell Sara so she can update "
        f"the declared amount and keep this tripwire honest.")]


ALL = [concentration, deadlines, inbox, anomaly, subscriptions, reconciliation,
       coverage, review_queue, milestones, fixed_balances, lanes,
       projected_shortfall, cash_drag, plaid_freshness,
       catch_all_lumps, transfers_drift]




def run_all():
    findings, errors = [], []
    for fn in ALL:
        try:
            findings.extend(fn())
        except (Exception, SystemExit) as e:  # one broken check must not silence the rest
            errors.append(f"{fn.__name__}: {e}")
    return findings, errors
