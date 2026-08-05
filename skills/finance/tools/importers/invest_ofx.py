#!/usr/bin/env python3
"""Import brokerage ACTIVITY from an investment OFX/QFX (INVSTMTMSGSRS —
Vanguard's download format) into Beancount transactions.

Usage:
  invest_ofx.py <file.qfx> [ledger-account] [--write] [--all] [--dry-run] [--since YYYY-MM-DD]

This is the ACTIVITY half of the investment pipeline (holdings_ofx.py is the
positions/prices half). Accounts route by <ACCTID> last-4 via rules.toml
[[accounts]], exactly like ofx.py. Cash and units share the routed account —
the OFX cash/settlement sub-account IS the brokerage account. What each OFX
action becomes:

  BUYMF/BUYSTOCK     units {cost} acquiring a lot  +  negative USD cash leg
  SELLMF/SELLSTOCK   -units {} @ price  +  USD proceeds  +  Income:US:Gains
                     (auto-balanced; specific-lot detail needs the broker's
                     lot data at tax time — the {} books whole lots)
  REINVEST           units {cost}  +  Income:US:Dividends (INCOMETYPE DIV)
                     or Income:US:CapGainsDistributions (CGLONG/CGSHORT)
  INCOME             USD cash  +  Income:US:Dividends / :CapGainsDistributions
                     / :Interest by INCOMETYPE
  INVBANKTRAN        the bank-transaction path — contributions/withdrawals
                     categorize via rules.toml [[payee_rules]] (the TRANSFER
                     rule sends them to Assets:US:Transfers)

Unsupported action types (options, debt, journal entries, ...) are reported
and skipped, never fatal. Income/gains accounts that are not yet opened in
ledger/*.beancount are listed as paste-ready `open` directives on stderr.

DRY-RUN BY DEFAULT; --write appends to ledger/<year>.beancount and bean-check
rolls back a bad import. Dedupe is the bank importer's: (1) FITID-exact —
every entry stores the broker's FITID as `fitid:` metadata, the primary
identity; (2) hash-exact via `import-hash:` (date | signed cash cents |
action payee | account), honored only when the recorded fitids don't
disagree (identical same-day actions with different FITIDs BOTH import);
(3) the ±5-day fuzzy fallback, applied only to legacy ledger entries that
carry neither fitid nor hash. --all disables.

POSITIONS RECONCILE (the investment analog of bank balance continuity): the
statement's <INVPOSLIST> holds positions as of <DTASOF>. After parsing, the
ledger's units per commodity through that date plus this import's unit deltas
are compared per commodity and tagged MATCH / MISMATCH (UNVERIFIABLE when the
ledger can't be queried). A MISMATCH never blocks — positions predate the
vault everywhere — it means pre-import history is missing, and the report
suggests seeding a dated opening lot ("seed an opening position of N units").
MATCHed commodities get a dated `balance` assertion. Statement cash
(<AVAILCASH>) is NOT reconciled: at Vanguard the settlement fund is itself a
position, so AVAILCASH is unreliable.

BOOKING — how new costed lots coexist with snapshot-seeded units (verified
against beancount 3.2.3):
  * Existing vault accounts hold units from opening snapshots at NO cost
    (e.g. `Assets:US:Vanguard:Brokerage 7426.960 VTSAX` from
    Equity:Opening-Balances). Under the default STRICT booking, buys with
    {cost} land in the same account without any error — mixed costless +
    costed inventories are legal to AUGMENT. So imports of buys, reinvests,
    dividends, and transfers need NO open-directive change and NO migration.
  * SELLS are where booking bites. `-units {} @ price` books against costed
    lots only. STRICT accepts it while the account holds at most ONE costed
    lot of that commodity; the first sell facing several lots fails
    bean-check as "Ambiguous matches" (the --write rolls back). The fix is
    one token on that account's open directive:
        2020-01-01 open Assets:US:Vanguard:Brokerage "FIFO"
    FIFO books {} sells oldest-lot-first and the gain auto-computes. (NOT
    booking "NONE": under NONE a {} sell fails to interpolate at all —
    "Too many missing numbers".)
  * A sell can NEVER book against costless snapshot units ({} skips them —
    "Not enough lots"). If a sale dips into snapshot-seeded units, first
    convert the snapshot posting into a costed opening lot, e.g.
        Assets:US:Vanguard:Brokerage  7426.960 VTSAX {130.00 USD, 2024-01-01}
    with the basis from the broker's records ({0.00 USD} is a legal
    placeholder but overstates gains at sale). The MISMATCH suggestion
    prints the same seed recipe.
"""
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rules import categorize, route_by_acctid  # noqa: E402
from vault import VAULT, query  # noqa: E402
from importers.common import (append_to_ledger, assertion_date,  # noqa: E402
                              bank_transactions, escape, existing_ids,
                              existing_index, hash_is_duplicate, import_hash,
                              is_duplicate, parse_ofx_amount, read_ofx,
                              since_from_argv)

FLAGS = {"--all", "--write", "--dry-run"}
MATCH, MISMATCH, UNVERIFIABLE = "MATCH", "MISMATCH", "UNVERIFIABLE"

GAINS = "Income:US:Gains"
INCOME_ACCOUNTS = {
    "DIV": "Income:US:Dividends",
    "CGLONG": "Income:US:CapGainsDistributions",
    "CGSHORT": "Income:US:CapGainsDistributions",
    "INTEREST": "Income:US:Interest",
}
INCOME_DEFAULT = "Income:US:Other"

BUY_KINDS = ("BUYMF", "BUYSTOCK")
SELL_KINDS = ("SELLMF", "SELLSTOCK")
HANDLED = BUY_KINDS + SELL_KINDS + ("REINVEST", "INCOME", "INVBANKTRAN")
UNSUPPORTED = ("BUYDEBT", "BUYOPT", "BUYOTHER", "SELLDEBT", "SELLOPT",
               "SELLOTHER", "TRANSFER", "INVEXPENSE", "MARGININTEREST",
               "RETOFCAP", "SPLIT", "JRNLSEC", "JRNLFUND", "CLOSUREOPT")
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9'._-]*$")


def _tag(block, tag, default=""):
    m = re.search(rf"<{tag}>([^<\n]+)", block)
    return m.group(1).strip() if m else default


def _num(block, tag):
    """Numeric field, or None when absent OR malformed ('1.2.3', EU commas)
    — the caller's missing-field handling then reports-and-skips the row
    instead of importing a silently mangled amount."""
    m = re.search(rf"<{tag}>([-+\d.,]+)", block)
    if not m:
        return None
    val = parse_ofx_amount(m.group(1))
    if val is None:
        print(f";   malformed <{tag}> {m.group(1)!r} — treated as absent",
              file=sys.stderr)
    return val


def _date(block, tag):
    m = re.search(rf"<{tag}>(\d{{8}})", block)
    return datetime.strptime(m.group(1), "%Y%m%d").date() if m else None


def securities(text):
    """<SECLIST> UNIQUEID -> ticker (falls back to the UNIQUEID itself)."""
    out = {}
    for sec in re.findall(r"<SECINFO>(.*?)</SECINFO>", text, re.S):
        uid = _tag(sec, "UNIQUEID")
        if uid:
            out[uid] = _tag(sec, "TICKER") or uid
    return out


def invest_statements(text):
    """Yield (acctid, statement_text) per <INVSTMTRS> in the file."""
    for stmt in re.findall(r"<INVSTMTRS>(.*?)</INVSTMTRS>", text, re.S):
        yield _tag(stmt, "ACCTID"), stmt


def actions(stmt, sec):
    """Yield one dict per supported action in a statement's <INVTRANLIST>,
    plus a summary of skipped rows: ({kind,date,ticker,units,price,total,
    payee} | {kind:'INVBANKTRAN', txn}, ...). Malformed rows are reported,
    never fatal."""
    skipped = []
    for kind, block in re.findall(
            rf"<({'|'.join(HANDLED)})>(.*?)</\1>", stmt, re.S):
        if kind == "INVBANKTRAN":
            # the embedded <STMTTRN> lacks the </BANKTRANLIST> terminator
            # bank_transactions() expects — supply one
            for t in bank_transactions(block + "</BANKTRANLIST>"):
                yield {"kind": kind, "date": t["date"], "txn": t,
                       "fitid": t["fitid"]}
            continue
        date = _date(block, "DTTRADE") or _date(block, "DTPOSTED")
        ticker = sec.get(_tag(block, "UNIQUEID"), _tag(block, "UNIQUEID"))
        units, price, total = _num(block, "UNITS"), _num(block, "UNITPRICE"), _num(block, "TOTAL")
        income = _tag(block, "INCOMETYPE").upper()
        if not date or (kind != "INCOME" and not units) or (kind == "INCOME" and total is None):
            skipped.append((kind, f"{date or 'no date'}: missing units/total — row skipped"))
            continue
        if kind != "INCOME" and not TICKER_RE.match(ticker):
            skipped.append((kind, f"{date}: security {ticker!r} has no usable "
                                  f"ticker — book this row by hand"))
            continue
        if kind in BUY_KINDS + ("REINVEST",) and not price and total is None:
            # No UNITPRICE and no TOTAL: the lot would book at {0.00 USD}
            # basis — a phantom-zero cost that silently overstates every
            # future gain. Report-and-skip; book by hand with the real basis.
            skipped.append((kind, f"{date}: {ticker} carries neither UNITPRICE "
                                  f"nor TOTAL — refusing to mint a {{0.00 USD}} "
                                  f"basis lot; book this row by hand"))
            continue
        yield {"kind": kind, "date": date, "ticker": ticker, "units": units,
               "price": price or 0.0, "total": total, "income": income,
               "fitid": _tag(block, "FITID")}
    for kind, note in skipped:
        print(f";   skipped ({kind}) {note}", file=sys.stderr)
    unsupported = [k for k in UNSUPPORTED if re.search(rf"<{k}>", stmt)]
    if unsupported:
        print(f";   unsupported OFX action types present, NOT imported: "
              f"{', '.join(unsupported)} — book by hand if material", file=sys.stderr)


def positions(stmt, sec):
    """<INVPOSLIST> -> {ticker: units summed} as of the statement date."""
    m = re.search(r"<INVPOSLIST>(.*?)</INVPOSLIST>", stmt, re.S)
    out = {}
    if not m:
        return out
    for pos in re.findall(r"<INVPOS>(.*?)</INVPOS>", m.group(1), re.S):
        ticker = sec.get(_tag(pos, "UNIQUEID"), _tag(pos, "UNIQUEID"))
        units = _num(pos, "UNITS")
        if units is not None and TICKER_RE.match(ticker):
            out[ticker] = out.get(ticker, 0.0) + units
    return out


# ---------------------------------------------------------------- emitting
def _fmt_units(x):
    # Preserve the file's full unit precision (Vanguard MF units carry 5
    # decimals): a truncated posting re-multiplies against per-unit cost to a
    # DIFFERENT total and bean-check rejects the batch by fractions of a cent.
    return f"{x:.6f}".rstrip("0").rstrip(".") if abs(x - round(x, 3)) > 1e-9 else f"{x:.3f}"


def _fmt_price(price):
    """Full precision, but never fewer than 2 decimals (price conventions)."""
    t = f"{price:.6f}".rstrip("0")
    whole, _, frac = t.partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def _cost(units, price, total):
    """Cost braces for an acquiring posting: per-unit when it explains the
    cash to the cent, else total-cost {{...}} so commissions land in basis
    and the entry always balances exactly."""
    if abs(abs(units) * price - abs(total)) < 0.005:
        # Full price precision: printing a 4-decimal fund price at 2 decimals
        # changes units*price and bean-check rejects by fractions of a cent.
        return "{" + f"{_fmt_price(price)} USD" + "}"
    return "{{" + f"{abs(total):.2f} USD" + "}}"


def render(date, payee, meta, postings):
    lines = [f'{date} * "{escape(payee)}" ""']
    lines += [f'  {k}: "{escape(str(v))}"' for k, v in meta.items() if v]
    lines += postings
    return "\n".join(lines) + "\n"


def build(a, account):
    """One parsed action -> (payee, cash_amount_for_hash, entry_text,
    {ticker: unit_delta}, accounts_used)."""
    date, kind = a["date"], a["kind"]
    if kind == "INVBANKTRAN":
        t = a["txn"]
        # Brokerage cash-ins often carry NO payee, so payee rules can't route
        # them. rules.toml [accounts_invbanktran] maps the brokerage account
        # to a default counter (e.g. Transfers when the bank twin is in the
        # ledger, an Income bucket when the source is external); a payee rule
        # still wins when one matches.
        counter = categorize(t["payee"], t["type"], t["amount"], account)
        if counter in ("Income:US:Other", "Expenses:Uncategorized"):
            from rules import rules as _rules
            counter = _rules().get("accounts_invbanktran", {}).get(account, counter)
        entry = render(date, t["payee"], {"ofx-type": t["type"], "import-hash": a["hash"],
                                          "fitid": t["fitid"]},
                       [f"  {account}   {t['amount']:.2f} USD", f"  {counter}"])
        return entry, {}, {account, counter}
    ticker, units, price = a["ticker"], a["units"], a["price"]
    if kind in BUY_KINDS:
        total = -abs(a["total"] if a["total"] is not None else units * price)
        entry = render(date, a["payee"], {"ofx-type": kind, "import-hash": a["hash"],
                                          "fitid": a.get("fitid", "")},
                       [f"  {account}   {_fmt_units(units)} {ticker} {_cost(units, price, total)}",
                        f"  {account}   {total:.2f} USD"])
        return entry, {ticker: units}, {account}
    if kind in SELL_KINDS:
        total = abs(a["total"] if a["total"] is not None else units * price)
        entry = render(date, a["payee"], {"ofx-type": kind, "import-hash": a["hash"],
                                          "fitid": a.get("fitid", "")},
                       [f"  {account}   -{_fmt_units(abs(units))} {ticker} {{}} @ {_fmt_price(price)} USD"
                        f"  ; whole lots (FIFO if set) — broker lot data governs at tax time",
                        f"  {account}   {total:.2f} USD",
                        f"  {GAINS}"])
        return entry, {ticker: -abs(units)}, {account, GAINS}
    if kind == "REINVEST":
        income_acct = INCOME_ACCOUNTS.get(a["income"], INCOME_DEFAULT)
        # Sign follows the ROW: a normal reinvest carries negative TOTAL
        # (income leg negative = income earned) and positive units; a broker
        # CORRECTION carries negative units and positive TOTAL — forcing
        # -abs() there would book the reversal as a second income hit
        # instead of backing the first one out.
        total = a["total"] if a["total"] is not None else -(units * price)
        entry = render(date, a["payee"], {"ofx-type": kind, "import-hash": a["hash"],
                                          "fitid": a.get("fitid", "")},
                       [f"  {account}   {_fmt_units(units)} {ticker} {_cost(units, price, total)}",
                        f"  {income_acct}   {total:.2f} USD"])
        return entry, {ticker: units}, {account, income_acct}
    # INCOME (cash distribution)
    income_acct = INCOME_ACCOUNTS.get(a["income"], INCOME_DEFAULT)
    entry = render(date, a["payee"], {"ofx-type": kind, "import-hash": a["hash"],
                                      "fitid": a.get("fitid", "")},
                   [f"  {account}   {a['total']:.2f} USD", f"  {income_acct}"])
    return entry, {}, {account, income_acct}


def payee_for(a):
    k = a["kind"]
    if k == "INVBANKTRAN":
        return a["txn"]["payee"]
    if k in BUY_KINDS:
        return f"BUY {_fmt_units(a['units'])} {a['ticker']} @ {a['price']:.2f}"
    if k in SELL_KINDS:
        return f"SELL {_fmt_units(abs(a['units']))} {a['ticker']} @ {a['price']:.2f}"
    if k == "REINVEST":
        return f"REINVEST {a['income'] or 'DIV'} {_fmt_units(a['units'])} {a['ticker']} @ {a['price']:.2f}"
    return f"{a['income'] or 'INCOME'} {a['ticker']}"


def cash_amount(a):
    """The signed cash effect used in the dedupe hash (cents-stable)."""
    if a["kind"] == "INVBANKTRAN":
        return a["txn"]["amount"]
    if a["total"] is not None:
        return a["total"]
    if a["kind"] == "REINVEST":
        return -(a["units"] * a["price"])  # direction follows the row (corrections)
    sign = -1 if a["kind"] in BUY_KINDS else 1
    return sign * abs(a["units"] * a["price"])


# ------------------------------------------------------------- reconcile
def ledger_units(account, asof):
    """{commodity: units} held in the ledger through a date; None when the
    ledger can't be queried (vault venv missing)."""
    try:
        rows = query(f"SELECT currency, sum(number) AS units WHERE account = '{account}' "
                     f"AND currency != 'USD' AND date <= {asof.isoformat()} GROUP BY currency")
    except (RuntimeError, SystemExit):
        return None
    out = {}
    for r in rows:
        try:
            out[r["currency"]] = out.get(r["currency"], 0.0) + float(r["units"])
        except (TypeError, ValueError, KeyError):
            continue
    return out


def reconcile(account, stated, asof, kept_units, first_activity):
    """Compare ledger+import units per commodity against <INVPOSLIST>.
    Returns (worst_tag, matched: {ticker: units}) and prints the report."""
    if not stated:
        print(f"; {account}: positions UNVERIFIABLE — statement has no <INVPOSLIST>",
              file=sys.stderr)
        return UNVERIFIABLE, {}
    prior = ledger_units(account, asof)
    if prior is None:
        print(f"; {account}: positions UNVERIFIABLE — ledger not queryable "
              f"(vault venv missing?)", file=sys.stderr)
        return UNVERIFIABLE, {}
    parts, missing, excess, matched = [], [], [], {}
    for ticker in sorted(set(stated) | set(prior) | set(kept_units)):
        computed = prior.get(ticker, 0.0) + kept_units.get(ticker, 0.0)
        want = stated.get(ticker, 0.0)
        if abs(computed - want) <= 0.001:
            parts.append(f"{ticker} MATCH ({_fmt_units(want)})")
            matched[ticker] = want
        else:
            parts.append(f"{ticker} MISMATCH (ledger+import {_fmt_units(computed)} "
                         f"vs statement {_fmt_units(want)})")
            (missing if want > computed else excess).append((ticker, want - computed))
    tag = MISMATCH if (missing or excess) else MATCH
    print(f"; {account}: positions vs statement {asof} {tag} — {', '.join(parts)}",
          file=sys.stderr)
    if missing:
        seed_date = (first_activity or asof) - timedelta(days=1)
        print(f";   a MISMATCH is a pre-import history gap, not a block — seed an "
              f"opening position of "
              f"{', '.join(f'{_fmt_units(g)} {t}' for t, g in missing)}, e.g.:", file=sys.stderr)
        print(f';   {seed_date} * "Opening position (history predates the vault)" ""',
              file=sys.stderr)
        for ticker, gap in missing:
            print(f";     {account}   {_fmt_units(gap)} {ticker} {{COST USD}}  "
                  f"; basis from broker records — needed before {ticker} sells can book",
                  file=sys.stderr)
        print(f";     Equity:Opening-Balances", file=sys.stderr)
    if excess:
        print(f";   ledger EXCEEDS the statement by "
              f"{', '.join(f'{_fmt_units(-g)} {t}' for t, g in excess)} — that is "
              f"duplicates or drift, not a history gap; audit manual entries", file=sys.stderr)
    return tag, matched


def opened_accounts():
    """Accounts with an `open` directive anywhere in ledger/*.beancount."""
    out = set()
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            out.update(re.findall(r"^\s*\d{4}-\d{2}-\d{2}\s+open\s+(\S+)",
                                  f.read_text(), re.M))
        except OSError:
            continue
    return out


# ------------------------------------------------------------------ main
def main():
    argv = sys.argv[1:]
    since, argv = since_from_argv(argv, __doc__)
    unknown = {a for a in argv if a.startswith("--")} - FLAGS
    if unknown:
        sys.exit(f"unknown flag(s): {', '.join(sorted(unknown))}\n\n{__doc__}")
    args = [a for a in argv if not a.startswith("--")]
    dedupe = "--all" not in argv
    write = "--write" in argv and "--dry-run" not in argv
    if not args:
        sys.exit(__doc__)
    text = read_ofx(args[0])
    sec = securities(text)
    statements = list(invest_statements(text))
    if not statements:
        sys.exit("no <INVSTMTRS> blocks found — is this an investment OFX/QFX? "
                 "(bank/card exports go through ofx.py)")
    if len(args) > 1 and len(statements) > 1:
        sys.exit(f"{args[0]} holds {len(statements)} accounts — an explicit ledger "
                 f"account can't apply to all of them. Add [[accounts]] routing entries instead.")
    ledger_hashes, ledger_fitids = existing_ids() if dedupe else ({}, {})
    entries, used_accounts = [], set()
    for acct_id, stmt in statements:
        account = args[1] if len(args) > 1 else route_by_acctid(acct_id)
        if not account:
            print(f"; skipping account ending {acct_id[-4:]!r}: no [[accounts]] entry in "
                  f"rules.toml — add one (or pass the ledger account explicitly)", file=sys.stderr)
            continue
        idx = existing_index(account, set(ledger_hashes)) if dedupe else {}
        new_hashes, new_fitids = {}, set()
        rows = sorted(actions(stmt, sec), key=lambda a: a["date"])
        if since:
            pre = len(rows)
            rows = [a for a in rows if str(a["date"]) >= since]
            if pre - len(rows):
                print(f";   --since {since}: ignoring {pre - len(rows)} earlier rows "
                      f"(pre-snapshot history the opening position already nets)", file=sys.stderr)
        kept, skipped, deltas_by_date = [], [], []
        for a in rows:
            a["payee"] = payee_for(a)
            amt = cash_amount(a)
            a["hash"] = h = import_hash(a["date"], amt, a["payee"], account)
            fid = a.get("fitid", "")
            if dedupe and fid and (fid in ledger_fitids.get(account, ())
                                   or fid in new_fitids):
                skipped.append((a, amt, "fitid"))
                continue
            if dedupe and hash_is_duplicate(h, fid, ledger_hashes, new_hashes):
                skipped.append((a, amt, "hash"))
                continue
            if dedupe and is_duplicate(idx, a["date"], amt, a["payee"]):
                skipped.append((a, amt, "±5d"))
                continue
            new_hashes.setdefault(h, set())
            if fid:
                new_hashes[h].add(fid)
                new_fitids.add(fid)
            entry, deltas, used = build(a, account)
            entries.append((a["date"], entry))
            used_accounts |= used
            if deltas:
                deltas_by_date.append((a["date"], deltas))
            kept.append(a["date"])
        asof = _date(stmt, "DTASOF") or _date(stmt, "DTEND")
        stated = positions(stmt, sec)
        if asof:
            in_window = [d for d in kept if d <= asof]
            kept_units = {}  # unit deltas from this import dated on/before asof
            for d, deltas in deltas_by_date:
                if d <= asof:
                    for t, u in deltas.items():
                        kept_units[t] = kept_units.get(t, 0.0) + u
            tag, matched = reconcile(account, stated, asof,
                                     kept_units, min(in_window, default=None))
            if tag == MATCH and kept:
                a_date = assertion_date(asof, max(in_window, default=asof))
                for ticker, units in sorted(matched.items()):
                    entries.append((a_date, f"{a_date} balance {account}   "
                                            f"{_fmt_units(units)} {ticker}\n"))
        else:
            print(f"; {account}: positions UNVERIFIABLE — no <DTASOF> on the statement",
                  file=sys.stderr)
        print(f"; imported {len(kept)} transactions to {account}"
              + (f" (skipped {len(skipped)} already in ledger)" if skipped else ""),
              file=sys.stderr)
        for a, amt, why in skipped:
            print(f";   skipped ({why}) {a['date']} {amt:.2f} {a['payee']}", file=sys.stderr)
    missing = sorted(used_accounts - opened_accounts())
    if missing:
        print("; NOT YET OPENED in ledger/*.beancount — add to accounts.beancount "
              "(bean-check will reject --write until then):", file=sys.stderr)
        for acct in missing:
            print(f";   2000-01-01 open {acct}" +
                  ("   USD" if acct.split(":")[0] in ("Income", "Expenses") else
                   '            ; holds units + settlement cash; add "FIFO" once sells appear'),
                  file=sys.stderr)
    for _, e in entries:
        print(e)
    sys.stdout.flush()
    if write:
        if not entries:
            print("; nothing new to write", file=sys.stderr)
            return
        paths = append_to_ledger(entries)
        print(f"; wrote {len(entries)} entries to {', '.join(paths)} (bean-check passed "
              f"or was unavailable — see above)", file=sys.stderr)


if __name__ == "__main__":
    main()
