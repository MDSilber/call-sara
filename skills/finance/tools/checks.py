#!/usr/bin/env python3
"""The planner's checks. Deterministic; each returns a list of findings.

A finding: {"check", "severity", "title", "detail"} — no side effects here.
severity: "alert" (act soon) | "watch" (note) | "info"
Run via run_checks.py, which writes reports/findings.md.
Nothing household-specific lives here: thresholds come from facts/goals,
tuning from rules.toml, dates from facts/, transactions from the ledger.
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
        for r in query(f"SELECT currency, sum(convert(position, '{shadow_currency()}')) AS v "
                       f"WHERE account ~ '^Assets' AND currency ~ '{excl}' GROUP BY currency"):
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
    for d, text, relpath in dated_bullets():
        days = (d - today).days
        if 0 <= days <= horizon:
            out.append(finding(
                "deadlines", "watch",
                f"In {days} days ({d.isoformat()}): {text}",
                f"From {relpath}. Confirm whether it needs an action or is informational."))
    return out


# ------------------------------------------------------------------ anomaly
def anomaly():
    """Large charges at payees you rarely use — the fraud / surprise signal."""
    floor_amt = goals().get("anomaly_min_amount") or 400
    tune = rules().get("anomaly", {})
    ignore_acct = tune.get("ignore_account_regex", "Cash|Uncategorized")
    ignore_payee = tune.get("ignore_payee_regex", r"^(ATM |WITHDRAWAL|CHECK )")
    rows = query("SELECT date, payee, number, account "
                 f"WHERE account ~ '^Expenses' AND NOT account ~ '{ignore_acct}' "
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
                f"{money(n)} at rarely-seen payee: {payee[:48]} ({r['date']})",
                f"Category {r['account'].replace('Expenses:', '')}. Seen "
                f"{freq[payee.upper()]}x ever. Confirm it's yours."))
    return out[-8:]  # cap noise


# ------------------------------------------------------------ review queue
def review_queue():
    """Uncategorized postings are unfinished work — the categorization backlog."""
    rows = query("SELECT count(*) AS n, sum(convert(position,'USD')) AS v "
                 "WHERE account = 'Expenses:Uncategorized'")
    n = int(float(rows[0]["n"])) if rows and rows[0].get("n") else 0
    if n == 0:
        return []
    v = amount(rows[0]["v"]) if rows[0].get("v") else 0.0
    sev = "alert" if n > 100 else ("watch" if n > 10 else "info")
    return [finding(
        "review-queue", sev,
        f"{n} uncategorized transactions ({money(v)}) awaiting rules",
        "Run `tools/run query.py uncategorized` for the top payees, add "
        "[[payee_rules]] to rules.toml, then `tools/run recategorize.py --write`.")]


# -------------------------------------------------------------------- goals
def goals_status():
    unset = [k for k, v in goals().items() if v is None]
    if not unset:
        return []
    return [finding(
        "goals", "info",
        f"{len(unset)} goal targets not yet set: {', '.join(unset)}",
        "Advisor proposes numbers once the spend/income baseline exists.")]


ALL = [concentration, deadlines, anomaly, review_queue, goals_status]


def run_all():
    findings, errors = [], []
    for fn in ALL:
        try:
            findings.extend(fn())
        except (Exception, SystemExit) as e:  # one broken check must not silence the rest
            errors.append(f"{fn.__name__}: {e}")
    return findings, errors
