#!/usr/bin/env python3
"""Deterministic arithmetic — money math through code, never a model's head.

Usage:  tools/run calc.py "<expression>"     (plain `python3 tools/calc.py` works too)

Grammar (anything else is refused): number literals, + - * / % ** and unary
minus, parentheses, and min/max/round/abs. The expression is parsed with
ast.parse under a strict node allowlist — no names, no attributes, no
strings, never eval — and evaluated with decimal.Decimal, so 0.1 + 0.2 is
exactly 0.3. round() is half-up to the given decimal places (the money
convention, not Python's half-even builtin).

Prints the exact value on line 1, then a money-rounded rendering.

Examples:
    tools/run calc.py "0.1 + 0.2"                        # 0.3, $0.30
    tools/run calc.py "7300.50 / 12"                     # 608.375, $608.38
    tools/run calc.py "(8250 - 7789.55) / 7789.55 * 100" # % delta vs typical
    tools/run calc.py "min(6500, 0.06 * 91000)"          # the binding limit
    tools/run calc.py "1234.56 * (1 + 0.0435) ** 3"      # 3y compound growth

The MCP twin is `finance_calc` (danny-mcp src/calc.ts) — same grammar, same
refusal wording; keep them in sync.
"""
import ast
import sys
from collections.abc import Callable
from decimal import (ROUND_HALF_UP, Decimal, DecimalException, DivisionByZero,
                     getcontext)

PRECISION = 50          # significant digits — far beyond any money figure
MAX_EXPR = 1_000        # characters; also bounds parser recursion
REFUSED = ("refused: only numbers, + - * / % **, parentheses, and "
           "min/max/round/abs are allowed")


class CalcError(Exception):
    """A refused expression or a bad argument — message is user-facing."""


_BINOPS: dict[type[ast.operator], Callable[[Decimal, Decimal], Decimal]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}


def _describe(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return f"name {node.id!r}"
    if isinstance(node, ast.Attribute):
        return f"attribute access .{node.attr}"
    if isinstance(node, ast.Constant):
        return f"{type(node.value).__name__} literal"
    return type(node).__name__.lower()


def _literal(src: str, node: ast.Constant) -> Decimal:
    """Exact Decimal from the literal's own source text ('0.1' stays 0.1;
    a float round-trip would bake in binary error before math starts)."""
    if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
        raise CalcError(f"{REFUSED} (got: {_describe(node)})")
    text = (ast.get_source_segment(src, node) or repr(node.value)).replace("_", "")
    try:
        return Decimal(text)
    except DecimalException:                    # hex/octal/binary int literals
        return Decimal(repr(node.value))


def _round(args: list[Decimal]) -> Decimal:
    x, places = args[0], args[1] if len(args) == 2 else Decimal(0)
    if places != places.to_integral_value() or abs(places) > 30:
        raise CalcError("round() needs an integer number of decimal places (|n| <= 30)")
    return x.quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP)


def _call(src: str, node: ast.Call) -> Decimal:
    if not isinstance(node.func, ast.Name) or node.func.id not in ("min", "max", "round", "abs"):
        raise CalcError(f"{REFUSED} (got: call to {_describe(node.func)})")
    if node.keywords:
        raise CalcError(f"{REFUSED} (got: keyword argument)")
    name, args = node.func.id, [_eval(src, a) for a in node.args]
    if name in ("min", "max"):
        if not args:
            raise CalcError(f"{name}() needs at least one argument")
        return min(args) if name == "min" else max(args)
    if name == "abs":
        if len(args) != 1:
            raise CalcError("abs() takes exactly one argument")
        return abs(args[0])
    if len(args) not in (1, 2):
        raise CalcError("round() takes one or two arguments")
    return _round(args)


def _eval(src: str, node: ast.AST) -> Decimal:
    """Recursive walk over the allowlisted AST — anything unlisted refuses."""
    if isinstance(node, ast.Expression):
        return _eval(src, node.body)
    if isinstance(node, ast.Constant):
        return _literal(src, node)
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(src, node.left), _eval(src, node.right))
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _eval(src, node.operand)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.Call):
        return _call(src, node)
    raise CalcError(f"{REFUSED} (got: {_describe(node)})")


def evaluate(expression: str) -> Decimal:
    """Parse + evaluate under the allowlist; CalcError on anything refused."""
    if len(expression) > MAX_EXPR:
        raise CalcError(f"expression longer than {MAX_EXPR} characters")
    getcontext().prec = PRECISION
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as e:
        raise CalcError(f"{REFUSED} (got: unparseable expression)") from e
    try:
        return _eval(expression, tree)
    except RecursionError as e:
        raise CalcError(f"{REFUSED} (expression too deeply nested)") from e


def render_plain(d: Decimal) -> str:
    """Full-precision positional rendering, trailing zeros trimmed."""
    if not d.is_finite() or not -60 <= d.adjusted() <= 60:
        return str(d.normalize() if d.is_finite() else d)
    text = format(d, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def render_money(d: Decimal) -> str:
    """Cents, half-up, thousands separators: -1234.567 -> -$1,234.57."""
    try:
        cents = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except DecimalException:
        return "(too large for a money rendering)"
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents):,.2f}"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        result = evaluate(argv[0])
    except CalcError as e:
        print(f"calc: {e}", file=sys.stderr)
        return 2
    except DivisionByZero:
        print("calc: division by zero", file=sys.stderr)
        return 2
    except DecimalException:
        print("calc: not computable with decimal arithmetic (invalid operation or overflow)",
              file=sys.stderr)
        return 2
    print(render_plain(result))
    print(f"money: {render_money(result)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
