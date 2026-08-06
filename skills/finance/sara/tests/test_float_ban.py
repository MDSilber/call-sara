"""The float ban — money paths are Decimal, mechanically enforced.

Walks the AST of every module in sara/sources and sara/ledger and fails on
ANY appearance of the name `float`: calls, annotations, casts, defaults.
Statement money enters as strings and stays exact; the one permitted
crossing (Plaid's JSON floats) goes float -> str -> Decimal without ever
naming the type. If this test is in your way, the fix is Decimal, not an
exemption.
"""

from __future__ import annotations

import ast
from pathlib import Path

MONEY_PACKAGES = ("sara/sources", "sara/ledger")
PKG_ROOT = Path(__file__).resolve().parents[1]


def find_float_uses(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "float":
            hits.append(f"{path.relative_to(PKG_ROOT)}:{node.lineno} — name `float`")
        elif isinstance(node, ast.Attribute) and node.attr == "float":
            hits.append(f"{path.relative_to(PKG_ROOT)}:{node.lineno} — attribute `.float`")
        elif isinstance(node, ast.Constant) and isinstance(node.value, float):
            hits.append(f"{path.relative_to(PKG_ROOT)}:{node.lineno} — float literal "
                        f"{node.value!r}")
    return hits


def test_no_float_anywhere_in_the_money_paths() -> None:
    hits: list[str] = []
    for pkg in MONEY_PACKAGES:
        for path in sorted((PKG_ROOT / pkg).rglob("*.py")):
            hits.extend(find_float_uses(path))
    assert not hits, "float usage in money paths:\n  " + "\n  ".join(hits)


def test_the_ban_actually_bites() -> None:
    """Guard the guard: each pattern the walker claims to catch, it catches."""
    import textwrap

    probe = PKG_ROOT / "tests" / "_float_probe.py"
    probe.write_text(textwrap.dedent("""
        def f(x: float) -> float:
            return float(x) + 0.5
    """))
    try:
        hits = find_float_uses(probe)
        assert len(hits) == 4  # annotation, return type, call, literal
    finally:
        probe.unlink()
