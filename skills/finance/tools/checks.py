#!/usr/bin/env python3
"""The planner's checks. Deterministic; each returns a list of findings.

A finding: {"check", "severity", "title", "detail"} — no side effects here.
severity: "alert" (act soon) | "watch" (note) | "info"
Run via run_checks.py, which writes reports/findings.md.
Nothing household-specific lives here: thresholds come from facts/goals,
tuning from rules.toml, dates from facts/, transactions from the ledger.
One deliberate exception to "no side effects": milestones() records crossed
milestones back into facts/goals/index.md so each fires exactly once.
"""
import re
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import (VAULT, amount, dated_bullets, illiquid_currency_regex,  # noqa: E402
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
def concentration():
    """Flag any single holding above the ceiling, as % of total (liquid+paper)."""
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
    out = []
    for sym, v in list(values.items()) + list(paper.items()):
        pct = 100.0 * v / total
        if pct > ceiling:
            tag = " (illiquid paper)" if sym in paper else ""
            out.append(finding(
                "concentration", "watch",
                f"{sym}{tag} is {pct:.0f}% of net worth (ceiling {ceiling:.0f}%)",
                f"~{money(v)} in one position vs ~{money(total)} total. If it's illiquid, "
                f"the actionable moment is a liquidity event — apply the THESIS.md selling "
                f"rule then. If liquid, this is a diversification decision now. Watch, not alarm."))
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
                f"From {relpath}. Confirm whether it needs an action or is informational."))
    return out


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
                f"{freq[payee.upper()]}x ever. Confirm it's yours — once confirmed, append it to [anomaly] ignore_payee_regex in rules.toml so it never re-alerts."))
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
    for cat, series in by_cat.items():
        if lumpy and re.search(lumpy, cat):
            continue
        hist = sorted(series[ym] for ym in trailing if series.get(ym, 0) > 0)
        if len(hist) < SPIKE_MIN_ACTIVE_MONTHS:
            continue
        med = hist[len(hist) // 2]  # upper median — conservative, fires less
        curv = series.get(cur, 0.0)
        if curv >= floor_amt and curv > SPIKE_FACTOR * med:
            scored.append((curv - med, finding(
                "anomaly", "watch",
                f"{cat.replace('Expenses:', '')} ran {curv / med:.1f}x its usual month: "
                f"{money(curv)} vs ~{money(med)} median",
                f"{cur[0]}-{cur[1]:02d} vs the median of its active months in the trailing "
                f"{SPIKE_TRAILING_MONTHS}. Scan the line items — planned one-off, price creep, "
                f"or a miscategorization. If it's known-lumpy (annual premiums, taxes), add it "
                f"to rules.toml [anomaly] ignore_account_regex so it stops firing.")))
    scored.sort(key=lambda t: -t[0])
    return [f for _, f in scored[:SPIKE_TOP_N]]


# ------------------------------------------------------------ review queue
REVIEW_SHARE_PCT = 2  # >2% of the latest month's expense postings uncategorized = the month
                      # can't be reviewed honestly yet; below that, stragglers are tolerable


def review_queue():
    """Uncategorized postings are unfinished work — the categorization backlog.

    Counts Expenses:Uncategorized (this vault's convention) and Expenses:FIXME
    (the beancount-import convention) — importers park what they can't
    categorize there rather than stalling, so the debt must stay visible.
    Also the monthly review gate: when more than REVIEW_SHARE_PCT of the
    latest data month's expense postings are still uncategorized, the month
    isn't reviewable yet, so the finding escalates to at least watch.
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
    n = sum(c for c, _ in per.values())
    if n == 0:
        return []
    v = sum(x for _, x in per.values())
    fixme = per.get("Expenses:FIXME", [0, 0.0])[0]
    share = 100.0 * cur_uncat / cur_total if cur_total else 0.0
    sev = "alert" if n > 100 else ("watch" if n > 10 or share > REVIEW_SHARE_PCT else "info")
    gate = (f" {share:.0f}% of {cur[0]}-{cur[1]:02d}'s postings are uncategorized — "
            f"categorize before reviewing the month." if share > REVIEW_SHARE_PCT else "")
    return [finding(
        "review-queue", sev,
        f"{n} uncategorized transactions ({money(v)}) awaiting rules"
        + (f" — {fixme} in Expenses:FIXME" if fixme else ""),
        f"Run `tools/run query.py uncategorized` for the top payees, add "
        f"[[payee_rules]] to rules.toml, then `tools/run recategorize.py --write`.{gate}")]


# -------------------------------------------------------------------- goals
def goals_status():
    unset = [k for k, v in goals().items() if v is None]
    if not unset:
        return []
    return [finding(
        "goals", "info",
        f"{len(unset)} goal targets not yet set: {', '.join(unset)}",
        "Advisor proposes numbers once the spend/income baseline exists.")]


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
    if active:
        total_mo = sum(s["monthly"] for s in active)
        top = sorted(active, key=lambda s: -s["monthly"])[:5]
        out.append(finding(
            "subscriptions", "info",
            f"{len(active)} active recurring charges ≈ {money(total_mo)}/mo "
            f"({money(total_mo * 12)}/yr)",
            "Largest (merchant text): "
            + " · ".join(f"`{s['merchant']}` {_cents(s['monthly'])}/mo" for s in top)
            + ". The standing question for each: still used? right tier? annual billing "
              "cheaper? (See the savings-hunt reference for the full audit.)"))
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
    re-anchor; never asserted at all → say so once (info). Assertions are
    what turn "the ledger says" into "the bank agrees".
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
        if anchor is None:
            out.append(finding(
                "reconciliation", "info",
                f"{account} has never had a balance assertion",
                f"The account is active but nothing anchors its history to a statement. One "
                f"line fixes it: `YYYY-MM-DD balance {account}  <closing> USD` from any "
                f"statement (importers emit one automatically when continuity verifies)."))
        elif (today - anchor).days > RECON_STALE_DAYS:
            out.append(finding(
                "reconciliation", "watch",
                f"{account} last reconciled {(today - anchor).days}d ago — "
                f"pull a statement and re-anchor",
                f"Newest balance assertion is {anchor.isoformat()}; transactions kept flowing "
                f"since. Drift (missed rows, fees, holds) accumulates silently — import the "
                f"latest statement or add an assertion from its closing balance."))
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


def coverage():
    """Stale feeds and month-sized holes — where the ledger stopped seeing.

    An account's typical rhythm is the median gap between its postings
    (floored at COV_GAP_FLOOR_DAYS, or set explicitly via rules.toml
    [[accounts]] cadence); silent for 2x that → stale feed. A gap wider than
    max(COV_HOLE_DAYS, rhythm) inside the history → a missed statement.
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
        if len(dates) < 3:
            continue  # not enough history to know a rhythm
        acfg = cfg.get(account, {})
        gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]))
        floor = COV_CADENCE_DAYS.get(str(acfg.get("cadence", "")).lower(), COV_GAP_FLOOR_DAYS)
        expected = max(gaps[len(gaps) // 2], floor)
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
        silent = (today - last_seen).days
        if silent > COV_STALE_FACTOR * expected:
            candidates.append((len(dates), finding(
                "coverage", "watch",
                f"{account}: stale feed — no data for {silent}d. Pull from `{inst}`.",
                f"Newest data {last_seen.isoformat()} vs a typical rhythm of ~{expected}d "
                f"between postings. The feed has probably stopped — pull a fresh export "
                f"from `{inst}` (institution text) and import it."
                + (" (Rhythm set by rules.toml cadence.)" if "cadence" in acfg else ""))))
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
                f"both sides; that window looks never-imported. Re-pull it from `{inst}` — the "
                f"importer dedupes, so overlapping exports are safe.")))
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
        fix = ("sweep the excess to its thesis destination" if drift > 0
               else "top it up from the operating buffer")
        out.append(finding(
            "fixed-balance", "watch",
            f"{account} is ${abs(drift):,.0f} {direction} its fixed ${float(target):,.0f}",
            f"THESIS.md sets this account at a fixed amount — {fix}."))
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
    """
    from forecast import DEFAULT_DAYS, build_forecast  # deferred: forecast
    # imports this module's helpers, so a top-level import would be circular
    warns = build_forecast()["household"]["warns"]
    out = []
    for w in warns:
        if w["kind"] == "below_zero":
            out.append(finding(
                "projected-shortfall", "alert",
                f"{w['account']} projected to hit ~{money(w['min'])} around {w['date']}",
                f"{DEFAULT_DAYS}-day cash-flow projection (cadence patterns — estimates, "
                f"not a statement) crosses below $0. Largest projected outflows before "
                f"the crunch (merchant text): `{w['drivers']}`. Verify with tools/run forecast.py, then "
                f"move money or a payment date before it lands."))
        else:
            out.append(finding(
                "projected-shortfall", "watch",
                f"{w['account']} projected under its {money(w['floor'])} floor "
                f"(~{money(w['min'])} around {w['date']})",
                f"THESIS.md holds this account at a fixed level; the {DEFAULT_DAYS}-day "
                f"projection (estimates from cadence patterns) dips below it. Drivers "
                f"(merchant text): `{w['drivers']}`. Re-time the outflow or pre-position a top-up."))
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
        "Recorded in facts/goals/index.md so this fires once. Worth marking — and worth "
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


ALL = [concentration, deadlines, anomaly, subscriptions, reconciliation, coverage,
       review_queue, goals_status, milestones, fixed_balances, projected_shortfall]


def run_all():
    findings, errors = [], []
    for fn in ALL:
        try:
            findings.extend(fn())
        except (Exception, SystemExit) as e:  # one broken check must not silence the rest
            errors.append(f"{fn.__name__}: {e}")
    return findings, errors
