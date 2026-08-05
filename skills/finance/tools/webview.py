#!/usr/bin/env python3
"""Generate reports/dashboard.html — the vault's static glanceable dashboard.

Run:    tools/run webview.py [forecast-days]      (default 60)
Writes: reports/dashboard.html — the ONLY file this tool writes; everything
        else is read-only. One fully self-contained page (inline CSS/SVG/JS,
        zero external requests) so it can be opened from disk and never
        phones home. fava (scripts/dashboard.sh) stays the drill-down; this
        is the beauty view (scripts/dashboard.sh --pretty).

Data surfaces reused, never re-derived: reports.liquid_balances/paper_value
(the not-counted illiquid convention), forecast.build_forecast (projections),
reports/findings.md (checks). History curve: month-end valuation at the
latest dated price on or before each month end, else cost basis — the
endpoint is the headline number itself (latest prices), so hero and curve
always agree.

Security: ledger payees, findings text, and account names are bank-
controlled DATA. Every string is HTML-escaped before it touches markup;
chart data rides in escaped data-* JSON attributes; the inline JS only ever
assigns via textContent. Optional five-dimension health scores are read from
reports/health.md (lines like `- Cash flow: 72`); absent that, the tiles
show account-group balances.
"""
import html
import json
import re
import sys
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vault import (REPORTS, VAULT, amount, illiquid_currency_regex,  # noqa: E402
                   money, query)
from reports import liquid_balances, paper_value  # noqa: E402
from forecast import build_forecast  # noqa: E402

MAX_CURVE_POINTS = 24      # two years of month-ends is a curve; more is wallpaper
SPEND_BAR_ROWS = 10        # categories shown as bars; the tail folds into Other
TREND_MONTHS = 6
PRICE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+price\s+(\S+)\s+([\d.,]+)\s+USD\b", re.M)
MONTH_ABBR = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def esc(s):
    return html.escape(str(s), quote=True)


def jattr(obj):
    """JSON for a data-* attribute — html.escape makes it inert in markup;
    the browser's attribute parser hands JSON.parse the exact original."""
    return esc(json.dumps(obj, separators=(",", ":")))


def compact(v):
    """$1.2M / $115K / $950 — axis-tick money."""
    a = abs(v)
    if a >= 1e6:
        s = f"${a / 1e6:.1f}M".replace(".0M", "M")
    elif a >= 1e4:
        s = f"${a / 1e3:.0f}K"
    elif a >= 1e3:
        s = f"${a / 1e3:.1f}K".replace(".0K", "K")
    else:
        s = f"${a:,.0f}"
    return "-" + s if v < 0 else s


def month_label(y, m):
    return f"{MONTH_ABBR[m]} {y}"


def nice_ticks(lo, hi, n=4):
    """~n clean tick values covering [lo, hi]."""
    span = (hi - lo) or 1.0
    raw = span / max(n, 1)
    mag = 10 ** len(str(int(abs(raw)))) / 10 if raw >= 1 else 1.0
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), 10 * mag)
    first = step * (lo // step)
    ticks = []
    t = first
    while t <= hi + step:
        if t >= lo - 1e-9:
            ticks.append(round(t, 6))
        t += step
    return ticks[:n + 2]


# ------------------------------------------------------------------- data
def latest_ledger_date():
    rows = query("SELECT max(date) AS d WHERE account ~ '^(Assets|Liabilities)'")
    try:
        return datetime.strptime(rows[0]["d"], "%Y-%m-%d").date()
    except (IndexError, KeyError, TypeError, ValueError):
        return None


def price_history():
    """{symbol: [(date, usd_price)…]} from explicit price directives."""
    prices = {}
    ledger_dir = VAULT / "ledger"
    if ledger_dir.is_dir():
        for f in sorted(ledger_dir.rglob("*.beancount")):
            try:
                txt = f.read_text()
            except OSError:
                continue
            for m in PRICE_RE.finditer(txt):
                prices.setdefault(m.group(2), []).append(
                    (date.fromisoformat(m.group(1)),
                     float(m.group(3).replace(",", ""))))
    return {c: sorted(v) for c, v in prices.items()}


def _units(cell, currency):
    """Sum every `<number> <currency>` run in a bean-query inventory cell
    (a cell can hold several lots: '340 AGGRX {29.00 USD}, 10 AGGRX {…}')."""
    total = 0.0
    for m in re.finditer(rf"(-?\d[\d,]*(?:\.\d+)?)\s+{re.escape(currency)}(?![.\w])",
                         cell or ""):
        total += float(m.group(1).replace(",", ""))
    return total


def networth_series(liquid_now, asof):
    """[(date, value)…] month-end liquid net worth. One ledger query: monthly
    deltas per currency in units + cost; valued at the latest dated price on
    or before each month end, else cost basis. The final point IS liquid_now
    (the headline, at latest prices) so hero and curve can never disagree."""
    excl = illiquid_currency_regex()
    where = ("account ~ '^(Assets|Liabilities)'"
             + (f" AND NOT currency ~ '{excl.replace(chr(39), chr(39) * 2)}'" if excl else ""))
    rows = query(f"SELECT year, month, currency, sum(position) AS units, "
                 f"sum(cost(position)) AS cost WHERE {where} "
                 f"GROUP BY year, month, currency ORDER BY year, month")
    per_month = {}
    for r in rows:
        try:
            ym = (int(r["year"]), int(r["month"]))
        except (TypeError, ValueError):
            continue
        cur = r["currency"]
        d_units = amount(r["units"], "USD") if cur == "USD" else _units(r["units"], cur)
        d_cost = amount(r["cost"], "USD")
        u, c = per_month.setdefault(ym, {}).setdefault(cur, [0.0, 0.0])
        per_month[ym][cur] = [u + d_units, c + d_cost]
    months = sorted(per_month)
    if not months:
        return []
    prices = price_history()
    cum = {}  # currency -> [units, cost]
    points = []
    for i, (y, m) in enumerate(months):
        for cur, (du, dc) in per_month[(y, m)].items():
            u, c = cum.setdefault(cur, [0.0, 0.0])
            cum[cur] = [u + du, c + dc]
        d = date(y, m, monthrange(y, m)[1])
        total = 0.0
        for cur, (u, c) in cum.items():
            if cur == "USD":
                total += u
            elif abs(u) > 1e-9:
                px = [p for pd, p in prices.get(cur, []) if pd <= d]
                total += u * px[-1] if px else c
        points.append((d, round(total, 2)))
    # the last month is partial (data reaches asof, not month end): date it
    # honestly and pin it to the headline valuation
    end_date = asof or points[-1][0]
    points[-1] = (min(points[-1][0], end_date) if asof else points[-1][0], liquid_now)
    return points[-MAX_CURVE_POINTS:]


def spend_data():
    """(latest-month bars, months trend, latest (y, m), month_to_date)."""
    rows = query("SELECT year, month, root(account, 2) AS cat, "
                 "sum(convert(position, 'USD')) AS amt "
                 "WHERE account ~ '^Expenses' GROUP BY year, month, cat "
                 "ORDER BY year, month")
    by_month = {}
    for r in rows:
        try:
            ym = (int(r["year"]), int(r["month"]))
        except (TypeError, ValueError):
            continue
        cat = (r["cat"] or "").replace("Expenses:", "") or "Uncategorized"
        by_month.setdefault(ym, {})[cat] = by_month.setdefault(ym, {}).get(cat, 0.0) \
            + amount(r["amt"])
    if not by_month:
        return [], [], None, False
    months = sorted(by_month)
    latest = months[-1]
    today = date.today()
    mtd = latest == (today.year, today.month)
    cats = sorted(by_month[latest].items(), key=lambda kv: -kv[1])
    cats = [(c, v) for c, v in cats if abs(v) >= 0.5]
    trend = [(ym, sum(by_month[ym].values())) for ym in months[-TREND_MONTHS:]]
    return cats, trend, latest, mtd


def health_scores():
    """Optional reports/health.md: `- Cash flow: 72` (or table rows) -> up to
    five (name, 0-100) tiles. Written by an assessment session, not by code."""
    path = REPORTS / "health.md"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        m = re.match(r"^[|\-*\s]*([A-Za-z][\w /&-]{2,28}?)\s*[:|]\s*(\d{1,3})\s*(?:/\s*100)?\s*\|?\s*$",
                     line)
        if m and 0 <= int(m.group(2)) <= 100:
            out.append((m.group(1).strip(), int(m.group(2))))
    return out[:5]


def account_groups(balances):
    """Fallback tiles: net USD by institution-ish path segment, top five."""
    groups = {}
    for acct, v in balances:
        parts = acct.split(":")
        key = parts[2] if len(parts) >= 3 else parts[-1]
        groups.setdefault(key, [0.0, 0])
        groups[key][0] += v
        groups[key][1] += 1
    top = sorted(groups.items(), key=lambda kv: -abs(kv[1][0]))[:5]
    return [(k, v, n) for k, (v, n) in top]


def parse_findings():
    """reports/findings.md -> (findings, counts_line, errors). Text is DATA."""
    path = REPORTS / "findings.md"
    if not path.exists():
        return None, "", []
    text = path.read_text()
    counts = re.search(r"^\*\*(.+?)\*\*$", text, re.M)
    errors = []
    err = re.search(r"^## Check errors\n(.*)", text, re.M | re.S)
    if err:
        errors = [l[2:].strip() for l in err.group(1).splitlines() if l.startswith("- ")]
        text = text[:err.start()]
    findings = []
    for block in re.split(r"^### ", text, flags=re.M)[1:]:
        lines = block.strip().splitlines()
        title = re.sub(r"^[^\w$~(]+", "", lines[0]).strip()  # drop the icon
        sev, check = "info", ""
        detail = []
        for line in lines[1:]:
            m = re.match(r"^_(.+?) · (alert|watch|info)_\s*$", line)
            if m:
                check, sev = m.group(1), m.group(2)
            elif line.strip():
                detail.append(line.strip())
        findings.append({"title": title, "severity": sev, "check": check,
                         "detail": " ".join(detail)})
    return findings, (counts.group(1) if counts else ""), errors


# ------------------------------------------------------------------ marks
def code_spans(escaped):
    """`x` -> <code>x</code>, applied strictly AFTER escaping."""
    return re.sub(r"`([^`]{1,80})`", r"<code>\1</code>", escaped)


def svg_line_chart(points):
    """Net-worth curve: 2px line, 10% area wash, end dot + ring, direct end
    label, hairline grid, crosshair+tooltip layer driven by data-points."""
    if len(points) < 2:
        return ("<div class='empty'>Not enough ledger history to draw a curve yet "
                "&mdash; import more statements and regenerate.</div>")
    W, H = 860, 280
    ML, MR, MT, MB = 64, 96, 18, 34
    xs = [d.toordinal() for d, _ in points]
    vs = [v for _, v in points]
    ticks = nice_ticks(min(vs), max(vs))
    lo, hi = min(ticks[0], min(vs)), max(ticks[-1], max(vs))
    span_x = (xs[-1] - xs[0]) or 1
    span_v = (hi - lo) or 1.0

    def X(o):
        return ML + (W - ML - MR) * (o - xs[0]) / span_x

    def Y(v):
        return MT + (H - MT - MB) * (1 - (v - lo) / span_v)

    grid, ylab = [], []
    for t in ticks:
        y = Y(t)
        grid.append(f"<line x1='{ML}' y1='{y:.1f}' x2='{W - MR}' y2='{y:.1f}' class='grid'/>")
        ylab.append(f"<text x='{ML - 8}' y='{y + 4:.1f}' class='tick' text-anchor='end'>{esc(compact(t))}</text>")
    step = max(1, round(len(points) / 6))
    xlab = []
    for i, (d, _) in enumerate(points):
        if i % step == 0 or i == len(points) - 1:
            anchor = "end" if i == len(points) - 1 else "middle"
            xlab.append(f"<text x='{X(xs[i]):.1f}' y='{H - 10}' class='tick' "
                        f"text-anchor='{anchor}'>{esc(MONTH_ABBR[d.month])}&#8202;"
                        f"{esc(str(d.year)[2:] if d.month == 1 or i == 0 else '')}</text>")
    pts = [(X(o), Y(v)) for o, v in zip(xs, vs)]
    path = "M" + " L".join(f"{x:.1f} {y:.1f}" for x, y in pts)
    area = path + f" L{pts[-1][0]:.1f} {H - MB} L{pts[0][0]:.1f} {H - MB} Z"
    ex, ey = pts[-1]
    payload = [{"x": round(x, 1), "y": round(y, 1),
                "l": month_label(d.year, d.month) + ("" if i < len(points) - 1 else " (as of)"),
                "v": money(v)}
               for i, ((x, y), (d, v)) in enumerate(zip(pts, points))]
    return f"""<figure class="chart">
<svg viewBox="0 0 {W} {H}" role="img" aria-label="Liquid net worth by month"
     class="nw" data-points="{jattr(payload)}" tabindex="0">
  {''.join(grid)}
  <line x1="{ML}" y1="{H - MB}" x2="{W - MR}" y2="{H - MB}" class="axis"/>
  {''.join(ylab)}{''.join(xlab)}
  <path d="{area}" class="wash"/>
  <path d="{path}" class="line1"/>
  <line class="xhair" x1="0" y1="{MT}" x2="0" y2="{H - MB}" visibility="hidden"/>
  <circle class="hoverdot" r="5" visibility="hidden"/>
  <circle cx="{ex:.1f}" cy="{ey:.1f}" r="5" class="dot1"/>
  <text x="{ex + 10:.1f}" y="{ey + 4:.1f}" class="endlab">{esc(money(vs[-1]))}</text>
</svg>
</figure>"""


def table_view(caption, headers, rows):
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    return (f"<details class='tv'><summary>View as table</summary>"
            f"<table><caption>{caption}</caption>"
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></details>")


def spend_bars(cats, month_lbl):
    if not cats:
        return "<div class='empty'>No categorized spending this period.</div>"
    total = sum(v for _, v in cats) or 1.0
    shown = cats[:SPEND_BAR_ROWS]
    tail = cats[SPEND_BAR_ROWS:]
    if tail:
        shown = shown + [(f"Other ({len(tail)})", sum(v for _, v in tail))]
    mx = max(abs(v) for _, v in shown) or 1.0
    rows = []
    for name, v in shown:
        w = max(0.6, 100.0 * abs(v) / mx)
        tip = f"{money(v)} {name} · {100 * v / total:.0f}% of {month_lbl}"
        rows.append(
            f"<div class='brow' tabindex='0' data-tip=\"{esc(tip)}\">"
            f"<span class='blab'>{esc(name)}</span>"
            f"<span class='btrack'><span class='bar' style='width:{w:.1f}%'></span></span>"
            f"<span class='bval'>{esc(money(v))}</span></div>")
    return "<div class='bars'>" + "".join(rows) + "</div>"


def trend_columns(trend):
    if len(trend) < 2:
        return "<div class='empty'>Not enough months yet for a trend.</div>"
    W, H, MB, MT = 340, 190, 26, 26
    n = len(trend)
    vs = [v for _, v in trend]
    mx = max(vs) or 1.0
    slot = (W - 16) / n
    bw = min(24.0, slot * 0.55)
    peak = vs.index(max(vs))
    cols, labs = [], []
    for i, ((y, m), v) in enumerate(trend):
        x = 8 + slot * i + (slot - bw) / 2
        h = max(2.0, (H - MB - MT) * v / mx)
        ytop = H - MB - h
        tip = f"{money(v)} {month_label(y, m)} total spend"
        cols.append(
            f"<g tabindex='0' data-tip=\"{esc(tip)}\">"
            f"<rect x='{x:.1f}' y='{ytop:.1f}' width='{bw:.1f}' height='{h:.1f}' rx='4' class='col2'/>"
            f"<rect x='{x:.1f}' y='{H - MB - min(4.0, h):.1f}' width='{bw:.1f}' height='{min(4.0, h):.1f}' class='col2'/>"
            + (f"<text x='{x + bw / 2:.1f}' y='{ytop - 6:.1f}' class='caplab' "
               f"text-anchor='middle'>{esc(compact(v))}</text>"
               if i in (peak, n - 1) else "")
            + "</g>")
        labs.append(f"<text x='{x + bw / 2:.1f}' y='{H - 8}' class='tick' "
                    f"text-anchor='middle'>{esc(MONTH_ABBR[m])}</text>")
    return (f"<figure class='chart'><svg viewBox='0 0 {W} {H}' role='img' "
            f"aria-label='Monthly spend, last {n} months'>"
            f"<line x1='8' y1='{H - MB}' x2='{W - 8}' y2='{H - MB}' class='axis'/>"
            + "".join(cols) + "".join(labs) + "</svg></figure>")


def sparkline(points, warn, floor):
    """Forecast mini-trend: de-emphasis line, min dot accented (status when
    warned). points: [(date, value)…] already sorted."""
    if len(points) < 2:
        return ""
    W, H, P = 220, 46, 6
    xs = [d.toordinal() for d, _ in points]
    vs = [v for _, v in points]
    lo, hi = min(vs + ([floor] if floor is not None else [])), max(vs)
    span_v = (hi - lo) or 1.0
    span_x = (xs[-1] - xs[0]) or 1

    def X(o):
        return P + (W - 2 * P) * (o - xs[0]) / span_x

    def Y(v):
        return P + (H - 2 * P) * (1 - (v - lo) / span_v)

    poly = " ".join(f"{X(o):.1f},{Y(v):.1f}" for o, v in zip(xs, vs))
    i_min = vs.index(min(vs))
    dot_cls = "dotwarn" if warn else "dot1"
    floor_line = ""
    if floor is not None and lo <= floor <= hi:
        fy = Y(floor)
        floor_line = f"<line x1='{P}' y1='{fy:.1f}' x2='{W - P}' y2='{fy:.1f}' class='floor'/>"
    return (f"<svg viewBox='0 0 {W} {H}' class='spark' aria-hidden='true'>{floor_line}"
            f"<polyline points='{poly}' class='sparkline'/>"
            f"<circle cx='{X(xs[i_min]):.1f}' cy='{Y(vs[i_min]):.1f}' r='4' class='{dot_cls}'/></svg>")


ICON_ALERT = ("<svg class='ic' viewBox='0 0 16 16' aria-hidden='true'><circle cx='8' cy='8' r='7' "
              "fill='var(--critical)'/><rect x='7.1' y='4' width='1.8' height='5.2' rx='.9' fill='#fff'/>"
              "<circle cx='8' cy='11.6' r='1.1' fill='#fff'/></svg>")
ICON_WATCH = ("<svg class='ic' viewBox='0 0 16 16' aria-hidden='true'><path d='M8 1.5 15 14H1Z' "
              "fill='var(--warning)'/><rect x='7.2' y='6' width='1.6' height='4' rx='.8' fill='#3b2d00'/>"
              "<circle cx='8' cy='11.8' r='1' fill='#3b2d00'/></svg>")
ICON_INFO = ("<svg class='ic' viewBox='0 0 16 16' aria-hidden='true'><circle cx='8' cy='8' r='7' "
             "fill='none' stroke='var(--muted)' stroke-width='1.5'/><rect x='7.2' y='7' width='1.6' "
             "height='4.4' rx='.8' fill='var(--muted)'/><circle cx='8' cy='4.9' r='1' fill='var(--muted)'/></svg>")


# ------------------------------------------------------------------- page
CSS = """
:root { color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink2:#52514e; --muted:#898781;
  --grid:#e1e0d9; --axis:#c3c2b7; --border:rgba(11,11,11,.10);
  --s1:#2a78d6; --s2:#eb6834; --s1-track:#cde2fb;
  --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
  --up:#006300; --down:#d03b3b; --code:#f0efec; }
@media screen and (prefers-color-scheme: dark) { :root { color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#ffffff; --ink2:#c3c2b7; --muted:#898781;
  --grid:#2c2c2a; --axis:#383835; --border:rgba(255,255,255,.10);
  --s1:#3987e5; --s2:#d95926; --s1-track:#184f95;
  --up:#0ca30c; --code:#262624; } }
* { box-sizing:border-box; margin:0; }
html { -webkit-text-size-adjust:100%; }
body { background:var(--page); color:var(--ink);
  font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }
.wrap { max-width:1120px; margin:0 auto; padding:34px 26px 40px; }
a { color:var(--s1); }
code { background:var(--code); border-radius:4px; padding:.08em .35em;
  font-size:.92em; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }

/* masthead */
.eyebrow { color:var(--ink2); font-size:13px; letter-spacing:.04em;
  text-transform:uppercase; }
.hero { font-size:56px; font-weight:650; letter-spacing:-.015em; line-height:1.1;
  margin:2px 0 4px; }
.hero-sub { color:var(--ink2); font-size:14px; }
.delta { font-weight:600; }
.delta.up { color:var(--up); } .delta.down { color:var(--down); }
.paper { color:var(--ink2); font-size:14px; margin-top:10px; padding-top:10px;
  border-top:1px solid var(--border); }
.paper strong { color:var(--ink); font-weight:600; }

/* layout */
section { margin-top:26px; }
.card { background:var(--surface); border:1px solid var(--border);
  border-radius:12px; padding:20px 22px; break-inside:avoid; }
.card h2 { font-size:15px; font-weight:650; margin-bottom:2px; }
.card .sub { color:var(--muted); font-size:12.5px; margin-bottom:14px; }
.tiles { display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); }
.tile { background:var(--surface); border:1px solid var(--border); border-radius:12px;
  padding:14px 16px; }
.tile .lab { color:var(--ink2); font-size:12.5px; }
.tile .val { font-size:22px; font-weight:650; margin-top:2px; }
.tile .n { color:var(--muted); font-size:12px; margin-top:2px; }
.meter { height:4px; border-radius:2px; background:var(--s1-track); margin-top:10px; }
.meter i { display:block; height:100%; border-radius:2px; background:var(--s1); }
.meter.warn i { background:var(--warning); } .meter.crit i { background:var(--critical); }
.cols { display:grid; gap:14px; grid-template-columns:3fr 2fr; align-items:stretch; }
@media (max-width:860px){ .cols { grid-template-columns:1fr; } .hero { font-size:44px; } }

/* charts */
.chart svg { width:100%; height:auto; display:block; }
.grid { stroke:var(--grid); stroke-width:1; }
.axis { stroke:var(--axis); stroke-width:1; }
.tick { fill:var(--muted); font-size:11px; font-variant-numeric:tabular-nums; }
.caplab { fill:var(--ink2); font-size:11px; font-weight:600; }
.endlab { fill:var(--ink); font-size:13px; font-weight:650; }
.wash { fill:var(--s1); opacity:.09; stroke:none; }
.line1 { fill:none; stroke:var(--s1); stroke-width:2; stroke-linecap:round;
  stroke-linejoin:round; }
.dot1 { fill:var(--s1); stroke:var(--surface); stroke-width:2; }
.dotwarn { fill:var(--critical); stroke:var(--surface); stroke-width:2; }
.col2 { fill:var(--s2); }
g[data-tip]:hover .col2, g[data-tip]:focus .col2 { filter:brightness(1.12); }
g[data-tip]:focus { outline:none; }
.xhair { stroke:var(--axis); stroke-width:1; }
.hoverdot { fill:var(--s1); stroke:var(--surface); stroke-width:2; }
svg.nw:focus { outline:none; }

/* horizontal bars */
.bars { display:grid; gap:8px; }
.brow { display:grid; grid-template-columns:150px 1fr 78px; gap:10px;
  align-items:center; }
.brow:hover .bar, .brow:focus .bar { filter:brightness(1.12); }
.brow:focus { outline:none; }
.blab { color:var(--ink2); font-size:13px; text-align:right; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap; }
.btrack { min-width:0; }
.bar { display:block; height:16px; background:var(--s2);
  border-radius:0 4px 4px 0; }
.bval { font-size:12.5px; font-variant-numeric:tabular-nums; color:var(--ink); }
@media (max-width:560px){ .brow { grid-template-columns:104px 1fr 70px; } }

/* forecast strip */
.fcgrid { display:grid; gap:12px; grid-template-columns:repeat(auto-fill,minmax(236px,1fr)); }
.fc { border:1px solid var(--border); border-radius:10px; padding:12px 14px; }
.fc .acct { font-size:13px; font-weight:650; }
.fc .asof { color:var(--muted); font-size:11.5px; }
.fc dl { display:grid; grid-template-columns:auto 1fr; gap:2px 10px; margin-top:8px;
  font-size:12.5px; }
.fc dt { color:var(--ink2); } .fc dd { text-align:right;
  font-variant-numeric:tabular-nums; }
.fc .min { font-weight:650; }
.sparkline { fill:none; stroke:var(--muted); stroke-width:2; stroke-linecap:round;
  stroke-linejoin:round; }
.spark { margin-top:6px; }
.floor { stroke:var(--serious); stroke-width:1; stroke-opacity:.7; }
.warncard { border-left:3px solid var(--critical); }
.banner { display:flex; gap:10px; align-items:flex-start; border:1px solid var(--border);
  border-left:3px solid var(--critical); border-radius:10px; padding:10px 14px;
  margin-bottom:12px; font-size:13.5px; }
.banner b { font-weight:650; }
.chips { display:flex; flex-wrap:wrap; gap:8px 18px; margin-bottom:14px;
  color:var(--ink2); font-size:13px; }
.chips b { color:var(--ink); font-variant-numeric:tabular-nums; font-weight:650; }

/* findings */
.finding { display:flex; gap:10px; align-items:flex-start; border:1px solid var(--border);
  border-radius:10px; padding:12px 14px; margin-top:10px; }
.finding.alert { border-left:3px solid var(--critical); }
.finding.watch { border-left:3px solid var(--warning); }
.finding .t { font-weight:650; font-size:13.5px; }
.finding .d { color:var(--ink2); font-size:13px; margin-top:2px; }
.finding .k { color:var(--muted); font-size:11.5px; text-transform:uppercase;
  letter-spacing:.05em; }
.ic { width:16px; height:16px; flex:none; margin-top:2px; }
.infolist { margin:10px 0 0; padding:0; list-style:none; }
.infolist li { display:flex; gap:8px; color:var(--ink2); font-size:13px;
  padding:4px 0; align-items:flex-start; }
.allclear { display:flex; gap:10px; align-items:center; color:var(--ink2);
  font-size:13.5px; margin-top:8px; }

/* tables + misc */
.tv { margin-top:10px; }
.tv summary { color:var(--muted); font-size:12.5px; cursor:pointer; }
.tv table { border-collapse:collapse; margin-top:8px; font-size:12.5px; width:100%; }
.tv caption { text-align:left; color:var(--muted); font-size:11.5px;
  margin-bottom:6px; }
.tv th { text-align:left; color:var(--ink2); font-weight:600;
  border-bottom:1px solid var(--axis); padding:4px 10px 4px 0; }
.tv td { border-bottom:1px solid var(--grid); padding:4px 10px 4px 0;
  font-variant-numeric:tabular-nums; }
.empty { color:var(--muted); font-size:13.5px; padding:18px 0; }
.tip { position:fixed; z-index:9; background:var(--surface); color:var(--ink);
  border:1px solid var(--border); border-radius:8px; padding:7px 10px;
  font-size:12.5px; pointer-events:none; box-shadow:0 4px 14px rgba(0,0,0,.13);
  white-space:pre-line; max-width:280px; }
.tip b { font-size:13px; }
footer { margin-top:30px; padding-top:14px; border-top:1px solid var(--border);
  color:var(--muted); font-size:12.5px; }
footer p + p { margin-top:4px; }
@media print {
  body { background:#fff; }
  .card, .tile { border-color:#ddd; box-shadow:none; }
  .tip { display:none; }
  .wrap { max-width:none; padding:0; }
}
"""

JS = """
(function () {
  var tip = document.getElementById('tip');
  function showTip(text, x, y) {
    tip.textContent = text;            // textContent only: vault text is DATA
    tip.hidden = false;
    var r = tip.getBoundingClientRect();
    var left = Math.min(x + 14, window.innerWidth - r.width - 8);
    var top = y - r.height - 12;
    if (top < 4) top = y + 16;
    tip.style.left = left + 'px';
    tip.style.top = top + 'px';
  }
  function hideTip() { tip.hidden = true; }

  // per-mark tooltips (bars, columns): the row/group is the hit target
  document.querySelectorAll('[data-tip]').forEach(function (el) {
    el.addEventListener('pointermove', function (e) {
      showTip(el.dataset.tip, e.clientX, e.clientY);
    });
    el.addEventListener('pointerleave', hideTip);
    el.addEventListener('focus', function () {
      var r = el.getBoundingClientRect();
      showTip(el.dataset.tip, r.left + r.width / 2, r.top);
    });
    el.addEventListener('blur', hideTip);
  });

  // net-worth curve: crosshair snaps to the nearest month
  var nw = document.querySelector('svg.nw');
  if (!nw) return;
  var pts = JSON.parse(nw.dataset.points || '[]');
  var xh = nw.querySelector('.xhair');
  var hd = nw.querySelector('.hoverdot');
  var idx = -1;
  function place(i, cx, cy) {
    if (i < 0 || i >= pts.length) return;
    idx = i;
    var p = pts[i];
    xh.setAttribute('x1', p.x); xh.setAttribute('x2', p.x);
    xh.removeAttribute('visibility');
    hd.setAttribute('cx', p.x); hd.setAttribute('cy', p.y);
    hd.removeAttribute('visibility');
    showTip(p.v + '\\n' + p.l, cx, cy);
  }
  function clientXY(i) {
    var r = nw.getBoundingClientRect();
    var vb = nw.viewBox.baseVal;
    return [r.left + (pts[i].x / vb.width) * r.width,
            r.top + (pts[i].y / vb.height) * r.height];
  }
  nw.addEventListener('pointermove', function (e) {
    var r = nw.getBoundingClientRect();
    var vb = nw.viewBox.baseVal;
    var vx = (e.clientX - r.left) / r.width * vb.width;
    var best = 0, bd = 1e9;
    for (var i = 0; i < pts.length; i++) {
      var d = Math.abs(pts[i].x - vx);
      if (d < bd) { bd = d; best = i; }
    }
    var c = clientXY(best);
    place(best, c[0], c[1] - 6);
  });
  function clear() {
    xh.setAttribute('visibility', 'hidden');
    hd.setAttribute('visibility', 'hidden');
    hideTip(); idx = -1;
  }
  nw.addEventListener('pointerleave', clear);
  nw.addEventListener('blur', clear);
  nw.addEventListener('focus', function () {
    var i = pts.length - 1, c = clientXY(i); place(i, c[0], c[1] - 6);
  });
  nw.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
      var i = idx < 0 ? pts.length - 1
            : Math.min(pts.length - 1, Math.max(0, idx + (e.key === 'ArrowRight' ? 1 : -1)));
      var c = clientXY(i); place(i, c[0], c[1] - 6);
      e.preventDefault();
    } else if (e.key === 'Escape') { clear(); }
  });
})();
"""


def acct_display(acct):
    parts = acct.split(":")
    return " · ".join(parts[2:]) if len(parts) > 3 else parts[-1]


def build_page():
    balances, unpriced = liquid_balances()
    liquid = sum(v for _, v in balances)
    paper = paper_value()
    asof = latest_ledger_date()
    series = networth_series(liquid, asof)
    cats, trend, latest_m, mtd = spend_data()
    fc = build_forecast(days=FORECAST_DAYS)
    findings, counts_line, check_errors = parse_findings()
    scores = health_scores()
    today = date.today()

    # ---- masthead
    delta_html = ""
    if len(series) >= 2:
        prev_d, prev_v = series[-2]
        d = liquid - prev_v
        if abs(d) >= 1:
            cls, arrow = ("up", "▲") if d > 0 else ("down", "▼")
            delta_html = (f" <span class='delta {cls}'>{arrow} {esc(money(abs(d)))}</span>"
                          f" <span>vs {esc(month_label(prev_d.year, prev_d.month))}</span> ·")
    asof_txt = f"ledger through {asof.isoformat()}" if asof else "no ledger history yet"
    paper_html = ""
    if paper:
        paper_html = (f"<p class='paper'>Illiquid paper wealth "
                      f"<strong>{esc(money(paper))}</strong> — not counted above "
                      f"(combined picture {esc(money(liquid + paper))})</p>")
    unpriced_html = ""
    if unpriced:
        unpriced_html = (f"<p class='paper'>{len(unpriced)} holding"
                         f"{'s' if len(unpriced) != 1 else ''} excluded — no USD price on "
                         f"file: " + ", ".join(f"<code>{esc(a)}</code>" for a, _ in unpriced[:4])
                         + ("…" if len(unpriced) > 4 else "") + "</p>")
    masthead = f"""<header>
  <p class="eyebrow">Household finances — liquid net worth</p>
  <p class="hero">{esc(money(liquid))}</p>
  <p class="hero-sub">{delta_html} {esc(asof_txt)}</p>
  {paper_html}{unpriced_html}
</header>"""

    # ---- tiles: health scores if an assessment wrote them, else groups
    tiles = []
    if scores:
        for name, sc in scores:
            sev = "" if sc >= 60 else (" warn" if sc >= 40 else " crit")
            tiles.append(f"<div class='tile'><div class='lab'>{esc(name)}</div>"
                         f"<div class='val'>{sc}<span class='n'> / 100</span></div>"
                         f"<div class='meter{sev}'><i style='width:{sc}%'></i></div></div>")
        tiles_sub = "Health score — from the latest assessment (reports/health.md)"
    else:
        for name, v, n in account_groups(balances):
            tiles.append(f"<div class='tile'><div class='lab'>{esc(name)}</div>"
                         f"<div class='val'>{esc(money(v))}</div>"
                         f"<div class='n'>{n} account{'s' if n != 1 else ''}</div></div>")
        tiles_sub = ""
    tiles_html = f"<section class='tiles'>{''.join(tiles)}</section>" if tiles else ""
    if tiles and tiles_sub:
        tiles_html = (f"<section><p class='sub' style='margin:0 0 8px;color:var(--muted);"
                      f"font-size:12.5px'>{esc(tiles_sub)}</p>"
                      f"<div class='tiles'>{''.join(tiles)}</div></section>")

    # ---- net worth curve
    nw_table = table_view(
        "Liquid net worth by month — market value where a dated price exists, "
        "else cost basis; the endpoint uses latest prices (the headline).",
        ["Month", "Liquid net worth"],
        [(esc(month_label(d.year, d.month)), esc(money(v))) for d, v in series])
    curve_card = f"""<section class="card">
  <h2>Net worth over time</h2>
  <p class="sub">Liquid only — illiquid paper stays out of this curve, same as the headline.</p>
  {svg_line_chart(series)}{nw_table if series else ''}
</section>"""

    # ---- spend
    if latest_m:
        month_lbl = month_label(*latest_m) + (" — month to date" if mtd else "")
    else:
        month_lbl = "No expense history yet"
    spend_total = sum(v for _, v in cats)
    spend_table = table_view(
        f"Spending by category, {esc(month_lbl)}.",
        ["Category", "Amount"],
        [(esc(c), esc(money(v))) for c, v in cats] + [("<b>Total</b>", f"<b>{esc(money(spend_total))}</b>")])
    trend_table = table_view(
        "Total spend by month.",
        ["Month", "Total spend"],
        [(esc(month_label(y, m)), esc(money(v))) for (y, m), v in trend])
    spend_cards = f"""<section class="cols">
  <div class="card">
    <h2>Spending — {esc(month_lbl)}</h2>
    <p class="sub">Expenses only; transfers and card payments never count. Total {esc(money(spend_total))}.</p>
    {spend_bars(cats, month_label(*latest_m) if latest_m else "")}
    {spend_table if cats else ''}
  </div>
  <div class="card">
    <h2>{TREND_MONTHS}-month trend</h2>
    <p class="sub">Total spend by month.</p>
    {trend_columns(trend)}
    {trend_table if len(trend) >= 2 else ''}
  </div>
</section>"""

    # ---- forecast strip
    h = fc["household"]
    banners = []
    for w in h["warns"]:
        what = (f"below <b>$0</b> around {esc(str(w['date']))} (~{esc(money(w['min']))})"
                if w["kind"] == "below_zero" else
                f"below its {esc(money(w['floor']))} floor around {esc(str(w['date']))} "
                f"(~{esc(money(w['min']))})")
        banners.append(f"<div class='banner'>{ICON_ALERT}<div><b>Cash crunch — "
                       f"{esc(acct_display(w['account']))}</b> projected {what}."
                       f"<div class='k' style='color:var(--muted);font-size:12px'>"
                       f"Driven by {esc(w['drivers'])}</div></div></div>")
    warn_accts = {w["account"] for w in h["warns"]}
    fcards = []
    for a in fc["accounts"]:
        pts = [(min(a["asof"] or fc["today"], fc["today"]), a["start"])]
        pts += [(f["date"], f["running"]) for f in a["flows"]]
        warn = a["account"] in warn_accts
        min_txt = ("no projected dip" if a["min"] >= a["start"]
                   else f"~{money(a['min'])} · {a['min_date'].strftime('%b %-d')}")
        floor_row = (f"<dt>floor</dt><dd>{esc(money(a['floor']))}</dd>"
                     if a["floor"] is not None else "")
        fcards.append(f"""<div class="fc{' warncard' if warn else ''}">
  <div class="acct">{esc(acct_display(a['account']))}</div>
  <div class="asof">ledger through {esc(str(a['asof'] or '—'))}</div>
  {sparkline(pts, warn, a['floor'])}
  <dl>
    <dt>start</dt><dd>{esc(money(a['start']))}</dd>
    <dt>projected min</dt><dd class="min">{esc(min_txt)}</dd>
    {floor_row}
    <dt>ends</dt><dd>~{esc(money(a['end_balance']))}</dd>
  </dl>
</div>""")
    fc_table = table_view(
        "Projected per-account path over the horizon (cadence-inferred flows only; "
        "irregular spend is not projected).",
        ["Account", "Start", "Projected minimum", "On", "Ends", "Floor"],
        [(esc(a["account"]), esc(money(a["start"])), "~" + esc(money(a["min"])),
          esc(str(a["min_date"])), "~" + esc(money(a["end_balance"])),
          esc(money(a["floor"])) if a["floor"] is not None else "—")
         for a in fc["accounts"]])
    chips = (f"<div class='chips'><span>income <b>~{esc(money(h['income']))}</b></span>"
             f"<span>expenses <b>~{esc(money(h['expense']))}</b></span>"
             f"<span>transfers out <b>~{esc(money(h['transfer_net']))}</b></span>"
             f"<span>one-offs <b>~{esc(money(h['oneoff_total']))}</b></span>"
             f"<span>uncommitted surplus <b>~{esc(money(h['surplus']))}</b></span></div>")
    fc_body = (chips + "".join(banners)
               + f"<div class='fcgrid'>{''.join(fcards)}</div>" + fc_table
               if fc["accounts"] else
               "<div class='empty'>No regular streams detected yet — a stream needs "
               "3+ occurrences on a steady rhythm. Import more history and regenerate.</div>")
    forecast_card = f"""<section class="card">
  <h2>Cash-flow forecast — next {FORECAST_DAYS} days</h2>
  <p class="sub">Every figure here is a <b>projection (~)</b> from cadence patterns and dated
  facts, not a statement. Irregular spend is not projected. {esc(str(fc['today']))} → {esc(str(fc['end']))}.</p>
  {fc_body}
</section>"""

    # ---- findings
    if findings is None:
        f_body = ("<div class='empty'>No findings file — run <code>tools/run run_checks.py</code> "
                  "to generate reports/findings.md.</div>")
    else:
        open_f = [f for f in findings if f["severity"] in ("alert", "watch")]
        info_f = [f for f in findings if f["severity"] == "info"]
        parts = []
        for f in open_f:
            icon = ICON_ALERT if f["severity"] == "alert" else ICON_WATCH
            parts.append(f"<div class='finding {f['severity']}'>{icon}<div>"
                         f"<div class='k'>{esc(f['severity'])} · {esc(f['check'])}</div>"
                         f"<div class='t'>{code_spans(esc(f['title']))}</div>"
                         f"<div class='d'>{code_spans(esc(f['detail']))}</div></div></div>")
        if not open_f:
            parts.append(f"<div class='allclear'><svg class='ic' viewBox='0 0 16 16' "
                         f"aria-hidden='true'><circle cx='8' cy='8' r='7' fill='var(--good)'/>"
                         f"<path d='M4.6 8.4 7 10.8l4.4-5' stroke='#fff' stroke-width='1.8' "
                         f"fill='none' stroke-linecap='round' stroke-linejoin='round'/></svg>"
                         f"Nothing on fire — no open alerts or watch items.</div>")
        if info_f:
            items = "".join(f"<li>{ICON_INFO}<span>{code_spans(esc(f['title']))}</span></li>"
                            for f in info_f)
            parts.append(f"<ul class='infolist'>{items}</ul>")
        for e in check_errors:
            parts.append(f"<div class='finding watch'>{ICON_WATCH}<div>"
                         f"<div class='k'>check error</div>"
                         f"<div class='d'>{code_spans(esc(e))}</div></div></div>")
        f_body = "".join(parts)
    findings_card = f"""<section class="card">
  <h2>Findings</h2>
  <p class="sub">{esc(counts_line) if counts_line else 'From reports/findings.md.'}</p>
  {f_body}
</section>"""

    # ---- footer
    footer = f"""<footer>
  <p>Decision support, not tax, investment, or legal advice. Figures as of
  {esc(asof.isoformat() if asof else '—')}; forecast figures are projections.</p>
  <p>Generated {esc(today.isoformat())} by tools/webview.py — regenerate with
  <code>tools/run webview.py</code>. Drill down interactively:
  <code>scripts/dashboard.sh</code> (fava).</p>
</footer>"""

    title = "Household finance dashboard"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{esc(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
{masthead}
{tiles_html}
{curve_card}
{spend_cards}
{forecast_card}
{findings_card}
{footer}
</div>
<div id="tip" class="tip" hidden></div>
<script>{JS}</script>
</body>
</html>
"""


FORECAST_DAYS = 60


def dashboard():
    """Write reports/dashboard.html (called from reports.py's report loop)."""
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "dashboard.html").write_text(build_page())


if __name__ == "__main__":
    if len(sys.argv) > 1:
        try:
            FORECAST_DAYS = int(sys.argv[1])
        except ValueError:
            sys.exit(__doc__)
    dashboard()
    print(f"ok   dashboard -> {REPORTS / 'dashboard.html'}")
