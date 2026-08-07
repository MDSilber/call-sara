#!/usr/bin/env python3
"""Emit reports/summary.json — the machine-readable twin of the generated reports.

Run:    tools/run summary.py         (also runs inside tools/run reports.py)
Writes: reports/summary.json

Every figure is ASSEMBLED from the same verified builders the human reports
use (reports/webview/home/checks/forecast) — never re-derived — so the JSON
can never disagree with the pages. Committed with the vault like the other
reports; remote read-only surfaces (integrations/cloudflare-mcp) serve it.
Window labels ride every figure group; consumers must show them.
"""
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from crosscheck import ensure_crosschecks  # noqa: E402
from vault import (OWNER_JOINT, OWNER_UNASSIGNED, REPORTS, VAULT, account_owners,  # noqa: E402
                   amount, dated_bullets, query, shadow_currency)
from reports import liquid_balances, owner_rollup, paper_value, spend_matrix  # noqa: E402
from webview import _units, latest_ledger_date, networth_series, parse_findings  # noqa: E402
from checks import goals as goals_config  # noqa: E402
from checks import lane_status  # noqa: E402
from forecast import DEFAULT_DAYS, build_forecast  # noqa: E402
from builders import (_auto_tile, _education_ctx, _machine_ctx, _networth_ctx,  # noqa: E402
                      _next_ctx, _spend_tile, education_accounts, education_pace,
                      findings_date, monthly_expense_totals, must_move, needs_you,
                      sara_line, spend_pace, under_streak, window_label)

SCHEMA = 1
CASHFLOW_MONTHS = 13          # trailing 12 closed months + the current one
TAG_RE = re.compile(r"<[^>]+>")


# ------------------------------------------------------------ sanitizers
def _plain(value):
    """Markup/str -> tag-free text (tile chips carry <b> for the page)."""
    return TAG_RE.sub("", str(value)) if value is not None else None


def _clean(obj):
    """Recursively make a builder's output JSON-safe: dates -> ISO strings,
    Markup -> plain text, tuples -> lists, money floats -> 2 decimals."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, float):
        return round(obj, 2)
    if isinstance(obj, str):
        return obj if type(obj) is str else _plain(obj)  # Markup subclasses str
    if obj is None or isinstance(obj, (bool, int)):
        return obj
    return _plain(obj)


def _ym(ym) -> str:
    return f"{ym[0]:04d}-{ym[1]:02d}"


# --------------------------------------------------------------- sections
def _networth(balances, unpriced, liquid, paper, asof, series):
    assets = sum(v for a, v in balances if a.startswith("Assets"))
    liab = sum(v for a, v in balances if a.startswith("Liabilities"))
    return {
        "window": f"through {asof.isoformat()}" if asof else "ledger empty",
        "liquid": liquid, "assets": assets, "liabilities": liab,
        "paper": paper or 0.0,
        "paper_currency": shadow_currency() if paper else None,
        "combined": liquid + (paper or 0.0),
        "unpriced_accounts": [{"account": a, "held": cell}
                              for a, cell in unpriced],
        "monthly_series": {
            "window": f"{len(series)} month-ends",
            "points": [{"date": p["d"], "value": p["v"],
                        "at_cost": p["est"]} for p in series],
        },
    }


OWNER_CONVENTION = ("owner: metadata on each account's open directive; "
                    f"'{OWNER_JOINT}' is shared, '{OWNER_UNASSIGNED}' means untagged")


def _owners(balances, asof):
    """The owner lens: per-owner liquid + account counts from the SAME
    balances rows as the headline (crosscheck holds the slice sum to liquid
    net worth to the cent). split_5050 is the two-person convenience view —
    joint split evenly — and rides only when exactly two people are tagged."""
    slices = owner_rollup(balances)
    window = f"through {asof.isoformat()}" if asof else "ledger empty"
    if not slices:
        return {"window": window, "convention": OWNER_CONVENTION,
                "owners": [], "split_5050": None}
    people = [(who, v) for who, v, _n in slices
              if who not in (OWNER_JOINT, OWNER_UNASSIGNED)]
    joint = next((v for who, v, _n in slices if who == OWNER_JOINT), 0.0)
    split = None
    if len(people) == 2:
        split = {"note": ("joint split evenly between the two people — "
                          "a display convention, not an agreement"),
                 "owners": [{"owner": who, "liquid": v + joint / 2}
                            for who, v in people]}
    return {"window": window, "convention": OWNER_CONVENTION,
            "owners": [{"owner": who, "liquid": v, "accounts": n}
                       for who, v, n in slices],
            "split_5050": split}


def _positions():
    """Same query as query.py's `positions` command, returned as data."""
    out = []
    for r in query("SELECT currency, sum(position) AS units, "
                   "sum(convert(position,'USD')) AS usd "
                   "WHERE account ~ '^Assets' AND currency != 'USD' "
                   "GROUP BY currency ORDER BY currency"):
        cell = r["usd"] or ""
        out.append({"symbol": r["currency"],
                    "units": _units(r["units"], r["currency"]),
                    "usd": amount(cell) if "USD" in cell else None})
    return out


def _spend(pace, tile, months, cats):
    prev = None
    if pace.cur in months and months.index(pace.cur) > 0:
        prev = months[months.index(pace.cur) - 1]
    elif months and months[-1] < pace.cur:
        prev = months[-1]
    per_month_totals = [round(sum(cats[c].get(m, 0.0) for c in cats), 2)
                        for m in months]
    through = (f"{_ym(pace.cur)} through day {pace.through_day}"
               if pace.through_day else "nothing imported yet")
    return {
        "current_month": {
            "month": _ym(pace.cur), "window": through,
            "fallback_latest_imported": pace.fallback,
            "spent": pace.spent, "typical": pace.typical,
            "typical_window": window_label(pace.typical_window) or None,
            "typical_by_now": pace.typical_by_now,
            "pace_delta": pace.pace_delta, "left_of_typical": pace.left,
            "verdict": tile["verdict"],
        },
        "last_closed_month": ({"month": _ym(prev),
                               "total": round(sum(cats[c].get(prev, 0.0)
                                                  for c in cats), 2)}
                              if prev else None),
        "monthly_by_category": {
            "window": window_label(months) or "no expense months yet",
            "months": [_ym(m) for m in months],
            "categories": {c: [round(cats[c].get(m, 0.0), 2) for m in months]
                           for c in sorted(cats,
                                           key=lambda c: -sum(cats[c].values()))},
            "totals": per_month_totals,
        },
    }


def _cashflow():
    """Income vs expenses by month — the same one-query shape as query.py's
    `cashflow` command and home's month_in_out, trailing window."""
    rows = query("SELECT year, month, root(account,1) AS r, "
                 "sum(convert(position,'USD')) AS v "
                 "WHERE account ~ '^(Income|Expenses)' "
                 "GROUP BY year, month, r ORDER BY year, month")
    by_month = {}
    for r in rows:
        try:
            ym = (int(r["year"]), int(r["month"]))
        except (TypeError, ValueError):
            continue
        by_month.setdefault(ym, {})[r["r"]] = amount(r["v"])
    months = sorted(by_month)[-CASHFLOW_MONTHS:]
    out = []
    for ym in months:
        inc = -by_month[ym].get("Income", 0.0)   # income posts negative
        exp = by_month[ym].get("Expenses", 0.0)
        out.append({"month": _ym(ym), "income": inc, "expenses": exp,
                    "net": inc - exp})
    return {"window": window_label(months) or "no activity yet",
            "months": out}


def _findings():
    findings, counts, errors = parse_findings()
    if findings is None:
        return {"window": "checks never ran", "generated": None,
                "counts": "", "items": [], "errors": []}
    gen = findings_date()
    return {"window": f"checks from {gen}" if gen else "checks on file",
            "generated": gen, "counts": counts,
            "items": [{"severity": f["severity"], "title": f["title"],
                       "check": f["check"], "detail": f["detail"]}
                      for f in findings],
            "errors": errors}


def _forecast(today):
    fc = build_forecast(today=today)
    h = fc["household"]
    return {
        "window": (f"{fc['today'].isoformat()} to {fc['end'].isoformat()} "
                   f"({DEFAULT_DAYS} days, projected)"),
        "household": {
            "start": h["start"], "income": h["income"],
            "expenses": h["expense"], "transfer_net": h["transfer_net"],
            "oneoff_total": h["oneoff_total"], "surplus": h["surplus"],
            "oneoffs": [{"date": o["date"], "amount": o["amount"],
                         "text": o["text"], "source": o["source"]}
                        for o in h["oneoffs"]],
            "warns": [{"account": w["account"], "kind": w["kind"],
                       "min": w["min"], "date": w["date"],
                       "floor": w["floor"], "drivers": w["drivers"]}
                      for w in h["warns"]],
        },
        "accounts": [{"account": a["account"], "start": a["start"],
                      "asof": a["asof"], "min": a["min"],
                      "min_date": a["min_date"],
                      "end_balance": a["end_balance"], "floor": a["floor"],
                      "projected_flows": len(a["flows"])}
                     for a in fc["accounts"]],
    }


def _autopilot(lanes):
    mach = _machine_ctx(lanes)
    return {
        "window": mach["window"], "summary": mach["summary"],
        "lanes": [{"name": r["name"], "kind": r["kind"],
                   "account": r["account"], "cadence": r["cadence"],
                   "status": r["status"], "last": r["last"],
                   "last_amount": r["last_amount"], "expected": r["expected"],
                   "balance": r["balance"], "floor": r["floor"],
                   "note": r["note"], "detail": ctx["detail"]}
                  for r, ctx in zip(lanes, mach["rows"])],
    }


def _education(accounts, pace, tile, goals, asof):
    target = goals.get("education_target")
    return {
        "window": f"through {asof.isoformat()}" if asof else "ledger empty",
        "accounts": [{"account": a.account, "kid": a.kid, "value": a.value,
                      "at_cost": a.at_cost} for a in accounts],
        "total": sum(a.value for a in accounts),
        "target": target if isinstance(target, (int, float)) else None,
        "contribution_pace_monthly": pace,
        "verdict": tile["verdict"] or None,
    }


def _goals_calendar(goals, today):
    bullets = dated_bullets()
    future = [{"date": d, "days_until": (d - today).days, "text": t,
               "source": str(f)} for d, t, f in bullets if d >= today]
    passed = [{"date": d, "text": t, "source": str(f)}
              for d, t, f in bullets if d < today][-5:]
    return {"config": dict(goals),
            "calendar": {"window": "every dated facts/ bullet",
                         "upcoming": future, "recently_passed": passed}}


def _thesis_rules():
    """THESIS.md's headline rules, grouped by `##` section: every top-level
    `- ` bullet (wrapped continuation lines joined); a bulletless section
    contributes its prose paragraph as one rule instead. Pure-italic lines
    (`_..._` — the unfilled template's placeholders) never count. Raw text
    is data."""
    path = VAULT / "THESIS.md"
    if not path.exists():
        return {"source": None, "sections": []}
    sections, heading, rules, prose = [], None, [], []

    def flush():
        if heading and (rules or prose):
            sections.append({"heading": heading,
                             "rules": rules.copy() or [" ".join(prose)]})

    for line in path.read_text().splitlines():
        stripped = line.strip()
        if line.startswith("## "):
            flush()
            heading, rules, prose = line[3:].strip(), [], []
        elif line.startswith("- "):
            rules.append(line[2:].strip())
        elif re.match(r"^\s{2,}(?!- )\S", line) and rules:
            rules[-1] += " " + stripped
        elif (heading and not rules and stripped and not line.startswith("#")
              and not (stripped.startswith("_") and stripped.endswith("_"))):
            prose.append(stripped)
    flush()
    return {"source": "THESIS.md", "sections": sections}


def _glance(pace, totals, lanes, edu_tile, nw_delta_plain, liquid, asof,
            today, daypart="morning"):
    cards, more, needs_state = needs_you(today)
    moves_human = [mv for mv in must_move(today) if not mv["plumbing"]]
    nxt = _next_ctx(needs_state, cards, more, moves_human)
    return {
        "sara": sara_line(pace, needs_state, cards, more, daypart),
        "spend": _spend_tile(pace, under_streak(totals, pace.cur)),
        "networth": {
            "value": liquid,
            "delta": nw_delta_plain,
            "window": (f"liquid + retirement · through {asof.isoformat()}" if asof
                       else "liquid + retirement · ledger empty"),
        },
        "autopilot": _auto_tile(_machine_ctx(lanes)),
        "education": edu_tile,
        "next": {"label": nxt["label"], "text": _plain(nxt["text"]),
                 "meta": _plain(nxt["meta"])},
    }


def _app_snapshot(now):
    """The Sara App read model (sara.server.assemble.app_snapshot) — the same
    verified builders, rendered once here so the app server never parses the
    ledger on a GET. Omitted (with a note) when the sara package predates the
    app schema; the app then asks for a report regeneration."""
    try:
        from sara.server import assemble
    except ImportError as e:
        print(f"note: summary.json app section skipped ({e})", file=sys.stderr)
        return None
    return assemble.app_snapshot(now)


# ------------------------------------------------------------------ build
def build_summary(now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    today = now.date()
    balances, unpriced = liquid_balances()
    liquid = sum(v for _, v in balances)
    paper = paper_value()
    asof = latest_ledger_date()
    goals = goals_config()
    totals = monthly_expense_totals()
    series, _cut = networth_series(liquid, asof)

    pace = spend_pace(today, asof, totals)
    if pace.through_day is None and totals:      # stale ledger: same fallback
        last_m = max(ym for ym, _ in totals)     # as home.build_page
        if last_m < pace.cur:
            pace = spend_pace(today, asof, totals, month=last_m)
    spend_tile = _spend_tile(pace, under_streak(totals, pace.cur))
    months, cats = spend_matrix()

    lanes = lane_status(today)
    edu_accounts = education_accounts()
    edu_pace = education_pace(edu_accounts) if edu_accounts else None
    edu_tile = _education_ctx(edu_accounts, edu_pace, goals, today)["tile"]

    nw_delta = _networth_ctx(series, _cut, liquid, asof)["delta"]
    nw_delta_plain = _plain(nw_delta["body"]) if nw_delta else None

    hour = now.hour
    daypart = "morning" if hour < 12 else ("afternoon" if hour < 18
                                           else "evening")
    cleaned = _clean({
        "schema": SCHEMA,
        "generated_at": now.isoformat(timespec="seconds"),
        "ledger_through": asof,
        "networth": _networth(balances, unpriced, liquid, paper, asof, series),
        "owners": _owners(balances, asof),
        "balances": {
            "window": f"through {asof.isoformat()}" if asof else "ledger empty",
            "accounts": [{"account": a, "usd": v,
                          "owner": account_owners().get(a)} for a, v in balances],
        },
        "positions": {
            "window": f"through {asof.isoformat()}" if asof else "ledger empty",
            "holdings": _positions(),
        },
        "spend": _spend(pace, spend_tile, months, cats),
        "cashflow": _cashflow(),
        "findings": _findings(),
        "forecast": _forecast(today),
        "autopilot": _autopilot(lanes),
        "education_529": _education(edu_accounts, edu_pace, edu_tile, goals,
                                    asof),
        "goals": _goals_calendar(goals, today),
        "thesis_rules": _thesis_rules(),
        "glance": _glance(pace, totals, lanes, edu_tile, nw_delta_plain,
                          liquid, asof, today, daypart),
        "app": _app_snapshot(now),
    })
    assert isinstance(cleaned, dict)  # _clean maps dict -> dict
    return cleaned


def summary() -> None:
    """Write reports/summary.json (also called from reports.py's loop)."""
    ensure_crosschecks()  # dual-computation gate — cached when reports.py already ran it
    REPORTS.mkdir(exist_ok=True)
    payload = json.dumps(build_summary(), indent=1, ensure_ascii=False)
    (REPORTS / "summary.json").write_text(payload + "\n")


if __name__ == "__main__":
    summary()
    print(f"ok   summary -> {REPORTS / 'summary.json'}")
