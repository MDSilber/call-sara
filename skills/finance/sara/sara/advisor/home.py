#!/usr/bin/env python3
# pyright: strict
"""Generate reports/home.html — the one-viewport PRINT GLANCE.

Run:    tools/run home.py
Writes: reports/home.html — the ONLY file written; the vault is otherwise
        read-only. One self-contained page (no scripts, no webfonts, CSP
        default-src 'none') that opens from disk and prints on one sheet.

The rooms retired from this page (2026-08-07): Sara App is the live daily
driver and fava the drill-down, so the static page keeps only what a
fridge-door printout earns — the glance. Hero (greeting, Sara's one-line
verdict, freshness stamps), four verdict tiles (Spending pace, Net worth,
Autopilot, the 529), ONE "Next" line, footer. Ink identity: paper white,
ink black, a single rule weight — no gradients, no JS, dark scheme via the
OS preference only.

Every figure comes from tools/builders.py — the same builders the app
server and the weekly letter trust — so this page can never disagree with
them. Money honesty carries over: whole dollars, true minus, ≈ on derived
figures, the net-worth label says liquid + retirement (that is what the
headline counts), and the spending window names the day coverage is
complete through.

Security: payees, findings text, and lane names are bank-controlled DATA.
The page renders through jinja2 with autoescape on; the one intentional-
HTML value (the Next line's codespans) arrives as Markup built by escaping
first. The CSP meta makes zero-network a browser-enforced property.
"""
import contextlib
import re
from datetime import date, datetime

import jinja2
from markupsafe import Markup

from sara.vault import REPORTS, household
from sara.advisor.reports import liquid_balances, paper_value
from sara.advisor.webview import latest_ledger_date, networth_series
from sara.advisor.checks import goals as goals_config
from sara.advisor.checks import lane_status
# Re-exports — sara/server (assemble.py, live.py) imports these names from
# `home`; the implementations moved intact to builders.py. Keep this list a
# superset of what the server touches so its import path needs no edit.
from sara.advisor.builders import (  # noqa: F401
    MINUS,
    MONTH_FULL,
    SMALL_NUMS,
    Card,
    EduAccount,
    Pace,
    attribution_ctx,
    auto_tile,
    cheshbon_ctx,
    _codespans,
    education_ctx,
    machine_ctx,
    moneymap_ctx,
    networth_ctx,
    next_ctx,
    nw_chart_data,
    pace_chart_data,
    pace_ctx,
    sparkline,
    spend_tile,
    thesis_ctx,
    wins_ctx,
    delta0,
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
    window_label,
)

_ENV = jinja2.Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)

# The ink identity: paper, ink, one hairline weight, generous margins.
# Print is the first-class medium; the screen view is the same sheet.
CSS = """
:root { --paper:#ffffff; --ink:#111110; --ink2:#55534e; --faint:#8a877f;
  --rule:#d9d7d0; --good:#1a6b1a; --bad:#a52a2a; --warn:#8a6d00; }
@media screen and (prefers-color-scheme: dark) {
  :root { --paper:#141413; --ink:#f4f3ef; --ink2:#b8b5ac; --faint:#7d7a72;
    --rule:#3a3934; --good:#4fae4f; --bad:#d96c6c; --warn:#c9a227; } }
* { box-sizing:border-box; margin:0; }
html { -webkit-text-size-adjust:100%; }
body { background:var(--paper); color:var(--ink);
  font:15px/1.5 ui-serif, "Iowan Old Style", Georgia, serif; }
.sheet { max-width:820px; margin:0 auto; padding:44px 34px 30px; }
.brand { font-size:12px; letter-spacing:.14em; text-transform:uppercase;
  color:var(--faint); }
h1 { font-size:30px; font-weight:600; letter-spacing:-.01em; margin-top:10px; }
.verdict { font-size:18px; color:var(--ink); margin-top:6px; font-style:italic; }
.stamps { color:var(--faint); font-size:12.5px; margin-top:8px;
  font-family:ui-sans-serif, system-ui, sans-serif; }
.tiles { display:grid; grid-template-columns:repeat(4, 1fr); gap:0;
  border-top:1px solid var(--ink); border-bottom:1px solid var(--rule);
  margin-top:22px; }
.tile { padding:14px 14px 16px 0; border-left:1px solid var(--rule); padding-left:14px; }
.tile:first-child { border-left:none; padding-left:0; }
.tile .lab { font-size:11.5px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--faint); font-family:ui-sans-serif, system-ui, sans-serif; }
.tile .big { font-size:21px; font-weight:600; margin-top:6px; line-height:1.2;
  font-variant-numeric:tabular-nums; }
.tile .big.good { color:var(--good); } .tile .big.bad { color:var(--bad); }
.tile .big.warn { color:var(--warn); }
.tile .fig { font-size:13.5px; margin-top:4px; }
.tile .sub { color:var(--ink2); font-size:12.5px; margin-top:3px; line-height:1.45; }
.dots { margin-top:5px; font-size:12px; letter-spacing:.18em; }
.dots .ok { color:var(--good); } .dots .watch { color:var(--warn); }
.dots .bad { color:var(--bad); }
.next { margin-top:22px; padding:14px 0; border-bottom:1px solid var(--rule); }
.next .lab { font-size:11.5px; letter-spacing:.1em; text-transform:uppercase;
  color:var(--faint); font-family:ui-sans-serif, system-ui, sans-serif; }
.next .line { font-size:16.5px; margin-top:5px; }
.next .meta { color:var(--ink2); font-size:13px; margin-top:2px; }
.paper-note { color:var(--ink2); font-size:13px; margin-top:14px; }
code { font:0.92em ui-monospace, SFMono-Regular, Menlo, monospace; }
footer { margin-top:26px; color:var(--faint); font-size:12px; line-height:1.6;
  font-family:ui-sans-serif, system-ui, sans-serif; }
@media (max-width:640px) { .tiles { grid-template-columns:1fr 1fr; }
  .tile:nth-child(3) { border-left:none; padding-left:0; }
  .tile:nth-child(-n+2) { border-bottom:1px solid var(--rule); } }
@media print { body { background:#fff; color:#000; }
  .sheet { max-width:none; padding:0; } }
"""

PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="referrer" content="no-referrer">
<title>Sara — the glance</title>
<style>{{ css }}</style>
</head>
<body>
<div class="sheet">
<header>
  <p class="brand">Sara · the glance</p>
  <h1>{{ greet }}</h1>
  <p class="verdict">{{ sara }}</p>
  <p class="stamps">{{ ledger_stamp }}{{ checks_stamp }} · {{ stamp }}</p>
</header>
<section class="tiles">
  <div class="tile">
    <p class="lab">Spending</p>
    <p class="big{{ ' ' + g.spend.cls if g.spend.cls }}">{{ g.spend.verdict }}</p>
    {% if g.spend.fig %}<p class="fig">{{ g.spend.fig }}</p>{% endif %}
    <p class="sub">{{ g.spend.sub }}</p>
    {% if g.spend.streak %}<p class="sub">{{ g.spend.streak }}</p>{% endif %}
  </div>
  <div class="tile">
    <p class="lab">Net worth</p>
    <p class="big">{{ g.nw.v }}</p>
    {% if g.nw.chip %}<p class="fig">{{ g.nw.chip }}</p>{% endif %}
    <p class="sub">{{ g.nw.sub }}</p>
  </div>
  <div class="tile">
    <p class="lab">Autopilot</p>
    <p class="big{{ ' ' + g.auto.cls if g.auto.cls }}">{{ g.auto.verdict }}</p>
    {% if g.auto.dots %}<p class="dots" aria-label="{{ g.auto.aria }}">
      {%- for d in g.auto.dots %}<span class="{{ d }}">&#9679;</span>{% endfor %}</p>
    {% endif %}
    <p class="sub">{{ g.auto.sub }}</p>
  </div>
  <div class="tile">
    <p class="lab">{{ g.edu.label }}</p>
    <p class="big{{ ' ' + g.edu.cls if g.edu.cls }}">{{ g.edu.verdict or g.edu.fig }}</p>
    {% if g.edu.verdict and g.edu.fig %}<p class="fig">{{ g.edu.fig }}</p>{% endif %}
    <p class="sub">{{ g.edu.sub }}</p>
  </div>
</section>
<section class="next">
  <p class="lab">{{ nxt.label }}</p>
  <p class="line">{{ nxt.text }}</p>
  {% if nxt.meta %}<p class="meta">{{ nxt.meta }}</p>{% endif %}
</section>
{% if paper %}
<p class="paper-note">Illiquid paper ≈{{ paper }} stays out of every number on
this sheet, per the thesis.</p>
{% endif %}
<footer>
  <p>Decision support, not tax, investment, or legal advice. The live rooms are
  in Sara App; this sheet is the ten-second glance.</p>
</footer>
</div>
</body>
</html>
"""


def build_page(now: datetime | None = None) -> str:
    """The glance context, from the shared builders, rendered to one sheet."""
    now = now or datetime.now()
    today = now.date()
    balances, _unpriced = liquid_balances()
    liquid = sum(v for _, v in balances)
    paper = paper_value()
    asof = latest_ledger_date()
    totals = monthly_expense_totals()

    pace = spend_pace(today, asof, totals)
    if pace.through_day is None and totals:
        # stale ledger: nothing imported for the calendar month — pace the
        # latest month that has data instead, and say so
        last_m = max(ym for ym, _ in totals)
        if last_m < pace.cur:
            pace = spend_pace(today, asof, totals, month=last_m)
    series, baseline_cut = networth_series(liquid, asof)
    nw = networth_ctx(series, baseline_cut, liquid, asof)
    cards, more, needs_state = needs_you(today)
    moves = [mv for mv in must_move(today) if not mv["plumbing"]]
    edu_accounts = education_accounts()
    edu_pace = education_pace(edu_accounts) if edu_accounts else None
    edu = education_ctx(edu_accounts, edu_pace, goals_config(), today)
    mach = machine_ctx(lane_status(today))

    delta = nw["delta"]
    glance = {
        "spend": spend_tile(pace, under_streak(totals, pace.cur)),
        "nw": {"v": m0(liquid),
               # the delta chip, tags stripped — <b> is a screen affordance
               "chip": re.sub(r"<[^>]+>", "", str(delta["body"])) if delta else "",
               "sub": "liquid + retirement · " + nw["asof"]},
        "auto": auto_tile(mach),
        "edu": edu["tile"],
    }
    names = household("names")
    hour = now.hour
    daypart = "morning" if hour < 12 else ("afternoon" if hour < 18 else "evening")
    checks_stamp = ""
    if (cd := findings_date()):
        # a hand-mangled findings stamp must never crash the whole page
        with contextlib.suppress(ValueError):
            checks_stamp = f" · checks from {date.fromisoformat(cd).strftime('%b %-d')}"
    return _ENV.from_string(PAGE).render(
        css=Markup(CSS),
        greet=f"Good {daypart}, {names}" if names else f"Good {daypart}",
        sara=sara_line(pace, needs_state, cards, more, daypart),
        stamp=f"printed {today.strftime('%a %b %-d')}",
        ledger_stamp=(f"Ledger through {mon_d(asof)}" if asof
                      else "Ledger empty"),
        checks_stamp=checks_stamp,
        g=glance,
        nxt=_ink(next_ctx(needs_state, cards, more, moves)),
        paper=m0(paper) if paper else None)


def _ink(nxt: dict) -> dict:
    """Print polish: the meta line's backticks mark quoted bank text on
    screen surfaces; on paper they read as typos — quote plainly."""
    if nxt.get("meta"):
        nxt["meta"] = str(nxt["meta"]).replace("`", "'")
    return nxt


def home() -> None:
    """Write reports/home.html (also called from reports.py's report loop)."""
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "home.html").write_text(build_page())


if __name__ == "__main__":
    home()
    print(f"ok   home -> {REPORTS / 'home.html'}")
