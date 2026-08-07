# pyright: strict
"""Allocation vs the thesis: declared targets, the live mix, concentration.

The household declares its target mix once, in rules.toml:

    [allocation_targets]
    # encode what the THESIS actually states — targets are % of INVESTED
    # dollars above the reserve; cash and reserved dollars sit outside them
    reserve_usd = 200000        # fixed-dollar reserve, netted from cash first
    band_pts = 5                # default drift band, +/- points
    exclude_account_re = "529"  # accounts on their own glide path (optional)
    employer_stock = ["DUOL"]   # single-issuer positions worth naming (optional)

    [allocation_targets.classes]
    equity_us = 81              # class -> target %; classes sum to 100
    equity_intl = 9
    fixed_income = 10

    [allocation_targets.map]    # commodity -> class, where the ledger's
    VTSAX = "equity_us"         # asset_allocation_* metadata is missing;
    [allocation_targets.map.FFLDX]  # a table splits one fund across classes
    equity_us = 54
    equity_intl = 36
    fixed_income = 10

Commodities can instead carry fava's own convention in the ledger —
`asset_allocation_equity_us: 100` on the commodity directive — and those
percentages are read first; the rules map fills gaps and wins conflicts.
A metadata class maps onto a declared class by exact name, else by prefix
("equity_us" rolls up into a declared "equity"). The class names "cash" and
"other" are reserved: they are never drift-scored.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast

from sara.advisor.types import Money
from sara.vault import VAULT, amount, illiquid_currency_regex, query, rules

RESERVED_CLASSES = ("cash", "other")
DEFAULT_BAND_PTS = 5.0
CLASS_LABELS = {
    "equity_us": "US stocks", "equity_intl": "International stocks",
    "equity": "Stocks", "stocks": "Stocks",
    "fixed_income": "Fixed income", "bond_us": "US bonds", "bonds": "Bonds",
    "bond_intl": "International bonds", "cash": "Cash", "other": "Other",
    "real_estate": "Real estate", "reit": "REITs",
}

# fava's commodity-metadata convention: asset_allocation_<class>: <pct>
_COMMODITY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\s+commodity\s+([A-Z][\w.\-']*)"
                           r"((?:\n[ \t]+\S[^\n]*)*)", re.M)
_ALLOC_META_RE = re.compile(r"^[ \t]+asset_allocation_([a-z0-9_]+):\s*([\d.]+)", re.M)


def class_label(name: str) -> str:
    return CLASS_LABELS.get(name, name.replace("_", " ").capitalize())


def commodity_classes() -> dict[str, dict[str, float]]:
    """{symbol: {metadata class: pct}} from the ledger's commodity directives."""
    out: dict[str, dict[str, float]] = {}
    ledger_dir = VAULT / "ledger"
    if not ledger_dir.is_dir():
        return out
    for f in sorted(ledger_dir.rglob("*.beancount")):
        try:
            txt = f.read_text()
        except OSError:
            continue
        for m in _COMMODITY_RE.finditer(txt):
            split = {c: float(p) for c, p in _ALLOC_META_RE.findall(m.group(2))}
            if split:
                out[m.group(1)] = split
    return out


@dataclass(frozen=True)
class Targets:
    classes: dict[str, tuple[float, float]]   # name -> (target %, band pts)
    reserve_usd: Money
    exclude_account_re: str | None
    employer: list[str]
    cmap: dict[str, dict[str, float]]         # commodity -> {class: pct}


def declared_targets() -> Targets | None:
    """The parsed [allocation_targets] section, or None if never declared."""
    raw = rules().get("allocation_targets")
    if not isinstance(raw, dict):
        return None
    raw = cast("dict[str, Any]", raw)  # the rules.toml crossing: TOML tables are str-keyed
    band_default = float(raw.get("band_pts", DEFAULT_BAND_PTS))
    classes: dict[str, tuple[float, float]] = {}
    class_decls: dict[str, Any] = raw.get("classes") or {}
    for name, v in class_decls.items():
        if isinstance(v, dict):
            v = cast("dict[str, Any]", v)
            classes[name] = (float(v.get("target_pct", 0)),
                             float(v.get("band_pts", band_default)))
        else:
            try:
                classes[name] = (float(v), band_default)
            except (TypeError, ValueError):
                continue
    if not classes:
        return None
    cmap: dict[str, dict[str, float]] = {}
    map_decls: dict[str, Any] = raw.get("map") or {}
    for sym, v in map_decls.items():
        if isinstance(v, dict):
            v = cast("dict[str, Any]", v)
            split: dict[str, float] = {}
            for cls, pct in v.items():
                try:
                    split[str(cls)] = float(pct)
                except (TypeError, ValueError):
                    continue
            if split:
                cmap[sym] = split
        elif isinstance(v, str):
            cmap[sym] = {v: 100.0}
    excl = raw.get("exclude_account_re")
    employer_decl: list[Any] = raw.get("employer_stock", []) or []
    employer = [str(s) for s in employer_decl]
    return Targets(classes=classes, reserve_usd=float(raw.get("reserve_usd", 0)),
                   exclude_account_re=str(excl) if excl else None,
                   employer=employer, cmap=cmap)


def _roll_up(meta_class: str, declared: dict[str, tuple[float, float]]) -> str | None:
    """Map a metadata class name onto a declared class: exact, else prefix
    (metadata 'equity_us' rolls into a declared 'equity'), else a reserved
    name ('cash'/'other') survives as itself."""
    if meta_class in declared or meta_class in RESERVED_CLASSES:
        return meta_class
    hits = [c for c in declared if meta_class.startswith(c)]
    return max(hits, key=len) if hits else None


@dataclass(frozen=True)
class ClassRow:
    name: str
    label: str
    value: Money
    share_pct: float        # of invested dollars (target classes only)
    target_pct: float
    band_pts: float

    @property
    def drift_pts(self) -> float:
        return self.share_pct - self.target_pct

    @property
    def out_of_band(self) -> bool:
        return abs(self.drift_pts) > self.band_pts


@dataclass(frozen=True)
class AllocationView:
    rows: list[ClassRow]            # declared classes, in declaration order
    invested: Money                 # dollars the targets are scored over
    liquid_total: Money             # full liquid NW (concentration denominator)
    cash_total: Money               # cash + cash-classed funds, before reserve
    cash_above_reserve: Money
    reserve_usd: Money
    reserve_short: Money            # >0 when cash can't cover the reserve
    other_value: Money              # 'other'-classed holdings (never scored)
    unclassified: list[tuple[str, Money]]   # symbol, value — no class anywhere
    excluded_value: Money           # in accounts matched by exclude_account_re
    top: tuple[str, Money, float] | None       # symbol, value, % of liquid
    employer: tuple[str, Money, float] | None  # symbols, value, % of liquid
    stock_pct: float                # equity share of the target mix


def stock_share(classes: dict[str, tuple[float, float]]) -> float:
    """Equity share of the declared mix, normalized to the classes' own sum."""
    total = sum(t for t, _ in classes.values())
    if total <= 0:
        return 0.0
    stocks = sum(t for name, (t, _) in classes.items()
                 if name.startswith("equity") or "stock" in name)
    return 100.0 * stocks / total


def allocation_view() -> AllocationView | None:
    """Score the live portfolio against the declared targets. None until a
    [allocation_targets] section exists in rules.toml."""
    tg = declared_targets()
    if tg is None:
        return None
    cmap = dict(commodity_classes())
    cmap.update(tg.cmap)                      # rules map wins conflicts
    excl = illiquid_currency_regex()
    where = ("account ~ '^(Assets|Liabilities)'"
             + (f" AND NOT currency ~ '{excl}'" if excl else ""))
    rows = query(f"SELECT account, currency, sum(convert(position, 'USD')) AS v "
                 f"WHERE {where} GROUP BY account, currency")
    skip_re = re.compile(tg.exclude_account_re) if tg.exclude_account_re else None
    liquid_total = excluded = cash = other = 0.0
    class_vals: dict[str, Money] = {name: 0.0 for name in tg.classes}
    unclassified: dict[str, Money] = {}
    by_symbol: dict[str, Money] = {}
    for r in rows:
        v = amount(r["v"])
        if abs(v) < 0.005:
            continue
        liquid_total += v
        sym = r["currency"]
        if sym != "USD":
            by_symbol[sym] = by_symbol.get(sym, 0.0) + v
        if skip_re and skip_re.search(r["account"] or ""):
            excluded += v
            continue
        if sym == "USD":
            cash += v
            continue
        split = cmap.get(sym)
        if not split:
            unclassified[sym] = unclassified.get(sym, 0.0) + v
            continue
        total_pct = sum(split.values()) or 100.0
        for meta_class, pct in split.items():
            part = v * pct / total_pct
            cls = _roll_up(meta_class, tg.classes)
            if cls == "cash":
                cash += part
            elif cls in class_vals:
                class_vals[cls] += part
            else:                              # 'other' and unmapped splits
                other += part
    invested = sum(class_vals.values())
    out_rows = [ClassRow(name=name, label=class_label(name), value=val,
                         share_pct=(100.0 * val / invested) if invested else 0.0,
                         target_pct=tg.classes[name][0],
                         band_pts=tg.classes[name][1])
                for name, val in class_vals.items()]
    top = max(((s, v) for s, v in by_symbol.items() if v > 0),
              key=lambda kv: kv[1], default=None)
    emp_val = sum(v for s, v in by_symbol.items() if s in tg.employer and v > 0)
    return AllocationView(
        rows=out_rows, invested=invested, liquid_total=liquid_total,
        cash_total=cash, cash_above_reserve=max(0.0, cash - tg.reserve_usd),
        reserve_usd=tg.reserve_usd,
        reserve_short=max(0.0, tg.reserve_usd - cash), other_value=other,
        unclassified=sorted(unclassified.items(), key=lambda kv: -kv[1]),
        excluded_value=excluded,
        top=(top[0], top[1], 100.0 * top[1] / liquid_total)
        if top and liquid_total > 0 else None,
        employer=(" + ".join(s for s in tg.employer if by_symbol.get(s, 0) > 0),
                  emp_val, 100.0 * emp_val / liquid_total)
        if emp_val > 0 and liquid_total > 0 else None,
        stock_pct=stock_share(tg.classes))
