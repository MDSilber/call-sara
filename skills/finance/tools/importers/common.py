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


def existing_index(account):
    """(amount, payee-prefix) -> set of dates already in the ledger for an account.

    Transaction date vs post date differ between export formats (Chase CSV
    uses transaction date; QFX uses DTPOSTED), so dedupe against the LEDGER
    matches amount + payee within a date WINDOW. The window applies ONLY to
    ledger-sourced dates — rows within one export are deduped exactly (a bank
    never emits the same transaction twice, and two real same-amount charges
    days apart at one merchant are legitimate). Ledger-side twins inside the
    window still collide, so importers list every skip and --all forces it.
    """
    idx = {}
    try:
        rows = query(f"SELECT date, number, payee WHERE account = '{account}'")
    except (RuntimeError, SystemExit):
        return idx
    for r in rows:
        try:
            key = (round(float(r["number"]), 2), payee_key(r["payee"]))
            idx.setdefault(key, set()).add(datetime.strptime(r["date"], "%Y-%m-%d").date())
        except (TypeError, ValueError):
            continue
    return idx


def is_duplicate(idx, date, amount, payee, window=5):
    """True if (amount, payee) already exists in the LEDGER within ±window days.

    window=5 covers weekend/holiday posting lag between transaction date and
    post date.
    """
    dates = idx.get((round(amount, 2), payee_key(payee)))
    if not dates:
        return False
    return any(abs((d - date).days) <= window for d in dates)


def seen_in_file(fileset, date, amount, payee):
    """Exact intra-file duplicate check + record (banks don't emit true dupes)."""
    key = (date, round(amount, 2), payee_key(payee))
    if key in fileset:
        return True
    fileset.add(key)
    return False


def emit(date, payee, meta, account, amount, counter):
    """Print one Beancount transaction."""
    print(f'{date} * "{payee}" ""')
    for k, v in meta.items():
        if v:
            print(f'  {k}: "{v}"')
    print(f"  {account}   {amount:.2f} USD")
    print(f"  {counter}")
    print()
