"""The CQRS read path: summary.json + analytics.duckdb, nothing else.

GET endpoints never parse the ledger. The write side (tools/run reports.py,
ingest --write, the categorize action) materializes two artifacts and this
module serves them:

- ``reports/summary.json`` — the verified builders' output, extended with an
  ``app`` section (tools/summary.py) holding the room payloads exactly as the
  frontend renders them. Hot-reloaded on mtime/size change.
- ``reports/analytics.duckdb`` — the cross-checked SQL shadow (sara.analytics)
  for exploratory queries: search, pagination, drills, register, lots. The
  file is atomically replaced on rebuild, so the pool re-opens the root
  connection whenever the stat signature moves and hands each request its own
  cursor (the documented duckdb multi-thread pattern).

Money leaves this layer as display strings (true minus, whole dollars for
aggregates, cents on receipts); bare numbers appear only as chart geometry.
"""

from __future__ import annotations

import contextlib
import json
import re
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import duckdb

from sara.vault import VAULT

REPORTS = VAULT / "reports"
SUMMARY_PATH = REPORTS / "summary.json"
DB_PATH = REPORTS / "analytics.duckdb"

MINUS = "−"


class ReadModelMissing(RuntimeError):
    """A GET arrived before the write side materialized its artifact."""


# ---------------------------------------------------------------- money
def money0(v: Decimal | float | int) -> str:
    """Whole dollars with the true minus: -1234.56 -> 'MINUS$1,235'."""
    n = float(v)
    body = f"${abs(n):,.0f}"
    return MINUS + body if round(abs(n)) and n < 0 else body


def money2(v: Decimal | float | int) -> str:
    """Receipt-grade: cents when the value has them, whole dollars when not."""
    n = float(v)
    mag = abs(n)
    body = f"${mag:,.2f}" if abs(mag - round(mag)) >= 0.005 else f"${round(mag):,.0f}"
    return MINUS + body if n < 0 else body


def feed_money(v: Decimal | float, *, income: bool) -> str:
    """The Activity feed's convention: income leads with +, refunds on
    expense rows keep the true minus (same rule assemble._feed_money set)."""
    n = float(v)
    signed = -n if income else n
    mag = abs(signed)
    body = f"${mag:,.2f}" if abs(mag - round(mag)) >= 0.005 else f"${round(mag):,.0f}"
    if income:
        return ("+" if signed >= 0 else MINUS) + body
    return body if signed >= 0 else MINUS + body


def delta0(v: Decimal | float) -> str:
    n = float(v)
    return ("+" if n >= 0 else MINUS) + f"${abs(n):,.0f}"


# ------------------------------------------------------------- summary.json
@dataclass
class _Cached:
    sig: tuple[int, int]
    data: dict[str, Any]


class SummarySnapshot:
    """reports/summary.json, hot-reloaded on stat change."""

    def __init__(self, path: Path = SUMMARY_PATH) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._cached: _Cached | None = None

    def data(self) -> dict[str, Any]:
        try:
            st = self._path.stat()
        except OSError as e:
            raise ReadModelMissing(
                f"no {self._path.name} yet — run `tools/run reports.py` once "
                f"(the app serves reads from the generated reports)") from e
        sig = (st.st_mtime_ns, st.st_size)
        with self._lock:
            if self._cached is None or self._cached.sig != sig:
                raw: object = json.loads(self._path.read_text())
                if not isinstance(raw, dict):
                    raise ReadModelMissing(f"{self._path.name} is not a JSON object")
                self._cached = _Cached(sig, cast("dict[str, Any]", raw))
            return self._cached.data

    def app(self, room: str) -> dict[str, Any]:
        """The app section for one room; empty dict when the summary predates
        the app schema (the caller renders its own 'regenerate' notice)."""
        app = self.data().get("app")
        if isinstance(app, dict):
            payload = cast("dict[str, Any]", app).get(room)
            if isinstance(payload, dict):
                return cast("dict[str, Any]", payload)
        raise ReadModelMissing(
            "summary.json predates the app schema — run `tools/run reports.py` "
            "to regenerate it")


# --------------------------------------------------------- analytics.duckdb
Row = dict[str, Any]


class AnalyticsDB:
    """A refreshing read-only pool over reports/analytics.duckdb.

    One root connection per file version (stat signature); each request runs
    on its own cursor. The file is atomically replaced by rebuilds — a swap
    is detected on the next acquire and the root re-opens; a query that races
    the swap retries once on a fresh connection.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._root: duckdb.DuckDBPyConnection | None = None
        self._sig: tuple[int, int] | None = None

    def _signature(self) -> tuple[int, int]:
        try:
            st = self._path.stat()
        except OSError as e:
            raise ReadModelMissing(
                f"no {self._path.name} yet — run `tools/run reports.py` (or "
                f"`sara-analytics`) to build the analytics shadow") from e
        return (st.st_mtime_ns, st.st_size)

    def _acquire(self) -> duckdb.DuckDBPyConnection:
        sig = self._signature()
        with self._lock:
            if self._root is None or self._sig != sig:
                if self._root is not None:
                    with contextlib.suppress(duckdb.Error):
                        self._root.close()
                self._root = duckdb.connect(str(self._path), read_only=True)
                self._sig = sig
            return self._root.cursor()

    def rows(self, sql: str, params: dict[str, object] | None = None) -> list[Row]:
        """Run one parameterized SELECT, rows as dicts. Retries once when the
        underlying file was swapped mid-flight."""
        for attempt in (0, 1):
            cur = self._acquire()
            try:
                res = cur.execute(sql, params or {})
                cols = [str(d[0]) for d in (res.description or [])]
                fetched = cast("list[tuple[object, ...]]", res.fetchall())
                return [dict(zip(cols, r, strict=True)) for r in fetched]
            except duckdb.Error:
                with self._lock:
                    if self._root is not None:
                        with contextlib.suppress(duckdb.Error):
                            self._root.close()
                    self._root = None
                    self._sig = None
                if attempt:
                    raise
            finally:
                with contextlib.suppress(duckdb.Error):
                    cur.close()
        raise AssertionError("unreachable")

    def one(self, sql: str, params: dict[str, object] | None = None) -> Row | None:
        rows = self.rows(sql, params)
        return rows[0] if rows else None


# ------------------------------------------------------- shared instances
SUMMARY = SummarySnapshot()
DB = AnalyticsDB()


# ------------------------------------------------- references/current-figures
_FIGURE_ROW = re.compile(r"^\|\s*(?P<label>[^|]+?)\s*\|\s*\$(?P<amount>[\d,]+)")
_RETIREMENT_HEAD = re.compile(r"^##\s*Retirement contributions \((?P<year>\d{4})\)")


@dataclass(frozen=True, slots=True)
class ContributionLimits:
    year: str
    limits: dict[str, float]
    source: str


def contribution_limits(references_dir: Path) -> ContributionLimits | None:
    """The two headline limits the pace meters compare against, read from
    references/current-figures.md (the known keys only, year labelled).
    None when the file or the section is missing — the meter hides."""
    path = references_dir / "current-figures.md"
    try:
        text = path.read_text()
    except OSError:
        return None
    year: str | None = None
    limits: dict[str, float] = {}
    in_section = False
    for line in text.splitlines():
        head = _RETIREMENT_HEAD.match(line)
        if head:
            year, in_section = head.group("year"), True
            continue
        if line.startswith("## "):
            in_section = False
            continue
        if not in_section:
            continue
        m = _FIGURE_ROW.match(line)
        if not m:
            continue
        label = m.group("label").lower()
        amount = float(m.group("amount").replace(",", ""))
        if label.startswith("401(k)"):
            limits["employer"] = amount
        elif label.startswith("ira (traditional + roth"):
            limits["ira"] = amount
    if year is None or not limits:
        return None
    return ContributionLimits(year=year, limits=limits,
                              source="references/current-figures.md")
