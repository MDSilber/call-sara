"""Shared bits for the importers: OFX/QFX parsing, escaping, Beancount emitting,
and dedupe against what's already in the ledger."""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vault import query  # noqa: E402  (tools/ on path)


def escape(s):
    """Beancount string-safe: no double quotes, collapse whitespace."""
    return " ".join((s or "").replace('"', "'").split())


def read_ofx(path):
    return Path(path).read_text(encoding="latin-1")


def acctid(text):
    m = re.search(r"<ACCTID>([^<\n]+)", text)
    return m.group(1).strip() if m else ""


def bank_statements(text):
    """Yield (acctid, statement_text) per bank/card statement in the file.

    A single export can carry several accounts (e.g. checking + savings);
    each <STMTRS>/<CCSTMTRS> block has its own <ACCTID> and transaction list.
    """
    chunks = re.split(r"(?=<(?:CC)?STMTRS>)", text)
    found = False
    for chunk in chunks:
        if not re.match(r"<(?:CC)?STMTRS>", chunk):
            continue
        found = True
        yield acctid(chunk), chunk
    if not found:
        yield acctid(text), text  # header-less / minimal files


def bank_transactions(text):
    """Yield dicts for each <STMTTRN> in a bank/card OFX/QFX statement."""
    for t in re.findall(r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>)", text, re.S):
        d = re.search(r"<DTPOSTED>(\d{8})", t)
        a = re.search(r"<TRNAMT>([-\d.]+)", t)
        if not (d and a):
            continue
        n = re.search(r"<NAME>([^<\n]+)", t)
        m = re.search(r"<MEMO>([^<\n]+)", t)
        ty = re.search(r"<TRNTYPE>([A-Z]+)", t)
        fit = re.search(r"<FITID>([^<\n]+)", t)
        name = escape(((n.group(1) if n else "") + (" " + m.group(1) if m else "")).strip())
        yield {
            "date": datetime.strptime(d.group(1), "%Y%m%d").date(),
            "amount": float(a.group(1)),
            "payee": name,
            "type": (ty.group(1) if ty else ""),
            "fitid": (fit.group(1).strip() if fit else ""),
        }


def payee_key(payee):
    """Normalized payee prefix used in the dedupe key."""
    return re.sub(r"[^A-Z0-9]", "", (payee or "").upper())[:12]


def existing_keys(account):
    """(date, amount, payee-prefix) triples already in the ledger — for dedupe.

    Including the payee keeps two same-day, same-amount charges at different
    merchants distinct; identical twins at ONE merchant still collide, so the
    importers list what they skip and the user can force with --all.
    """
    try:
        rows = query(f"SELECT date, number, payee WHERE account = '{account}'")
    except (RuntimeError, SystemExit):
        return set()
    keys = set()
    for r in rows:
        try:
            keys.add((r["date"], round(float(r["number"]), 2), payee_key(r["payee"])))
        except (TypeError, ValueError):
            continue
    return keys


def emit(date, payee, meta, account, amount, counter):
    """Print one Beancount transaction."""
    print(f'{date} * "{payee}" ""')
    for k, v in meta.items():
        if v:
            print(f'  {k}: "{v}"')
    print(f"  {account}   {amount:.2f} USD")
    print(f"  {counter}")
    print()
