"""Shared bits for the importers: OFX/QFX parsing, escaping, Beancount emitting,
dedupe against what's already in the ledger, statement balance continuity, and
the careful --write path (append + bean-check + rollback)."""
import hashlib
import os
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


def parse_ofx_amount(raw):
    """Parse an OFX numeric field to float, tolerating US thousands-commas.

    '2,500.00' -> 2500.0 (real banks emit this). Ambiguous or corrupt
    shapes — EU-style '1.234,56', '12,34', multi-dot '1.2.3' — return None
    so the caller can report-and-skip the row: float('2,500.00'-minus-regex)
    used to read as 2.00, and guessing the EU intent is just as much of a
    money bug as crashing.
    """
    s = (raw or "").strip()
    m = re.fullmatch(r"([-+]?)([\d.,]+)", s)
    if not m:
        return None
    sign, body = m.group(1), m.group(2)
    if body.count(".") > 1:
        return None  # '1.2.3' — corrupt
    if "," in body:
        # commas are only legal as US thousands separators: 1-3 leading
        # digits, ,ddd groups, then an optional .decimals tail
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", body):
            return None  # '1.234,56' / '12,34' — ambiguous, never guess
        body = body.replace(",", "")
    try:
        return float(sign + body)
    except ValueError:
        return None


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
    """Yield dicts for each <STMTTRN> in a bank/card OFX/QFX statement.

    Truncation-safe: the terminator lookahead includes \\Z and <LEDGERBAL>,
    so an export cut off before the closing tags still yields its final row;
    a tally of <STMTTRN> opens vs rows parsed-or-reported backstops any other
    silent drop with a loud warning.
    """
    opens = len(re.findall(r"<STMTTRN>", text))
    yielded = reported = 0
    for t in re.findall(r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>|<LEDGERBAL>|\Z)",
                        text, re.S):
        d = re.search(r"<DTPOSTED>(\d{8})", t)
        a = re.search(r"<TRNAMT>([-+\d.,]+)", t)
        if not (d and a):
            reported += 1
            print(f"; skipping malformed row — missing DTPOSTED/TRNAMT "
                  f"({' '.join(t.split())[:80]!r})", file=sys.stderr)
            continue
        # a malformed row must skip with a report, never crash the whole
        # import — and never silently mis-parse (see parse_ofx_amount)
        amt = parse_ofx_amount(a.group(1))
        try:
            when = datetime.strptime(d.group(1), "%Y%m%d").date()
        except ValueError:
            when = None
        if when is None or amt is None:
            reported += 1
            print(f"; skipping malformed row — unparseable TRNAMT/DTPOSTED "
                  f"({a.group(1)!r} / {d.group(1)!r})", file=sys.stderr)
            continue
        n = re.search(r"<NAME>([^<\n]+)", t)
        m = re.search(r"<MEMO>([^<\n]+)", t)
        ty = re.search(r"<TRNTYPE>([A-Z]+)", t)
        fit = re.search(r"<FITID>([^<\n]+)", t)
        name = escape(((n.group(1) if n else "") + (" " + m.group(1) if m else "")).strip())
        yielded += 1
        yield {
            "date": when,
            "amount": amt,
            "payee": name,
            "type": (ty.group(1) if ty else ""),
            "fitid": (fit.group(1).strip() if fit else ""),
        }
    if opens != yielded + reported:
        print(f"; WARNING: {opens} <STMTTRN> blocks opened but only "
              f"{yielded + reported} parsed or reported — the export looks "
              f"truncated/corrupt; reconcile the closing balance before "
              f"trusting this import", file=sys.stderr)


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
FITID_META = re.compile(r'^\s+fitid:\s*"([^"\n]+)"', re.M)
TXN_BLOCK = re.compile(r"^\d{4}-\d{2}-\d{2}\s+[*!].*\n((?:[ \t]+\S.*\n?)*)", re.M)
FIRST_POSTING = re.compile(r"^[ \t]+([A-Z][A-Za-z0-9:_-]*)", re.M)


def existing_ids():
    """Identity of every machine-imported entry in ledger/*.beancount.

    Read straight from the files (not bean-query) so it works before the
    vault venv exists and stays cheap. Returns (hashes, fitids):

      hashes: {import-hash: set of fitids recorded on entries carrying that
              hash}. An empty set = the hash is present but the entry has no
              fitid (imports that predate fitid:, and CSV rows — the CSV
              carries no FITID).
      fitids: {account: set of fitids} keyed by each entry's FIRST posting
              account — the imported account leg in every entry the
              importers write. The bank's FITID is the primary identity:
              it survives payee/amount edits and distinguishes genuinely
              identical same-day transactions.

    Ledgers that predate both simply contribute nothing here — their entries
    are still caught by the ±5-day fuzzy match below, so old vaults keep
    deduping without a migration.
    """
    hashes, fitids = {}, {}
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            text = f.read_text()
        except OSError:
            continue
        for m in TXN_BLOCK.finditer(text):
            body = m.group(1)
            hm = IMPORT_HASH_META.search(body)
            fm = FITID_META.search(body)
            if not hm and not fm:
                continue
            fid = fm.group(1).strip() if fm else ""
            if hm:
                hashes.setdefault(hm.group(1), set())
                if fid:
                    hashes[hm.group(1)].add(fid)
            if fid:
                pm = FIRST_POSTING.search(body)
                if pm:
                    fitids.setdefault(pm.group(1), set()).add(fid)
    return hashes, fitids


def hash_is_duplicate(h, fitid, ledger_hashes, batch_hashes):
    """Content-hash dedupe, FITID-aware (stage b — stage a, exact FITID
    match, runs in the importer first). A hash hit only counts as the same
    transaction when the recorded entry carries no fitid, or this row
    carries none (CSV), or the fitids agree. Two same-day identical rows
    with DIFFERENT bank FITIDs are two real transactions — both import."""
    for seen in (ledger_hashes, batch_hashes):
        if h in seen:
            fids = seen[h]
            if not fids or not fitid or fitid in fids:
                return True
    return False


def existing_index(account, known_hashes=None):
    """(amount, payee-prefix) -> set of dates already in the ledger for an account.

    The FUZZY half of dedupe; fitid-exact and hash-exact matching run first.
    This index catches the same transaction arriving via a different export
    format, where the date shifts (Chase CSV carries the transaction date,
    QFX the post date), by matching amount + payee within a date WINDOW.

    LEGACY ENTRIES ONLY: pass `known_hashes` (the ledger's recorded
    import-hashes) and any row whose recomputed hash is among them is left
    OUT of the index — machine-imported entries always carry the hash (and
    fitid never appears without it), so the exact stages already own their
    identity. Without this, two real same-amount charges days apart at one
    merchant (Monday gym drop-in, Thursday again) would fuzzy-collide even
    though their FITIDs and hashes differ. The window applies ONLY to
    ledger-sourced dates; importers list every skip and --all forces it.
    """
    idx = {}
    account = str(account).replace("'", "")  # account names never contain quotes;
    # strip them so rules.toml-sourced text can't break out of the BQL string
    try:
        rows = query(f"SELECT date, number, payee WHERE account = '{account}'")
    except (RuntimeError, SystemExit):
        return idx
    for r in rows:
        try:
            d = datetime.strptime(r["date"], "%Y-%m-%d").date()
            amt = round(float(r["number"]), 2)
        except (TypeError, ValueError):
            continue
        if known_hashes and import_hash(d, amt, r["payee"], account) in known_hashes:
            continue  # machine-imported — fitid/hash stages own it, never fuzzy
        idx.setdefault((amt, payee_key(r["payee"])), set()).add(d)
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
    b = re.search(r"<BALAMT>([-+\d.,]+)", block)
    d = re.search(r"<DTASOF>(\d{8})", block) or re.search(r"<DTEND>(\d{8})", text)
    closing = asof = None
    if b:
        closing = parse_ofx_amount(b.group(1))
        if closing is None:
            print(f"; ignoring malformed <BALAMT> {b.group(1)!r} — treating the "
                  f"statement as carrying no closing balance", file=sys.stderr)
    if d:
        try:
            asof = datetime.strptime(d.group(1), "%Y%m%d").date()
        except ValueError:
            print(f"; ignoring malformed balance as-of date {d.group(1)!r}", file=sys.stderr)
    return closing, asof


def ledger_balance_asof(account, asof):
    """(USD balance, posting count) for an account through a date; (None, 0)
    when the ledger can't be queried (vault venv missing)."""
    account = str(account).replace("'", "")  # see existing_index — no BQL breakout
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

    CAPPED at statement_end + 1: when statement rows postdate the balance's
    as-of date (banks do ship a LEDGERBAL older than the last rows), the
    closing balance excludes those rows — continuity's predicted sum already
    caps at as-of, and an assertion dated after them would be broken by the
    very import that writes it. `balance` asserts at the START of its date,
    so as-of + 1 covers activity through as-of and nothing later.
    """
    return min(max(statement_end - timedelta(days=2), last_txn_date),
               statement_end) + timedelta(days=1)


ASSERTION_LINE = re.compile(r"^[;\s]*(\d{4}-\d{2}-\d{2})\s+balance\s+(\S+)\s")


def existing_assertion_dates(account):
    """Dates of every balance assertion already recorded for an account in
    ledger/*.beancount — commented suggestions included, so a re-import
    neither duplicates an assertion nor re-suggests one (re-importing an
    already-imported file must not quietly advance the assertion line)."""
    out = set()
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            text = f.read_text()
        except OSError:
            continue
        for line in text.splitlines():
            m = ASSERTION_LINE.match(line)
            if m and m.group(2) == account:
                try:
                    out.add(datetime.strptime(m.group(1), "%Y-%m-%d").date())
                except ValueError:
                    continue
    return out


def since_from_argv(argv, usage):
    """Pull '--since YYYY-MM-DD' out of argv -> (since, remaining argv).

    Exits with usage on a missing or malformed value: rows are dropped by
    STRING comparison against this date, so '2026-6-1' would silently drop
    nothing (or everything), and '--since --write' would swallow the write
    flag AND import everything. Both are money bugs, not conveniences.
    """
    if "--since" not in argv:
        return None, argv
    i = argv.index("--since")
    value = argv[i + 1] if i + 1 < len(argv) else ""
    if value.startswith("--") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise SystemExit(f"--since needs a YYYY-MM-DD date "
                         f"(got {value or 'nothing'})\n\n{usage}")
    return value, argv[:i] + argv[i + 2:]


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
def atomic_write(path, text):
    """Write via tmp + fsync + os.replace so a crash mid-write can never
    leave a ledger file truncated — readers see the old file or the new one,
    nothing in between."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


ACTIVE_INCLUDE = "^include \"{name}\""  # column 0 — a commented ;include (or an
                                        # indented copy) is NOT active and must
                                        # not satisfy the presence check


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
            atomic_write(f, old + sep + "\n".join(by_year[year]))
            written.append(f)
        main_text = main.read_text()
        missing = [y for y in sorted(by_year)
                   if not re.search(ACTIVE_INCLUDE.format(name=f"{y}\\.beancount"),
                                    main_text, re.M)]
        if missing:
            backups[main] = main_text
            adds = "".join(f'include "{y}.beancount"\n' for y in missing)
            atomic_write(main, main_text + ("" if main_text.endswith("\n") else "\n") + adds)
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
            atomic_write(p, text)
