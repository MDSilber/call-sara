/**
 * Decimal-exact expression evaluator behind the finance_calc tool —
 * arithmetic through code, never a model's head.
 *
 * Grammar (anything else is refused): number literals, + - * / % ** and
 * unary minus, parentheses, and min/max/round/abs. A hand-written tokenizer
 * + recursive-descent parser — no eval, no Function, no dynamic code — with
 * decimal.js-light doing the arithmetic. Why decimal.js-light over a
 * hand-rolled scaled-BigInt: ~4 kB gzipped, zero deps, battle-tested — and
 * division/pow/rounding are exactly the code that should not be reinvented
 * inside a tool whose whole point is trustworthy arithmetic.
 *
 * round() is half-up to the given decimal places (the money convention).
 * Python precedence: -2**2 is -4; 2**-3 works; ** is right-associative.
 *
 * Mirror of call-sara's skills/finance/tools/calc.py — keep the grammar and
 * the refusal wording in sync with it.
 */
import Decimal from "decimal.js-light";

Decimal.config({ precision: 50 });

export class CalcError extends Error {}

const REFUSED =
  "refused: only numbers, + - * / % **, parentheses, and min/max/round/abs are allowed";
const MAX_EXPR = 1_000;
const MAX_RENDER_EXP = 60; // beyond ~10^60, positional rendering stops being useful
const FUNCS = new Set(["min", "max", "round", "abs"]);

type OpText = "+" | "-" | "*" | "/" | "%" | "**" | "(" | ")" | ",";
type Tok =
  | { kind: "num"; text: string }
  | { kind: "name"; text: string }
  | { kind: "op"; text: OpText }
  | { kind: "end" };

const NUM_RE = /^(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?\d+)?/;
const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*/;

function tokenize(src: string): Tok[] {
  const toks: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i] as string;
    if (/\s/.test(c)) {
      i += 1;
      continue;
    }
    const rest = src.slice(i);
    const num = NUM_RE.exec(rest);
    if (num) {
      toks.push({ kind: "num", text: num[0] });
      i += num[0].length;
      continue;
    }
    const name = NAME_RE.exec(rest);
    if (name) {
      toks.push({ kind: "name", text: name[0] });
      i += name[0].length;
      continue;
    }
    if (rest.startsWith("**")) {
      toks.push({ kind: "op", text: "**" });
      i += 2;
      continue;
    }
    if ("+-*/%(),".includes(c)) {
      toks.push({ kind: "op", text: c as OpText });
      i += 1;
      continue;
    }
    throw new CalcError(`${REFUSED} (got: character '${c}')`);
  }
  toks.push({ kind: "end" });
  return toks;
}

function literal(text: string): Decimal {
  let t = text.replace(/_/g, "");
  if (t.startsWith(".")) t = `0${t}`;
  if (t.endsWith(".")) t = `${t}0`;
  try {
    return new Decimal(t);
  } catch {
    throw new CalcError(`${REFUSED} (got: unparseable number '${text}')`);
  }
}

function describe(tok: Tok): string {
  if (tok.kind === "end") return "unexpected end of expression";
  if (tok.kind === "name") return `name '${tok.text}'`;
  return `'${tok.text}'`;
}

function roundHalfUp(x: Decimal, places: number): Decimal {
  if (places >= 0) return x.toDecimalPlaces(places, Decimal.ROUND_HALF_UP);
  const shift = new Decimal(10).toPower(-places);
  return x.div(shift).toDecimalPlaces(0, Decimal.ROUND_HALF_UP).mul(shift);
}

function applyFunc(name: string, args: Decimal[]): Decimal {
  if (name === "min" || name === "max") {
    if (args.length === 0) throw new CalcError(`${name}() needs at least one argument`);
    return args.reduce((a, b) => (name === "min" ? (b.lt(a) ? b : a) : b.gt(a) ? b : a));
  }
  if (name === "abs") {
    if (args.length !== 1) throw new CalcError("abs() takes exactly one argument");
    return (args[0] as Decimal).abs();
  }
  // round
  if (args.length < 1 || args.length > 2) {
    throw new CalcError("round() takes one or two arguments");
  }
  const x = args[0] as Decimal;
  const places = args.length === 2 ? (args[1] as Decimal) : new Decimal(0);
  if (!places.isInteger() || places.abs().gt(30)) {
    throw new CalcError("round() needs an integer number of decimal places (|n| <= 30)");
  }
  return roundHalfUp(x, places.toNumber());
}

class Parser {
  private pos = 0;

  constructor(private readonly toks: Tok[]) {}

  parse(): Decimal {
    const value = this.expr();
    const tail = this.peek();
    if (tail.kind !== "end") throw new CalcError(`${REFUSED} (got: trailing ${describe(tail)})`);
    return value;
  }

  private peek(): Tok {
    return this.toks[this.pos] ?? { kind: "end" };
  }

  private eatOp(op: OpText): boolean {
    const t = this.peek();
    if (t.kind === "op" && t.text === op) {
      this.pos += 1;
      return true;
    }
    return false;
  }

  private expr(): Decimal {
    let value = this.term();
    for (;;) {
      if (this.eatOp("+")) value = value.add(this.term());
      else if (this.eatOp("-")) value = value.sub(this.term());
      else return value;
    }
  }

  private term(): Decimal {
    let value = this.unary();
    for (;;) {
      if (this.eatOp("*")) value = value.mul(this.unary());
      else if (this.eatOp("/")) value = value.div(this.nonzero());
      else if (this.eatOp("%")) value = value.mod(this.nonzero());
      else return value;
    }
  }

  private nonzero(): Decimal {
    const value = this.unary();
    if (value.isZero()) throw new CalcError("division by zero");
    return value;
  }

  /** Unary +/- binds looser than ** on its left, exactly like Python. */
  private unary(): Decimal {
    if (this.eatOp("-")) return this.unary().neg();
    if (this.eatOp("+")) return this.unary();
    return this.power();
  }

  private power(): Decimal {
    const base = this.atom();
    if (!this.eatOp("**")) return base;
    return base.toPower(this.unary()); // right-assoc; exponent may be unary
  }

  private atom(): Decimal {
    const t = this.peek();
    this.pos += 1;
    if (t.kind === "num") return literal(t.text);
    if (t.kind === "name") return this.call(t.text);
    if (t.kind === "op" && t.text === "(") {
      const value = this.expr();
      if (!this.eatOp(")")) throw new CalcError(`${REFUSED} (got: unbalanced parenthesis)`);
      return value;
    }
    throw new CalcError(`${REFUSED} (got: ${describe(t)})`);
  }

  private call(name: string): Decimal {
    if (!FUNCS.has(name) || !this.eatOp("(")) {
      throw new CalcError(`${REFUSED} (got: name '${name}')`);
    }
    const args: Decimal[] = [];
    if (!this.eatOp(")")) {
      args.push(this.expr());
      while (this.eatOp(",")) args.push(this.expr());
      if (!this.eatOp(")")) throw new CalcError(`${REFUSED} (got: unbalanced parenthesis)`);
    }
    return applyFunc(name, args);
  }
}

/** Full-precision positional rendering, trailing zeros trimmed. */
export function renderPlain(d: Decimal): string {
  if (Math.abs(d.exponent()) > MAX_RENDER_EXP) return d.toExponential();
  let text = d.toFixed();
  if (text.includes(".")) text = text.replace(/0+$/, "").replace(/\.$/, "");
  return text === "" || text === "-0" ? "0" : text;
}

/** Cents, half-up, thousands separators: -1234.567 -> -$1,234.57. */
export function renderMoney(d: Decimal): string {
  if (Math.abs(d.exponent()) > MAX_RENDER_EXP) return "(too large for a money rendering)";
  const cents = roundHalfUp(d, 2);
  const fixed = cents.abs().toFixed(2);
  const dot = fixed.indexOf(".");
  const grouped = fixed.slice(0, dot).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${cents.lt(0) ? "-" : ""}$${grouped}${fixed.slice(dot)}`;
}

/**
 * Evaluate one expression under the strict grammar.
 * @throws CalcError with a user-facing message on anything refused.
 */
export function evaluate(expression: string): { exact: string; money: string } {
  if (expression.length > MAX_EXPR) {
    throw new CalcError(`expression longer than ${MAX_EXPR} characters`);
  }
  let result: Decimal;
  try {
    result = new Parser(tokenize(expression)).parse();
  } catch (err) {
    if (err instanceof CalcError) throw err;
    // decimal.js-light DecimalError: ln of a negative, exponent overflow, ...
    throw new CalcError("not computable with decimal arithmetic (invalid operation or overflow)");
  }
  return { exact: renderPlain(result), money: renderMoney(result) };
}
