"""Shared bits for the importers: OFX/QFX parsing, escaping, Beancount emitting,
dedupe against what's already in the ledger, statement balance continuity, and
the careful --write path (append + bean-check + rollback)."""
import hashlib
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from vault import BEAN_CHECK, VAULT, amount, query  # noqa: E402  (tools/ on path)


def escape(s):
    """Beancount string-safe. This text lands inside "..." in a file that is
    parsed — and in --write mode appended — automatically, so a statement
    field must never break out of the string: no double quotes, no backslashes
    (Beancount reads \\" as an escaped quote), and control characters
    (newlines included) collapse to single spaces."""
    s = "".join(c if c.isprintable() else " " for c in (s or ""))
    return " ".join(s.replace('"', "'").replace("\\", "/").split())


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
        a = re.search(r"<TRNAMT>([-+\d.]+)", t)
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


# ------------------------------------------------------------------- dedupe
def import_hash(date, amt, payee, account):
    """Stable content-hash identity for an imported row (the beanborg pattern).

    Hashed over the normalized fields that survive a re-export unchanged —
    date | amount in cents | payee prefix | ledger account — and written as
    `import-hash:` metadata on every imported transaction. Re-importing the
    same statement (or an overlapping export) then recognizes its own rows
    EXACTLY, before any fuzzy matching. 16 hex chars = 64 bits: collisions
    are effectively impossible at household transaction volumes, and short
    enough not to clutter the ledger.
    """
    key = f"{date.isoformat()}|{round(amt, 2) + 0.0:.2f}|{payee_key(payee)}|{account}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


IMPORT_HASH_META = re.compile(r'^\s+import-hash:\s*"([0-9a-f]{8,64})"', re.M)


def existing_hashes():
    """Every import-hash already recorded in ledger/*.beancount.

    Read straight from the files (not bean-query) so it works before the
    vault venv exists and stays cheap. Ledgers that predate hashing simply
    contribute nothing here — their entries are still caught by the ±5-day
    fuzzy match below, so old vaults keep deduping without a migration.
    """
    found = set()
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            found.update(IMPORT_HASH_META.findall(f.read_text()))
        except OSError:
            continue
    return found


def existing_index(account):
    """(amount, payee-prefix) -> set of dates already in the ledger for an account.

    The FUZZY half of dedupe; hash-exact matching (import_hash above) runs
    first. This index catches the same transaction arriving via a different
    export format, where the date shifts (Chase CSV carries the transaction
    date, QFX the post date), by matching amount + payee within a date
    WINDOW. The window applies ONLY to ledger-sourced dates — rows within one
    import are deduped hash-exact (a bank never emits the same transaction
    twice, and two real same-amount charges days apart at one merchant are
    legitimate). Ledger-side twins inside the window still collide, so
    importers list every skip and --all forces it.
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


# --------------------------------------------------------------- continuity
# Statement balance continuity — the "Golden Rule" (bankstatementparser):
# opening + credits - debits must equal closing, or the import is suspect.
VERIFIED, DISCREPANCY, UNVERIFIABLE = "VERIFIED", "DISCREPANCY", "UNVERIFIABLE"


def ofx_closing_balance(text):
    """(closing_balance, asof_date) from a statement's <LEDGERBAL>, else (None, None)."""
    m = re.search(r"<LEDGERBAL>(.*?)(?=</LEDGERBAL>|<AVAILBAL>|<STMTTRN>|\Z)", text, re.S)
    if not m:
        return None, None
    block = m.group(1)
    b = re.search(r"<BALAMT>([-+\d.]+)", block)
    d = re.search(r"<DTASOF>(\d{8})", block) or re.search(r"<DTEND>(\d{8})", text)
    asof = datetime.strptime(d.group(1), "%Y%m%d").date() if d else None
    return (float(b.group(1)) if b else None), asof


def ledger_balance_asof(account, asof):
    """(USD balance, posting count) for an account through a date; (None, 0)
    when the ledger can't be queried (vault venv missing)."""
    try:
        rows = query(f"SELECT count(*) AS n, sum(convert(position,'USD')) AS v "
                     f"WHERE account = '{account}' AND date <= {asof.isoformat()}")
    except (RuntimeError, SystemExit):
        return None, 0
    if not rows:
        return 0.0, 0
    n = int(float(rows[0].get("n") or 0))
    return amount(rows[0].get("v")), n


def check_continuity_ledger(account, closing, asof, kept):
    """Golden Rule anchored on the ledger: what the ledger already holds
    through the statement date, plus the rows this import adds (`kept`:
    (date, amount) pairs), must equal the statement's closing balance.
    Skipped duplicates are counted implicitly — they're the ledger rows.
    Returns (tag, detail).
    """
    if closing is None:
        return UNVERIFIABLE, "statement carries no closing balance"
    if asof is None:
        return UNVERIFIABLE, "closing balance has no usable as-of date"
    prior, n = ledger_balance_asof(account, asof)
    if prior is None:
        return UNVERIFIABLE, ("ledger not queryable (vault venv missing?) — "
                              "opening balance unknown")
    predicted = prior + sum(a for d, a in kept if d <= asof)
    if abs(predicted - closing) <= 0.005:
        return VERIFIED, (f"ledger {prior:+.2f} + this import "
                          f"{predicted - prior:+.2f} = closing {closing:.2f} as of {asof}")
    if n == 0:
        return UNVERIFIABLE, (f"no ledger history for {account} on/before {asof} — opening "
                              f"balance unknown (statement closes at {closing:.2f}; pad an "
                              f"opening balance, then re-check)")
    return DISCREPANCY, (f"ledger {prior:.2f} through {asof} + this import "
                         f"{predicted - prior:+.2f} = {predicted:.2f}, but the statement "
                         f"closes at {closing:.2f} (off by {closing - predicted:+.2f} — "
                         f"truncated export, missed rows, ledger drift — or, if this account predates the vault and has no Opening-Balances entry, seed one for exactly this delta)")


def check_continuity_rows(rows):
    """Golden Rule inside one CSV: with a running Balance column, every row
    must satisfy balance == previous balance + amount. `rows` is (amount,
    balance) in FILE order; banks export either oldest- or newest-first, so
    both orientations are tried. Returns (tag, detail, closing_balance).
    """
    if not rows or any(b is None for _, b in rows):
        return UNVERIFIABLE, "no running Balance column in this export", None
    for seq in (rows, rows[::-1]):  # oldest-first, then newest-first
        if all(abs(seq[i][1] - (seq[i - 1][1] + seq[i][0])) <= 0.005
               for i in range(1, len(seq))):
            opening = seq[0][1] - seq[0][0]
            return VERIFIED, (f"opening {opening:.2f} + row amounts chain to "
                              f"closing {seq[-1][1]:.2f}"), seq[-1][1]
    return DISCREPANCY, ("running Balance column does not chain (balance != previous + "
                         "amount somewhere) — rows are missing or the export is corrupt"), None


# ---------------------------------------------------------------- emitting
def assertion_date(statement_end, last_txn_date):
    """When to date a closing-balance assertion (beancount_reds_importers).

    statement_end - 2 days: banks cut exports while the last day or two of
    transactions are still settling; a transaction that posts later with a
    date inside that tail would break an assertion dated at the very end, so
    back off two days. But never assert before the last transaction actually
    being imported. +1 day because a Beancount `balance` directive asserts at
    the START of its date — the assertion must be dated the day after the
    activity it covers.
    """
    return max(statement_end - timedelta(days=2), last_txn_date) + timedelta(days=1)


def emit(date, payee, meta, account, amt, counter):
    """Render one Beancount transaction (metadata values are escaped too —
    they come from the same untrusted statement fields as the payee)."""
    lines = [f'{date} * "{escape(payee)}" ""']
    for k, v in meta.items():
        if v:
            lines.append(f'  {k}: "{escape(str(v))}"')
    lines.append(f"  {account}   {amt:.2f} USD")
    lines.append(f"  {counter}")
    return "\n".join(lines) + "\n"


def render_assertion(account, closing, date, verified):
    """A closing-balance assertion — active only when continuity anchored on
    the ledger VERIFIED it (an unanchored assertion would fail bean-check on
    any vault whose history starts mid-stream); otherwise a commented
    suggestion the user can enable once the opening balance is padded."""
    line = f"{date} balance {account}   {closing:.2f} USD"
    if verified:
        return line + "\n"
    return f"; {line}  (unverified against ledger — enable once the opening balance is reconciled)\n"


# ------------------------------------------------------------------- write
def append_to_ledger(entries):
    """Append rendered entries to ledger/<year>.beancount (grouped by date),
    keep main.beancount's include list complete, then bean-check. Any
    validation failure rolls every touched file back — a bad import must
    never leave the ledger broken. Returns the vault-relative paths written.
    """
    ledger_dir = VAULT / "ledger"
    main = ledger_dir / "main.beancount"
    if not main.exists():
        raise SystemExit(f"no ledger at {main} — is FINANCE_VAULT right?")
    by_year = {}
    for date, text in entries:
        by_year.setdefault(date.year, []).append(text)
    backups, written = {}, []
    try:
        for year in sorted(by_year):
            f = ledger_dir / f"{year}.beancount"
            backups[f] = f.read_text() if f.exists() else None
            old = backups[f] or ""
            sep = "" if (not old or old.endswith("\n\n")) else "\n"
            f.write_text(old + sep + "\n".join(by_year[year]))
            written.append(f)
        main_text = main.read_text()
        missing = [y for y in sorted(by_year)
                   if f'include "{y}.beancount"' not in main_text]
        if missing:
            backups[main] = main_text
            adds = "".join(f'include "{y}.beancount"\n' for y in missing)
            main.write_text(main_text + ("" if main_text.endswith("\n") else "\n") + adds)
    except OSError as e:
        _restore(backups)
        raise SystemExit(f"write failed, rolled back: {e}")
    if BEAN_CHECK.exists():
        out = subprocess.run([str(BEAN_CHECK), str(main)], capture_output=True, text=True)
        if out.returncode != 0:
            _restore(backups)
            raise SystemExit("bean-check rejected the import — rolled back:\n"
                             + (out.stderr or out.stdout).strip())
    else:
        print(f"; warning: bean-check not found at {BEAN_CHECK} — appended without validation",
              file=sys.stderr)
    return [str(p.relative_to(VAULT)) for p in written]


def _restore(backups):
    for p, text in backups.items():
        if text is None:
            p.unlink(missing_ok=True)
        else:
            p.write_text(text)
