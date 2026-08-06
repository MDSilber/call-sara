"""Typed views over untrusted JSON/TOML values — parse, don't validate.

Config files and API payloads arrive as `Any`; narrowing them with bare
`isinstance` leaves pyright holding `dict[Unknown, Unknown]`. These three
helpers are the one sanctioned crossing: whatever the shape actually was,
the caller gets a well-typed (possibly empty) value and moves on. Money
never passes through here — amounts have their own Decimal-only crossings.
"""

from __future__ import annotations

from typing import Any, cast

__all__ = ["as_dict", "as_dicts", "as_list", "as_str"]


def as_dict(value: object) -> dict[str, Any]:
    """The value as {str: Any}, or {} when it isn't a mapping."""
    if isinstance(value, dict):
        return {str(k): v for k, v in cast("dict[Any, Any]", value).items()}
    return {}


def as_list(value: object) -> list[Any]:
    """The value as [Any], or [] when it isn't a list."""
    return list(cast("list[Any]", value)) if isinstance(value, list) else []


def as_dicts(value: object) -> list[dict[str, Any]]:
    """Every mapping in a list, each as {str: Any}; non-mappings dropped."""
    out: list[dict[str, Any]] = []
    for item in as_list(value):
        loose: Any = item
        if isinstance(item, dict):
            out.append(as_dict(loose))
    return out


def as_str(value: object, default: str = "") -> str:
    """The value as str; None and non-strings become the default."""
    return value if isinstance(value, str) else default
