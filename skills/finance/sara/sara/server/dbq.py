"""Exploratory reads over reports/analytics.duckdb — every query in one place.

All SQL lives server-side and is parameterized (`$name` bindings); the client
sends filter VALUES only. Money is formatted here into display strings (the
sacred contract); plain numbers ride along solely as chart geometry. Sign
conventions are beancount's: expense postings positive, income negative,
liability balances negative.

Owner semantics (the household lens): each row's owner is the owner of the
FUNDING account — for an Expenses/Income posting that is ``other_account``;
for balances it is the account itself. ``joint`` is shared, NULL is
unassigned, and ``transit`` (clearing accounts) never belongs to a person, so
any owner-filtered view excludes it by construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .readmodel import DB, Row, delta0, feed_money, money0, money2

UNCAT_ACCOUNTS = ("Expenses:Uncategorized", "Expenses:FIXME")
OWNER_TRANSIT = "transit"
LT_DAYS = 365
FEED_PAGE = 60
MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
              "Sep", "Oct", "Nov", "Dec"]


def _f(v: object) -> float:
    if isinstance(v, (int, float, Decimal)):
        return float(v)
    return 0.0


def _cursor_parts(cursor: str | None) -> tuple[str, int] | None:
    """Parse a keyset cursor ('<iso-date>:<posting_id>'). A malformed cursor
    — bad date, bad id — is ignored (first page served), never a 500."""
    if not cursor:
        return None
    d, _, pid = cursor.partition(":")
    if not pid.lstrip("-").isdigit():
        return None
    try:
        return date.fromisoformat(d).isoformat(), int(pid)
    except ValueError:
        return None


def _s(v: object) -> str:
    return v if isinstance(v, str) else ("" if v is None else str(v))


def _iso(v: object) -> str:
    return v.isoformat() if isinstance(v, date) else _s(v)


def _day_label(d: object) -> str:
    if not isinstance(d, date):
        return _s(d)
    return f"{d.strftime('%a')} {MONTH_ABBR[d.month]} {d.day}"


def _mon_d(d: object) -> str:
    if not isinstance(d, date):
        return _s(d)
    return f"{MONTH_ABBR[d.month]} {d.day}"


def home_currency() -> str:
    row = DB.one("SELECT home_currency FROM build_info LIMIT 1")
    return _s(row["home_currency"]) if row else "USD"


def ledger_range() -> tuple[str | None, str | None]:
    row = DB.one("SELECT min_date, max_date FROM build_info LIMIT 1")
    if not row:
        return None, None
    return (_iso(row["min_date"]) or None, _iso(row["max_date"]) or None)


# ------------------------------------------------------------------ owners
def owners() -> list[dict[str, str]]:
    """Distinct declared owners (people first, then joint) — the lens menu.
    Empty when the ledger declares none; the lens stays hidden then."""
    # an owner earns a pill with material holdings or material flow — $1 of
    # balance or $100 of trailing-year movement. Existence isn't enough: an
    # all-but-empty slice (pennies of interest on a dormant joint account)
    # would filter every room to furniture.
    rows = DB.rows(
        "SELECT DISTINCT a.owner FROM accounts a "
        "WHERE a.owner IS NOT NULL AND a.owner NOT IN ('', $transit) "
        "AND a.root IN ('Assets', 'Liabilities') "
        "AND ((SELECT coalesce(sum(abs(b.value_home)), 0) FROM balances_daily b "
        "      WHERE b.account IN (SELECT account FROM accounts WHERE owner = a.owner AND root IN ('Assets', 'Liabilities')) "
        "      AND b.date = (SELECT max(date) FROM balances_daily)) >= 1 "
        "  OR (SELECT coalesce(sum(abs(p.amount_home)), 0) FROM postings p "
        "      WHERE p.account IN (SELECT account FROM accounts WHERE owner = a.owner AND root IN ('Assets', 'Liabilities')) "
        "      AND p.date >= (SELECT max(date) - INTERVAL 365 DAY FROM postings)) >= 100) "
        "ORDER BY a.owner",
        {"transit": OWNER_TRANSIT})
    names = [_s(r["owner"]) for r in rows]
    people = [n for n in names if n != "joint"]
    ordered = people + (["joint"] if "joint" in names else [])
    return [{"owner": n, "label": n[:1].upper() + n[1:]} for n in ordered]


def owner_clause(owner: str | None, col: str) -> tuple[str, dict[str, object]]:
    """WHERE fragment filtering `col` (an account column) by owner via the
    accounts dim. Person/joint views exclude transit by construction."""
    if not owner or owner == "all":
        return "", {}
    return (f" AND {col} IN (SELECT account FROM accounts WHERE owner = $owner)",
            {"owner": owner})


# ---------------------------------------------------------------- activity
@dataclass(frozen=True, slots=True)
class ActivityFilters:
    q: str | None = None
    amount_min: float | None = None
    amount_max: float | None = None
    category: str | None = None
    account: str | None = None
    owner: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    uncategorized_only: bool = False


def _activity_where(f: ActivityFilters) -> tuple[str, dict[str, object]]:
    where = "p.account LIKE 'Income:%' OR p.account LIKE 'Expenses:%'"
    where = f"({where})"
    params: dict[str, object] = {}
    if f.q:
        where += (" AND (p.payee ILIKE $q OR p.narration ILIKE $q)")
        params["q"] = f"%{f.q}%"
    if f.amount_min is not None:
        where += " AND abs(p.amount_home) >= $amt_min"
        params["amt_min"] = f.amount_min
    if f.amount_max is not None:
        where += " AND abs(p.amount_home) <= $amt_max"
        params["amt_max"] = f.amount_max
    if f.category:
        where += " AND (p.account = $cat OR p.account LIKE $cat_pfx)"
        params["cat"] = f.category
        params["cat_pfx"] = f.category + ":%"
    if f.account:
        where += " AND p.other_account = $acct"
        params["acct"] = f.account
    if f.date_from:
        where += " AND p.date >= $d0"
        params["d0"] = f.date_from
    if f.date_to:
        where += " AND p.date <= $d1"
        params["d1"] = f.date_to
    if f.uncategorized_only:
        where += " AND p.account IN ('Expenses:Uncategorized', 'Expenses:FIXME')"
    clause, extra = owner_clause(f.owner, "p.other_account")
    return where + clause, {**params, **extra}


def _cat_chip(account: str) -> str:
    """'Income:US:Salary:X' -> 'Salary · X' (country segment is plumbing)."""
    segs = account.split(":")[1:]
    if segs and len(segs[0]) == 2 and segs[0].isalpha() and segs[0].isupper():
        segs = segs[1:]
    return " · ".join(segs) or account


def _feed_row(r: Row) -> dict[str, object]:
    acct = _s(r["account"])
    uncat = acct in UNCAT_ACCOUNTS
    income = acct.startswith("Income")
    return {
        "id": int(_f(r["posting_id"])),
        "date": _iso(r["date"]),
        "day": _day_label(r["date"]),
        "payee": _s(r["payee"]).strip() or "(no payee)",
        "narration": _s(r["narration"]).strip(),
        "account": acct,
        "source_account": _s(r["other_account"]),
        "owner": _s(r["owner"]) or None,
        "category": "Uncategorized" if uncat else _cat_chip(acct),
        "amt": feed_money(_f(r["amount_home"]), income=income),
        "kind": "uncategorized" if uncat else ("income" if income else "expense"),
        "classifier": _s(r["classifier"]).strip(),
    }


SWEEP_TEXT = ("reinvest", "sweep")
SWEEP_MIN = 3  # fewer consecutive micro-rows than this stay individual


def _is_sweep(row: dict[str, object]) -> bool:
    """Broker plumbing noise: reinvested dividends and cash sweeps."""
    if row["kind"] != "income":
        return False
    blob = f"{row['payee']} {row['narration']} {row['account']}".lower()
    return any(w in blob for w in SWEEP_TEXT)


def _collapse_sweeps(rows: list[Row],
                     feed: list[dict[str, object]]) -> list[dict[str, object]]:
    """Fold runs of ≥SWEEP_MIN consecutive same-day sweep rows into one
    group entry with a server-summed total (the frontend never adds money).
    The underlying rows ride along for the expander."""
    out: list[dict[str, object]] = []
    i = 0
    while i < len(feed):
        j = i
        while (j < len(feed) and _is_sweep(feed[j])
               and feed[j]["date"] == feed[i]["date"] and _is_sweep(feed[i])):
            j += 1
        run = feed[i:j]
        if len(run) >= SWEEP_MIN:
            total = sum(_f(rows[k]["amount_home"]) for k in range(i, j))
            all_div = all("dividend" in str(r["account"]).lower()
                          or "reinvest" in f"{r['payee']} {r['narration']}".lower()
                          for r in run)
            noun = "reinvested dividends" if all_div else "sweep transactions"
            out.append({
                "kind": "sweep", "id": run[0]["id"],
                "date": run[0]["date"], "day": run[0]["day"],
                "label": f"{len(run)} {noun}",
                "amt": feed_money(total, income=True),
                "count": len(run), "rows": run,
            })
            i = j
        else:
            out.extend(run if run else [feed[i]])
            i = max(j, i + 1)
    return out


def activity_page(f: ActivityFilters, cursor: str | None,
                  limit: int = FEED_PAGE) -> dict[str, object]:
    """One keyset page of the feed, newest first. The cursor is the last
    row's '<iso-date>:<posting_id>'; matching totals ride the first page.
    Same-day runs of broker sweep noise arrive pre-folded."""
    where, params = _activity_where(f)
    limit = max(1, min(int(limit), 200))
    cursor_sql = ""
    cur = _cursor_parts(cursor)
    if cur:
        cursor_sql = (" AND (p.date < $cur_date OR "
                      "(p.date = $cur_date AND p.posting_id < $cur_id))")
        params = {**params, "cur_date": cur[0], "cur_id": cur[1]}
    sql = f"""
        SELECT p.posting_id, p.date, p.payee, p.narration, p.account,
               p.other_account, p.amount_home,
               json_extract_string(p.meta, '$.classifier') AS classifier,
               oa.owner AS owner
        FROM postings p
        LEFT JOIN accounts oa ON oa.account = p.other_account
        WHERE {where}{cursor_sql}
        ORDER BY p.date DESC, p.posting_id DESC
        LIMIT {limit + 1}
    """
    rows = DB.rows(sql, params)
    has_more = len(rows) > limit
    rows = rows[:limit]
    out: dict[str, object] = {
        "rows": _collapse_sweeps(rows, [_feed_row(r) for r in rows]),
        "cursor": (f"{_iso(rows[-1]['date'])}:{int(_f(rows[-1]['posting_id']))}"
                   if has_more and rows else None),
    }
    if not cursor:  # first page carries the filter's totals
        tot = DB.one(f"""
            SELECT count(*) AS n,
                   sum(CASE WHEN p.account LIKE 'Expenses:%' THEN p.amount_home END) AS spent,
                   sum(CASE WHEN p.account LIKE 'Income:%' THEN -p.amount_home END) AS received
            FROM postings p
            WHERE {where}
        """, params)
        out["matched"] = int(_f(tot["n"])) if tot else 0
        out["totals"] = {
            "spent": money0(_f(tot["spent"]) if tot else 0.0),
            "received": money0(_f(tot["received"]) if tot else 0.0),
        }
    return out


def activity_categories() -> list[dict[str, str]]:
    """Open Expenses/Income accounts — the teach picker's choices."""
    rows = DB.rows(
        "SELECT account FROM accounts WHERE is_open "
        "AND (account LIKE 'Expenses:%' OR account LIKE 'Income:%') "
        "AND account NOT IN ('Expenses:Uncategorized', 'Expenses:FIXME') "
        "ORDER BY account")
    return [{"account": _s(r["account"]),
             "label": _s(r["account"]).split(":", 1)[1].replace(":", " · ")}
            for r in rows]


def uncat_counts() -> dict[str, object]:
    row = DB.one(
        "SELECT count(*) AS n, coalesce(sum(amount_home), 0) AS v "
        "FROM postings WHERE account = 'Expenses:Uncategorized'")
    n = int(_f(row["n"])) if row else 0
    return {"count": n, "amount": money0(_f(row["v"]) if row else 0.0)}


# ---------------------------------------------------------------- register
def register(account: str, cursor: str | None, owner: str | None = None,
             limit: int = 80) -> dict[str, object]:
    """The statement view for one account: one human row per transaction
    (same-transaction legs merged), with the running balance the DB
    computed — never client-side."""
    limit = max(1, min(int(limit), 200))
    params: dict[str, object] = {"account": account}
    cursor_sql = ""
    cur = _cursor_parts(cursor)
    if cur:
        cursor_sql = (" AND (r.date < $cur_date OR "
                      "(r.date = $cur_date AND r.posting_id < $cur_id))")
        params["cur_date"] = cur[0]
        params["cur_id"] = cur[1]
    home = home_currency()
    rows = DB.rows(f"""
        SELECT r.posting_id, r.date, r.payee, r.narration, r.other_account,
               r.amount, r.currency, r.balance, r.amount_home
        FROM register r
        WHERE r.account = $account{cursor_sql}
        ORDER BY r.date DESC, r.posting_id DESC
        LIMIT {limit + 1}
    """, params)
    has_more = len(rows) > limit
    rows = rows[:limit]
    meta = DB.one(
        "SELECT a.owner, a.institution, a.is_open, a.open_date, "
        "       (SELECT count(*) FROM postings p WHERE p.account = a.account) AS n "
        "FROM accounts a WHERE a.account = $account", {"account": account})
    if meta is None:
        return {"account": account, "found": False, "rows": [], "cursor": None}
    bal_rows = DB.rows("""
        SELECT currency, units, value_home FROM balances_daily
        WHERE account = $account AND date = (SELECT max(date) FROM balances_daily)
          AND abs(units) > 1e-9
        ORDER BY abs(value_home) DESC NULLS LAST
    """, {"account": account})
    balances = [{
        "currency": _s(b["currency"]),
        "units": f"{_f(b['units']):,.4f}".rstrip("0").rstrip("."),
        "value": money0(_f(b["value_home"])) if b["value_home"] is not None else None,
    } for b in bal_rows]
    total = sum(_f(b["value_home"]) for b in bal_rows if b["value_home"] is not None)

    def _reg_row(r: Row) -> dict[str, object]:
        cur = _s(r["currency"])
        in_home = cur == home
        amt = _f(r["amount"])
        bal = _f(r["balance"])
        payee = _s(r["payee"]).strip()
        narration = _s(r["narration"]).strip()
        if not payee:  # a statement line with no payee: the note IS the name
            payee, narration = narration or "—", ""
        return {
            "id": int(_f(r["posting_id"])),
            "date": _iso(r["date"]),
            "day": _mon_d(r["date"]),
            "payee": payee,
            "narration": narration,
            "other": _s(r["other_account"]),
            "amt": money2(amt) if in_home else f"{amt:+,.4f}".rstrip("0").rstrip(".") + f" {cur}",
            "neg": amt < 0,
            "balance": money2(bal) if in_home else f"{bal:,.4f}".rstrip("0").rstrip(".") + f" {cur}",
        }

    def _merge_txn_rows(
            built: list[tuple[dict[str, object], str]]) -> list[dict[str, object]]:
        """Consecutive legs of the SAME transaction on this account (same
        date/payee/narration, DIFFERENT currency — a buy's cash leg + share
        leg) fold into one human row: amounts joined oldest-leg-first, the
        running balance from the newest leg (the balance after the whole
        transaction — rows arrive newest-first). Two identical same-day
        purchases share a currency and stay two rows."""
        merged: list[dict[str, object]] = []
        currencies: list[set[str]] = []
        for row, cur in built:
            prev = merged[-1] if merged else None
            if (prev and prev["date"] == row["date"]
                    and prev["payee"] == row["payee"]
                    and prev["narration"] == row["narration"]
                    and cur not in currencies[-1]):
                prev["amt"] = f"{row['amt']} · {prev['amt']}"
                prev["neg"] = bool(prev["neg"]) and bool(row["neg"])
                if not prev["other"]:
                    prev["other"] = row["other"]
                currencies[-1].add(cur)
                continue
            merged.append(dict(row))
            currencies.append({cur})
        return merged

    owner_val = _s(meta["owner"]) or None
    if owner and owner != "all" and owner_val != owner:
        # the lens is on and this account belongs to someone else: say so
        # rather than render another owner's statement inside their view
        return {"account": account, "found": True, "foreign_owner": owner_val,
                "rows": [], "cursor": None}
    return {
        "account": account,
        "found": True,
        "owner": owner_val,
        "institution": _s(meta["institution"]) or None,
        "is_open": bool(meta["is_open"]),
        "opened": _iso(meta["open_date"]) or None,
        "postings": int(_f(meta["n"])),
        "balance": money0(total),
        "balances": balances,
        "rows": _merge_txn_rows([(_reg_row(r), _s(r["currency"])) for r in rows]),
        "cursor": (f"{_iso(rows[-1]['date'])}:{int(_f(rows[-1]['posting_id']))}"
                   if has_more and rows else None),
    }


def account_list(owner: str | None = None) -> list[dict[str, object]]:
    """Assets/Liabilities accounts with their latest balance — palette rows
    and register jump targets."""
    clause, extra = owner_clause(owner, "a.account")
    rows = DB.rows(f"""
        SELECT a.account, a.owner, a.institution, a.is_open,
               coalesce(b.value, 0) AS value
        FROM accounts a
        LEFT JOIN (
            SELECT account, sum(value_home) AS value FROM balances_daily
            WHERE date = (SELECT max(date) FROM balances_daily)
            GROUP BY account
        ) b ON b.account = a.account
        WHERE (a.account LIKE 'Assets:%' OR a.account LIKE 'Liabilities:%')
          {clause}
        ORDER BY a.is_open DESC, abs(coalesce(b.value, 0)) DESC, a.account
    """, extra)
    return [{
        "account": _s(r["account"]),
        "owner": _s(r["owner"]) or None,
        "institution": _s(r["institution"]) or None,
        "is_open": bool(r["is_open"]),
        "balance": money0(_f(r["value"])),
    } for r in rows]


def txn_search(q: str, limit: int = 8) -> list[dict[str, object]]:
    """Palette live results: newest matches on payee/narration."""
    rows = DB.rows(f"""
        SELECT p.posting_id, p.date, p.payee, p.narration, p.account,
               p.other_account, p.amount_home,
               json_extract_string(p.meta, '$.classifier') AS classifier,
               oa.owner AS owner
        FROM postings p
        LEFT JOIN accounts oa ON oa.account = p.other_account
        WHERE (p.account LIKE 'Income:%' OR p.account LIKE 'Expenses:%')
          AND (p.payee ILIKE $q OR p.narration ILIKE $q)
        ORDER BY p.date DESC, p.posting_id DESC
        LIMIT {max(1, min(int(limit), 20))}
    """, {"q": f"%{q}%"})
    return [_feed_row(r) for r in rows]


# ------------------------------------------------------------ investments
def lots(today: date, owner: str | None = None) -> list[dict[str, object]]:
    """Per-lot holdings: surviving (account, symbol, cost_date, cost_number)
    groups under beancount booking, valued at the latest price on file."""
    clause, extra = owner_clause(owner, "p.account")
    home = home_currency()
    rows = DB.rows(f"""
        WITH lot AS (
            SELECT p.account, p.currency AS symbol, p.cost_date, p.cost_number,
                   sum(p.amount) AS units
            FROM postings p
            WHERE p.account LIKE 'Assets:%' AND p.currency != $home
              AND p.cost_number IS NOT NULL AND p.cost_currency = $home{clause}
            GROUP BY ALL HAVING abs(sum(p.amount)) > 1e-9
        ),
        latest AS (
            SELECT commodity, price, date FROM (
                SELECT commodity, price, date,
                       row_number() OVER (PARTITION BY commodity ORDER BY date DESC) AS rn
                FROM prices WHERE quote_currency = $home
            ) WHERE rn = 1
        )
        SELECT lot.*, latest.price, latest.date AS price_date
        FROM lot LEFT JOIN latest ON latest.commodity = lot.symbol
        ORDER BY lot.account, lot.symbol, lot.cost_date
    """, {"home": home, **extra})
    out: list[dict[str, object]] = []
    for r in rows:
        units = _f(r["units"])
        cost = _f(r["cost_number"])
        price = _f(r["price"]) if r["price"] is not None else None
        basis = units * cost
        value = units * price if price is not None else None
        gain = (value - basis) if value is not None else None
        acquired = r["cost_date"] if isinstance(r["cost_date"], date) else None
        long_term = acquired is not None and (today - acquired).days >= LT_DAYS
        out.append({
            "account": _s(r["account"]),
            "symbol": _s(r["symbol"]),
            "units": f"{units:,.4f}".rstrip("0").rstrip("."),
            "acquired": acquired.isoformat() if acquired else None,
            "acquired_lbl": (f"{MONTH_ABBR[acquired.month]} {acquired.day}, "
                             f"{acquired.year}") if acquired else "—",
            "term": "LT" if long_term else "ST",
            "basis": money0(basis),
            "value": money0(value) if value is not None else None,
            "valueN": round(value, 2) if value is not None else 0.0,
            "gain": delta0(gain) if gain is not None else None,
            "gainN": round(gain, 2) if gain is not None else None,
            "gain_cls": ("good" if gain is not None and gain >= 0 else
                         "bad" if gain is not None else ""),
            "gain_pct": (f"{100.0 * gain / basis:+.1f}%"
                         if gain is not None and basis else None),
        })
    return out


HARVEST_MIN = 250.0  # a loss smaller than this isn't worth the paperwork


def lots_verdict(rows: list[dict[str, object]]) -> str | None:
    """One glanceable sentence over the lots table: is there a tax loss
    worth harvesting? None when there are no lots (the empty state talks)."""
    if not rows:
        return None
    losses = sorted(float(str(r["gainN"])) for r in rows
                    if isinstance(r.get("gainN"), (int, float))
                    and float(str(r["gainN"])) < 0)
    if not losses:
        return "Nothing worth harvesting — every lot is sitting on a gain."
    worst = losses[0]
    if abs(worst) < HARVEST_MIN:
        return ("Nothing worth harvesting — the biggest unrealized loss is "
                f"{money0(abs(worst))}.")
    n = len(losses)
    return (f"{n} lot{'s' if n != 1 else ''} sit{'' if n != 1 else 's'} at a "
            f"loss — the biggest is {delta0(worst)}. Worth a harvesting look.")


def dividends_timeline(owner: str | None = None,
                       months: int = 24) -> dict[str, object]:
    """Dividend income by month (Income:*Dividend* postings, negated)."""
    clause, extra = owner_clause(owner, "p.other_account")
    rows = DB.rows(f"""
        SELECT date_trunc('month', p.date)::DATE AS month,
               sum(-p.amount_home) AS total, count(*) AS n
        FROM postings p
        WHERE p.account LIKE 'Income:%' AND p.account ILIKE '%dividend%'{clause}
        GROUP BY ALL ORDER BY month DESC LIMIT {max(6, min(months, 60))}
    """, extra)
    rows.reverse()
    ytd = DB.one(f"""
        SELECT coalesce(sum(-p.amount_home), 0) AS v, count(*) AS n
        FROM postings p
        WHERE p.account LIKE 'Income:%' AND p.account ILIKE '%dividend%'
          AND date_trunc('year', p.date) = date_trunc('year', current_date){clause}
    """, extra)
    return {
        "months": [{
            "month": _iso(r["month"])[:7],
            "label": f"{MONTH_ABBR[int(_iso(r['month'])[5:7])]} {_iso(r['month'])[2:4]}",
            "value": round(_f(r["total"]), 2),
            "amt": money0(_f(r["total"])),
            "n": int(_f(r["n"])),
        } for r in rows],
        "ytd": money0(_f(ytd["v"]) if ytd else 0.0),
        "ytd_count": int(_f(ytd["n"])) if ytd else 0,
    }


RETIREMENT_GROUPS: tuple[tuple[str, str, str], ...] = (
    # (limit key, display label, account-name regex)
    ("employer", "401(k) / 403(b)", "(401k|403b|457b)"),
    ("ira", "IRA (Trad + Roth)", "(ira)"),
)


def contribution_pace(year: int, owner: str | None = None) -> list[dict[str, object]]:
    """Net inflows YTD into each retirement bucket — compared upstream
    against the IRS limits from references/current-figures.md.

    The limits are PER PERSON, so when the ledger declares owners each
    (bucket, owner) gets its own row. Two flows never count: opening-balance
    seeds (Equity:* counter leg) and rollovers between retirement accounts —
    both are moves, not contributions."""
    clause, extra = owner_clause(owner, "p.account")
    out: list[dict[str, object]] = []
    for key, label, pattern in RETIREMENT_GROUPS:
        rows = DB.rows(f"""
            SELECT coalesce(a.owner, '') AS who,
                   coalesce(sum(p.amount_home), 0) AS v, count(*) AS n
            FROM postings p
            LEFT JOIN accounts a ON a.account = p.account
            WHERE p.account LIKE 'Assets:%'
              AND regexp_matches(lower(p.account), $pat){clause}
              AND date_trunc('year', p.date) = ($y || '-01-01')::DATE
              AND coalesce(p.other_account, '') NOT LIKE 'Equity:%'
              -- reinvested dividends/interest grow the account, but the IRS
              -- word "contribution" means new money from outside
              AND coalesce(p.other_account, '') NOT LIKE 'Income:%'
              AND NOT regexp_matches(lower(coalesce(p.other_account, '')),
                                     '(401k|403b|457b|ira)')
            GROUP BY 1 HAVING count(*) > 0
            ORDER BY 1
        """, {"pat": pattern, "y": str(year), **extra})
        for r in rows:
            who = _s(r["who"])
            v = _f(r["v"])
            if abs(v) < 0.005 and not int(_f(r["n"])):
                continue
            out.append({
                "key": key,
                "owner": who or None,
                "label": label + (f" · {who[:1].upper()}{who[1:]}" if who else ""),
                "contributedN": round(v, 2),
                "contributed": money0(v),
            })
    return out


def positions(owner: str | None = None) -> list[dict[str, object]]:
    """Holdings by symbol at the latest snapshot day — the DB twin of the
    positions table, owner-lens aware."""
    clause, extra = owner_clause(owner, "b.account")
    home = home_currency()
    rows = DB.rows(f"""
        SELECT b.currency AS symbol, sum(b.units) AS units,
               sum(b.value_home) AS value, max(p.date) AS price_date,
               any_value(p.price) AS price
        FROM balances_daily b
        LEFT JOIN (
            SELECT commodity, price, date FROM (
                SELECT commodity, price, date,
                       row_number() OVER (PARTITION BY commodity ORDER BY date DESC) AS rn
                FROM prices WHERE quote_currency = $home
            ) WHERE rn = 1
        ) p ON p.commodity = b.currency
        WHERE b.date = (SELECT max(date) FROM balances_daily)
          AND b.account LIKE 'Assets:%' AND b.currency != $home{clause}
        GROUP BY b.currency HAVING abs(sum(b.units)) > 1e-9
        ORDER BY abs(sum(b.value_home)) DESC NULLS LAST
    """, {"home": home, **extra})
    total = sum(_f(r["value"]) for r in rows if r["value"] is not None)
    out: list[dict[str, object]] = []
    for r in rows:
        value = _f(r["value"]) if r["value"] is not None else None
        price = _f(r["price"]) if r["price"] is not None else None
        out.append({
            "symbol": _s(r["symbol"]),
            "units": f"{_f(r['units']):,.4f}".rstrip("0").rstrip("."),
            "value": money0(value) if value is not None else None,
            "valueN": round(value, 2) if value is not None else 0.0,
            "price": f"${price:,.2f}" if price is not None else None,
            "price_date": _mon_d(r["price_date"]),
            "share": (f"{100.0 * value / total:.0f}%"
                      if value is not None and total else "—"),
        })
    return out


def month_span(months: int, end: str) -> list[str]:
    """Trailing month keys ending at `end` (YYYY-MM), for chart axes."""
    y, m = int(end[:4]), int(end[5:7])
    out: list[str] = []
    for _ in range(months):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def latest_day() -> str | None:
    row = DB.one("SELECT max(date) AS d FROM balances_daily")
    return _iso(row["d"]) or None if row else None


def week_ago(iso: str) -> str:
    return (date.fromisoformat(iso) - timedelta(days=7)).isoformat()
