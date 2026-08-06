#!/usr/bin/env python3
"""Generate reports/digest.html + digest.txt — Sara's weekly letter.

Run:    tools/run digest.py
Writes: reports/digest.html (email-safe: one 600px column, every style
        inline, no scripts, no webfonts, light-only — email clients strip
        <style> and ignore dark schemes) and reports/digest.txt (the
        plaintext twin for iMessage / plain mail). Nothing is SENT —
        delivery is the household's choice, made outside this tool.

The letter, not a dashboard: Sara's one-line verdict up top (home.py's
sara_line — the same builder the morning page trusts), then at most five
short beats — the week in money (in/out/net, window-labeled), anything
that needs a human this week, one delight (only when true), the autopilot
one-liner, and what Sara's watching next week. A 20-second phone read for
BOTH partners; a beat with nothing true to say is dropped, never padded.

Every figure comes from the verified builders (home.py / checks.py) or a
window-labeled ledger query in this file — the Iron Law applies: source,
window, whole dollars, true minus. Payees, findings text, and facts
bullets are bank-controlled DATA; the HTML renders through jinja2 with
autoescape on, and the text twin is built from raw strings only.
"""
import html as html_mod
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import jinja2

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import (OWNER_JOINT, REPORTS, account_owners, amount,  # noqa: E402
                   dated_bullets, household, owner_label, query)
from webview import latest_ledger_date  # noqa: E402
from checks import lane_status  # noqa: E402
from home import (Pace, delta0, m0, mon_d, monthly_expense_totals,  # noqa: E402
                  must_move, needs_you, saras_wins, sara_line, spend_pace,
                  under_streak, _auto_tile, _machine_ctx, _next_ctx,
                  _spend_tile)

WEEK_DAYS = 7          # the letter's window: the last 7 ledger-covered days
WATCH_HORIZON = 45     # "watching next week": nearest dated item this far out

# sara_line speaks for the home PAGE ("the Next line below", the rooms).
# The verdict logic is reused verbatim; only its on-page navigation phrases
# are re-aimed at the letter. An upstream wording change simply no-ops here.
_LETTER_PHRASES = [
    (re.compile(r"^First morning here — "), "First letter here — "),
    (re.compile(r"Start with the Next line below — the rest wait in Autopilot\."),
     "It's just below — the rest can wait."),
    (re.compile(r"Start with the Next line below\."), "It's just below."),
    (re.compile(r"they're waiting in Autopilot, none of it urgent\."),
     "they're on your home page, none of it urgent."),
    (re.compile(r"The Spending room tells it straight\."),
     "The week's numbers below tell it straight."),
]


def _letterize(verdict: str) -> str:
    for pat, repl in _LETTER_PHRASES:
        verdict = pat.sub(repl, verdict)
    return verdict


# ------------------------------------------------------------------- beats
def week_in_out(start: date, end: date) -> tuple[float, float]:
    """(money in, money out) over [start, end], both positive dollars.
    Income/Expenses roots only — transfers and card payments never touch
    either, so the pair reads as real in/out, not account shuffling."""
    rows = query(f"SELECT root(account,1) AS r, sum(convert(position,'USD')) AS v "
                 f"WHERE date >= {start.isoformat()} AND date <= {end.isoformat()} "
                 f"AND account ~ '^(Income|Expenses)' GROUP BY r")
    vals = {r["r"]: amount(r["v"]) for r in rows}
    return -vals.get("Income", 0.0), vals.get("Expenses", 0.0)


def _week_beat(today: date, asof: date | None, pace: Pace,
               streak: int) -> dict | None:
    """Beat 1 — the week in money. The window is the last WEEK_DAYS days the
    ledger actually covers (never 'this week' with silent gaps); the month
    pace verdict rides along as a second sentence, its own window named."""
    if asof is None:
        return None
    end = min(today, asof)
    start = end - timedelta(days=WEEK_DAYS - 1)
    inflow, outflow = week_in_out(start, end)
    # net of the ROUNDED pair, so the three displayed figures always agree
    # when a reader subtracts them (whole-dollar display drifts ≤ $1)
    net = round(inflow) - round(outflow)
    tile = _spend_tile(pace, streak)
    pace_line = ""
    if pace.typical is not None:
        pace_line = f"{tile['verdict']} for the month — {tile['fig']} ({tile['sub']})."
    return {"window": f"{mon_d(start)} – {mon_d(end)}",
            "inflow": m0(inflow), "outflow": m0(outflow),
            "net": delta0(net), "net_cls": "pos" if net >= 0 else "neg",
            "pace_line": pace_line}


def _plain(markup) -> str:
    """Markup -> plain text for the twin: strip tags, unescape entities."""
    return html_mod.unescape(re.sub(r"<[^>]+>", "", str(markup))).strip()


def _needs_owner(texts: list[str], lanes: list[dict]) -> str | None:
    """Whose hands a needs-item is for, from account `owner:` metadata:
    an account path in the item text maps directly; a lane NAME in the
    text maps through the lane's declared account (lane names already
    carry the person — the metadata makes it formal). Exactly one
    non-joint owner -> their display name; joint, several, or none ->
    None, and the beat stays addressed to the household."""
    owners = account_owners()
    if not owners:
        return None
    hits = {who for acct, who in owners.items()
            if any(acct in t for t in texts)}
    hits |= {owners[lane["account"]] for lane in lanes
             if lane["account"] in owners and lane["name"]
             and any(lane["name"] in t for t in texts)}
    hits.discard(OWNER_JOINT)
    return owner_label(next(iter(hits))) if len(hits) == 1 else None


def _needs_beat(nxt: dict, lanes: list[dict]) -> dict | None:
    """Beat 2 — the one thing that wants a human, straight from the home
    page's Next-line builder (same precedence: alert, then nearest dated
    obligation). Owner-specific items say whose hands (`who`). Quiet weeks
    drop the beat — the verdict already says so."""
    if nxt["quiet"]:
        return None
    text_plain = _plain(nxt["text"])
    return {"text": nxt["text"], "text_plain": text_plain,
            "meta": nxt["meta"],
            "who": _needs_owner([text_plain, nxt["meta"] or ""], lanes)}


def _delight_beat(today: date, totals: list, pace: Pace) -> dict | None:
    """Beat 3 — one delight, only when TRUE: realized finds this year
    (saras_wins parses only explicit `[x] … realized $N` lines), else a
    2+ month under-typical streak. Nothing true = no beat, never padding."""
    wins = saras_wins(today)
    if wins:
        top = wins["items"][0]
        peryr = "/yr" if top["peryr"] else ""
        return {"text": (f"Found money this year now totals "
                         f"{m0(wins['total'])} realized — the latest: "
                         f"{top['label']} ({m0(top['amt'])}{peryr}).")}
    streak = under_streak(totals, pace.cur)
    if streak >= 2:
        return {"text": (f"{streak} closed months in a row under your own "
                         f"typical. Quietly excellent — keep it boring.")}
    return None


def _auto_beat(lanes: list[dict]) -> dict | None:
    """Beat 4 — autopilot health in one line, the same _auto_tile the
    morning page shows. Nothing wired = nothing to report weekly."""
    tile = _auto_tile(_machine_ctx(lanes))
    if not tile["dots"]:
        return None
    return {"text": f"{tile['verdict']} — {tile['sub']}.", "cls": tile["cls"]}


def _watch_beat(today: date, already: str) -> dict | None:
    """Beat 5 — the nearest FUTURE dated item on the household calendar
    (facts/ dated bullets — the calendar every surface reads), so next
    week never arrives unannounced. The item the needs-you beat already
    carries is skipped — one obligation never fills two beats."""
    future = [(d, t) for d, t, _f in dated_bullets()
              if today < d <= today + timedelta(days=WATCH_HORIZON)
              and t.strip() != already]
    if not future:
        return None
    d, text = future[0]
    days = (d - today).days
    when = "tomorrow" if days == 1 else f"in {days} days"
    return {"day": mon_d(d), "when": when, "text": text}


# ---------------------------------------------------------------- letter
HTML_TEMPLATE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>Sara — your week</title>
</head>
<body style="margin:0;padding:0;background-color:#f4f3fa;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">{{ verdict }}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f4f3fa;">
<tr><td align="center" style="padding:28px 14px 36px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="width:100%;max-width:600px;background-color:#ffffff;border:1px solid #e7e4f4;border-radius:16px;overflow:hidden;box-shadow:0 10px 30px rgba(76,60,220,.10);">
<tr><td height="10" bgcolor="#6157ff" style="height:10px;font-size:0;line-height:0;background-color:#6157ff;background-image:linear-gradient(115deg,#6157ff 0%,#74c0fc 35%,#ff7eb6 68%,#ffb86b 100%);">&nbsp;</td></tr>
<tr><td style="padding:34px 40px 36px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
  <p style="margin:0;font-size:12.5px;line-height:1.5;color:#6e6c85;">Sara &middot; your week in money &middot; {{ stamp }}</p>
  <p style="margin:14px 0 0;font-size:21px;line-height:1.35;font-weight:600;color:#151329;">{{ greet }}</p>
  <p style="margin:12px 0 0;font-size:16.5px;line-height:1.6;color:#2c2a45;">{{ verdict }}</p>
{% if week %}
  <p style="margin:22px 0 0;font-size:15.5px;line-height:1.65;color:#4c4a63;">
    <strong style="font-weight:600;color:#151329;">The week in money.</strong>
    {{ week.inflow }} came in, {{ week.outflow }} went out &mdash;
    net <strong style="font-weight:600;color:{{ '#067647' if week.net_cls == 'pos' else '#d02b4c' }};">{{ week.net }}</strong>
    <span style="color:#6e6c85;">({{ week.window }})</span>.
    {% if week.pace_line %}{{ week.pace_line }}{% endif %}
  </p>
{% else %}
  <p style="margin:22px 0 0;font-size:15.5px;line-height:1.65;color:#4c4a63;">
    <strong style="font-weight:600;color:#151329;">The week in money.</strong>
    Nothing imported yet &mdash; the first statement starts the story.
  </p>
{% endif %}
{% if needs %}
  <p style="margin:18px 0 0;font-size:15.5px;line-height:1.65;color:#4c4a63;">
    <strong style="font-weight:600;color:#9a5b00;">One for {% if needs.who %}{{ needs.who }}&rsquo;s{% else %}your{% endif %} hands.</strong>
    {{ needs.text }}{% if needs.meta %} <span style="color:#6e6c85;">({{ needs.meta }})</span>{% endif %}
  </p>
{% endif %}
{% if delight %}
  <p style="margin:18px 0 0;font-size:15.5px;line-height:1.65;color:#4c4a63;">
    <strong style="font-weight:600;color:#067647;">A good thing.</strong>
    {{ delight.text }}
  </p>
{% endif %}
{% if auto %}
  <p style="margin:18px 0 0;font-size:15.5px;line-height:1.65;color:#4c4a63;">
    <strong style="font-weight:600;color:#151329;">Autopilot.</strong>
    {{ auto.text }}
  </p>
{% endif %}
{% if watch %}
  <p style="margin:18px 0 0;font-size:15.5px;line-height:1.65;color:#4c4a63;">
    <strong style="font-weight:600;color:#151329;">I&rsquo;m watching next week.</strong>
    {{ watch.day }} &mdash; {{ watch.text }} <span style="color:#6e6c85;">({{ watch.when }})</span>.
  </p>
{% endif %}
  <p style="margin:26px 0 0;font-size:16px;line-height:1.6;color:#151329;">&mdash;&nbsp;Sara</p>
  <p style="margin:28px 0 0;padding-top:16px;border-top:1px solid #eceaf4;font-size:12px;line-height:1.6;color:#6e6c85;">
    {{ ledger_stamp }} &middot; generated {{ gen_stamp }} &middot; the text twin is
    reports/digest.txt &middot; written by your vault, sent by no one.
  </p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""

_ENV = jinja2.Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)


def build_letter(now: datetime | None = None) -> tuple[str, str]:
    """(html, text) — both twins from ONE set of facts, gathered once."""
    now = now or datetime.now()
    today = now.date()
    asof = latest_ledger_date()
    totals = monthly_expense_totals()
    pace = spend_pace(today, asof, totals)
    if pace.through_day is None and totals:
        last_m = max(ym for ym, _ in totals)   # stale ledger: pace the
        if last_m < pace.cur:                  # latest month with data,
            pace = spend_pace(today, asof, totals, month=last_m)  # says so
    cards, more, needs_state = needs_you(today)
    moves = [mv for mv in must_move(today) if not mv["plumbing"]]
    verdict = _letterize(sara_line(pace, needs_state, cards, more, "week"))
    streak = under_streak(totals, pace.cur)

    lanes = lane_status(today)
    week = _week_beat(today, asof, pace, streak)
    needs = _needs_beat(_next_ctx(needs_state, cards, more, moves), lanes)
    delight = _delight_beat(today, totals, pace)
    auto = _auto_beat(lanes)
    watch = _watch_beat(today, needs["text_plain"] if needs else "")

    names = household("names")
    greet = f"Hi {names} —" if names else "Hi there —"
    ledger_stamp = f"Ledger through {mon_d(asof)}" if asof else "Ledger empty"
    html = _ENV.from_string(HTML_TEMPLATE).render(
        greet=greet, verdict=verdict,
        stamp=week["window"] if week else today.strftime("%B %-d"),
        week=week, needs=needs, delight=delight, auto=auto, watch=watch,
        ledger_stamp=ledger_stamp,
        gen_stamp=today.strftime("%a %b %-d"))

    lines = [f"{greet.rstrip(' —')} — it's Sara with your week.", "", verdict, ""]
    if week:
        lines.append(f"The week in money ({week['window']}): {week['inflow']} in, "
                     f"{week['outflow']} out — net {week['net']}.")
        if week["pace_line"]:
            lines.append(f"  {week['pace_line']}")
    else:
        lines.append("The week in money: nothing imported yet — the first "
                     "statement starts the story.")
    if needs:
        meta = f" ({needs['meta']})" if needs["meta"] else ""
        hands = f"{needs['who']}'s" if needs["who"] else "your"
        lines.append(f"One for {hands} hands: {needs['text_plain']}{meta}")
    if delight:
        lines.append(f"A good thing: {delight['text']}")
    if auto:
        lines.append(f"Autopilot: {auto['text']}")
    if watch:
        lines.append(f"Watching next week: {watch['day']} — {watch['text']} "
                     f"({watch['when']}).")
    lines += ["", "— Sara",
              (f"ledger through {mon_d(asof)}" if asof else "ledger empty")
              + f" · generated {today.strftime('%a %b %-d')}"]
    return html, "\n".join(lines) + "\n"


def digest() -> None:
    """Write reports/digest.{html,txt} (also runs in reports.py's loop)."""
    REPORTS.mkdir(exist_ok=True)
    html, text = build_letter()
    (REPORTS / "digest.html").write_text(html)
    (REPORTS / "digest.txt").write_text(text)


if __name__ == "__main__":
    digest()
    print(f"ok   digest -> {REPORTS / 'digest.html'} (+ digest.txt)")
