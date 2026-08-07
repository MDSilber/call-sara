"""Shared vocabulary for the advisor layer — one name per recurring shape.

`Money` is the lever for the Decimal migration: every dollar amount in this
layer is annotated `Money`, so flipping the alias (stage 1d-B) lets the type
checker enumerate exactly what must change. Chart geometry stays `float`
and says so explicitly — those numbers position pixels, never dollars.
"""
from __future__ import annotations

from datetime import date
from typing import Any

Money = float
"""A dollar amount. float today; Decimal after the money-path migration."""

Row = dict[str, str]
"""One bean-query result row (csv.DictReader: every cell is a string)."""

Finding = dict[str, Any]
"""{"check", "severity", "title", "detail"} as produced by checks.finding()."""

YM = tuple[int, int]
"""(year, month)."""

Payload = dict[str, Any]
"""A JSON-bound dict headed for summary.json / the app — display strings for
money, floats only for chart geometry."""

__all__ = ["Finding", "Money", "Payload", "Row", "YM", "date"]
