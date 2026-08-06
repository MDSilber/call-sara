"""The single write path into the ledger — every source lands through here.

Ported guard-for-guard from the audited importer write logic; the guards are
the product:

  * dedupe against the loaded ledger — source_id (FITID / Plaid id) primary,
    `import-hash:` content hash fallback, and the ±5-day fuzzy match kept
    for legacy entries that predate both;
  * balance-continuity gates (the "Golden Rule": opening + rows = closing);
  * dry-run by default — callers print entries and only append when asked;
  * atomic tmp+fsync+rename appends with an in-process bean-check and a
    full rollback of every touched file when validation fails.

Money is Decimal end to end; a float can never enter a ledger file from
here (tests/test_float_ban.py holds the door).
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from pathlib import Path

from sara.ledger.queries import CENT, ZERO, account_rows, ledger_balance_asof
from sara.sources.model import escape
from sara.vault import BEAN_CHECK, VAULT

Entry = tuple[date, str]  # (entry date, rendered beancount text)


# ------------------------------------------------------------------- dedupe
def payee_key(payee: str | None) -> str:
    """Normalized payee prefix used in the dedupe key."""
    return re.sub(r"[^A-Z0-9]", "", (payee or "").upper())[:12]


def _cents(amt: Decimal) -> Decimal:
    """Quantize to cents for identity/index math. -0.00 normalizes to 0.00,
    matching the float-era `round(x, 2) + 0.0` so hashes recorded by earlier
    imports still match byte for byte."""
    q = amt.quantize(CENT, rounding=ROUND_HALF_EVEN)
    return abs(q) if q == 0 else q


def import_hash(when: date, amt: Decimal, payee: str | None, account: str) -> str:
    """Stable content-hash identity for an imported row (the beanborg pattern).

    Hashed over the normalized fields that survive a re-export unchanged —
    date | amount in cents | payee prefix | ledger account — and written as
    `import-hash:` metadata on every imported transaction. Re-importing the
    same statement (or an overlapping export) then recognizes its own rows
    EXACTLY, before any fuzzy matching. 16 hex chars = 64 bits: collisions
    are effectively impossible at household transaction volumes, and short
    enough not to clutter the ledger.
    """
    key = f"{when.isoformat()}|{_cents(amt):.2f}|{payee_key(payee)}|{account}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


IMPORT_HASH_META = re.compile(r'^\s+import-hash:\s*"([0-9a-f]{8,64})"', re.M)
# The source-id slot: OFX entries record the bank's FITID as `fitid:`;
# Plaid entries record the Plaid transaction_id as `plaid-id:`. One regex
# reads both so every machine-imported entry has exactly one source identity.
SOURCE_ID_META = re.compile(r'^\s+(?:fitid|plaid-id):\s*"([^"\n]+)"', re.M)
TXN_BLOCK = re.compile(r"^\d{4}-\d{2}-\d{2}\s+[*!].*\n((?:[ \t]+\S.*\n?)*)", re.M)
FIRST_POSTING = re.compile(r"^[ \t]+([A-Z][A-Za-z0-9:_-]*)", re.M)

HashIndex = dict[str, set[str]]
SourceIdIndex = dict[str, set[str]]


def existing_ids() -> tuple[HashIndex, SourceIdIndex]:
    """Identity of every machine-imported entry in ledger/*.beancount.

    Read straight from the files (not bean-query) so it works before the
    vault venv exists and stays cheap. Returns (hashes, source_ids):

      hashes: {import-hash: set of source ids recorded on entries carrying
              that hash}. An empty set = the hash is present but the entry
              has no source id (imports that predate fitid:, and CSV rows —
              the CSV carries no FITID).
      source_ids: {account: set of ids} keyed by each entry's FIRST posting
              account — the imported account leg in every entry the
              importers write. The bank's FITID / Plaid's transaction_id is
              the primary identity: it survives payee/amount edits and
              distinguishes genuinely identical same-day transactions.

    Ledgers that predate both simply contribute nothing here — their entries
    are still caught by the ±5-day fuzzy match below, so old vaults keep
    deduping without a migration.
    """
    hashes: HashIndex = {}
    source_ids: SourceIdIndex = {}
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            text = f.read_text()
        except OSError:
            continue
        for m in TXN_BLOCK.finditer(text):
            body = m.group(1)
            hm = IMPORT_HASH_META.search(body)
            fm = SOURCE_ID_META.search(body)
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
                    source_ids.setdefault(pm.group(1), set()).add(fid)
    return hashes, source_ids


def hash_is_duplicate(h: str, source_id: str, ledger_hashes: HashIndex,
                      batch_hashes: HashIndex) -> bool:
    """Content-hash dedupe, source-id-aware (stage b — stage a, exact
    source-id match, runs first). A hash hit only counts as the same
    transaction when the recorded entry carries no source id, or this row
    carries none (CSV), or the ids agree. Two same-day identical rows
    with DIFFERENT bank FITIDs are two real transactions — both import."""
    for seen in (ledger_hashes, batch_hashes):
        if h in seen:
            fids = seen[h]
            if not fids or not source_id or source_id in fids:
                return True
    return False


FuzzyIndex = dict[tuple[Decimal, str], set[date]]


def existing_index(account: str, known_hashes: set[str] | None = None) -> FuzzyIndex:
    """(amount, payee-prefix) -> set of dates already in the ledger for an account.

    The FUZZY half of dedupe; source-id-exact and hash-exact matching run
    first. This index catches the same transaction arriving via a different
    export format, where the date shifts (Chase CSV carries the transaction
    date, QFX the post date), by matching amount + payee within a date WINDOW.

    LEGACY ENTRIES ONLY: pass `known_hashes` (the ledger's recorded
    import-hashes) and any row whose recomputed hash is among them is left
    OUT of the index — machine-imported entries always carry the hash (and
    a source id never appears without it), so the exact stages already own
    their identity. Without this, two real same-amount charges days apart at
    one merchant (Monday gym drop-in, Thursday again) would fuzzy-collide
    even though their FITIDs and hashes differ. The window applies ONLY to
    ledger-sourced dates; importers list every skip and --all forces it.
    """
    idx: FuzzyIndex = {}
    for d, amt, payee in account_rows(account):
        if known_hashes and import_hash(d, amt, payee, account) in known_hashes:
            continue  # machine-imported — source-id/hash stages own it, never fuzzy
        idx.setdefault((amt, payee_key(payee)), set()).add(d)
    return idx


def is_duplicate(idx: FuzzyIndex, when: date, amount: Decimal, payee: str | None,
                 window: int = 5) -> bool:
    """True if (amount, payee) already exists in the LEDGER within ±window days.

    window=5 covers weekend/holiday posting lag between transaction date and
    post date.
    """
    dates = idx.get((_cents(amount), payee_key(payee)))
    if not dates:
        return False
    return any(abs((d - when).days) <= window for d in dates)


class AccountDedupe:
    """The three dedupe stages for one account, in order, batch-aware.

    Stage a: source_id exact (ledger + this batch). Stage b: content hash,
    honored only when the source ids don't disagree. Stage c: ±5-day fuzzy,
    legacy ledger entries only. `check` returns the stage that matched
    ("fitid" / "hash" / "±5d") or None; `record` claims the row's identity
    so the rest of the batch dedupes against it too.
    """

    def __init__(self, account: str, ledger_hashes: HashIndex,
                 ledger_source_ids: SourceIdIndex, enabled: bool = True) -> None:
        self.account = account
        self.enabled = enabled
        self.ledger_hashes = ledger_hashes
        self.ledger_sids: frozenset[str] = frozenset(ledger_source_ids.get(account, set()))
        self.idx: FuzzyIndex = (existing_index(account, set(ledger_hashes))
                                if enabled else {})
        self.new_hashes: HashIndex = {}
        self.new_sids: set[str] = set()

    def hash_for(self, when: date, amount: Decimal, payee: str) -> str:
        return import_hash(when, amount, payee, self.account)

    def check(self, when: date, amount: Decimal, payee: str, source_id: str,
              h: str | None = None) -> str | None:
        if not self.enabled:
            return None
        h = h or self.hash_for(when, amount, payee)
        if source_id and (source_id in self.ledger_sids or source_id in self.new_sids):
            return "fitid"
        if hash_is_duplicate(h, source_id, self.ledger_hashes, self.new_hashes):
            return "hash"
        if is_duplicate(self.idx, when, amount, payee):
            return "±5d"
        return None

    def record(self, h: str, source_id: str) -> None:
        self.new_hashes.setdefault(h, set())
        if source_id:
            self.new_hashes[h].add(source_id)
            self.new_sids.add(source_id)


# --------------------------------------------------------------- continuity
# Statement balance continuity — the "Golden Rule" (bankstatementparser):
# opening + credits - debits must equal closing, or the import is suspect.
VERIFIED, DISCREPANCY, UNVERIFIABLE = "VERIFIED", "DISCREPANCY", "UNVERIFIABLE"
TOLERANCE = Decimal("0.005")


def check_continuity_ledger(account: str, closing: Decimal | None, asof: date | None,
                            kept: Iterable[tuple[date, Decimal]]) -> tuple[str, str]:
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
    predicted = prior + sum((a for d, a in kept if d <= asof), ZERO)
    if abs(predicted - closing) <= TOLERANCE:
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


def check_continuity_rows(
        rows: Sequence[tuple[Decimal, Decimal | None]]) -> tuple[str, str, Decimal | None]:
    """Golden Rule inside one CSV: with a running Balance column, every row
    must satisfy balance == previous balance + amount. `rows` is (amount,
    balance) in FILE order; banks export either oldest- or newest-first, so
    both orientations are tried. Returns (tag, detail, closing_balance).
    """
    if not rows or any(b is None for _, b in rows):
        return UNVERIFIABLE, "no running Balance column in this export", None
    chained: list[list[tuple[Decimal, Decimal]]] = [
        [(a, b) for a, b in rows if b is not None]]
    chained.append(chained[0][::-1])
    for seq in chained:  # oldest-first, then newest-first
        if all(abs(seq[i][1] - (seq[i - 1][1] + seq[i][0])) <= TOLERANCE
               for i in range(1, len(seq))):
            opening = seq[0][1] - seq[0][0]
            return VERIFIED, (f"opening {opening:.2f} + row amounts chain to "
                              f"closing {seq[-1][1]:.2f}"), seq[-1][1]
    return DISCREPANCY, ("running Balance column does not chain (balance != previous + "
                         "amount somewhere) — rows are missing or the export is corrupt"), None


# ---------------------------------------------------------------- emitting
def assertion_date(statement_end: date, last_txn_date: date) -> date:
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


def existing_assertion_dates(account: str) -> set[date]:
    """Dates of every balance assertion already recorded for an account in
    ledger/*.beancount — commented suggestions included, so a re-import
    neither duplicates an assertion nor re-suggests one (re-importing an
    already-imported file must not quietly advance the assertion line)."""
    out: set[date] = set()
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


def emit(when: date, payee: str, meta: Mapping[str, str], account: str,
         amt: Decimal, counter: str) -> str:
    """Render one Beancount transaction (metadata values are escaped too —
    they come from the same untrusted statement fields as the payee)."""
    lines = [f'{when} * "{escape(payee)}" ""']
    for k, v in meta.items():
        if v:
            lines.append(f'  {k}: "{escape(str(v))}"')
    lines.append(f"  {account}   {amt:.2f} USD")
    lines.append(f"  {counter}")
    return "\n".join(lines) + "\n"


def render_assertion(account: str, closing: Decimal, when: date, verified: bool) -> str:
    """A closing-balance assertion — active only when continuity anchored on
    the ledger VERIFIED it (an unanchored assertion would fail bean-check on
    any vault whose history starts mid-stream); otherwise a commented
    suggestion the user can enable once the opening balance is padded."""
    line = f"{when} balance {account}   {closing:.2f} USD"
    if verified:
        return line + "\n"
    return f"; {line}  (unverified against ledger — enable once the opening balance is reconciled)\n"


# ------------------------------------------------------------------- write
def atomic_write(path: Path, text: str) -> None:
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


def append_to_ledger(entries: list[Entry]) -> list[str]:
    """Append rendered entries to ledger/<year>.beancount (grouped by date),
    keep main.beancount's include list complete, then bean-check. Any
    validation failure rolls every touched file back — a bad import must
    never leave the ledger broken. Returns the vault-relative paths written.
    """
    ledger_dir = VAULT / "ledger"
    main = ledger_dir / "main.beancount"
    if not main.exists():
        raise SystemExit(f"no ledger at {main} — is FINANCE_VAULT right?")
    by_year: dict[int, list[str]] = {}
    for when, text in entries:
        by_year.setdefault(when.year, []).append(text)
    backups: dict[Path, str | None] = {}
    written: list[Path] = []
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
        raise SystemExit(f"write failed, rolled back: {e}") from e
    _bean_check_or_rollback(backups, main)
    return [str(p.relative_to(VAULT)) for p in written]


def _bean_check_or_rollback(backups: dict[Path, str | None], main: Path) -> None:
    if BEAN_CHECK.exists():
        out = subprocess.run([str(BEAN_CHECK), str(main)], capture_output=True, text=True)
        if out.returncode != 0:
            _restore(backups)
            raise SystemExit("bean-check rejected the import — rolled back:\n"
                             + (out.stderr or out.stdout).strip())
    else:
        print(f"; warning: bean-check not found at {BEAN_CHECK} — appended without validation",
              file=sys.stderr)


def _restore(backups: dict[Path, str | None]) -> None:
    for p, text in backups.items():
        if text is None:
            p.unlink(missing_ok=True)
        else:
            atomic_write(p, text)


# ----------------------------------------------- source-id addressed edits
def find_entries_by_source_id(ids: set[str]) -> dict[str, tuple[Path, str]]:
    """{source_id: (ledger file, full entry text)} for every machine-imported
    entry whose `fitid:`/`plaid-id:` is in `ids`. Fuel for the ingest
    daemon's modified/removed reporting — removals are ONLY ever reported."""
    out: dict[str, tuple[Path, str]] = {}
    for f in sorted((VAULT / "ledger").glob("*.beancount")):
        try:
            text = f.read_text()
        except OSError:
            continue
        for m in TXN_BLOCK.finditer(text):
            fm = SOURCE_ID_META.search(m.group(1))
            if fm and fm.group(1).strip() in ids:
                out[fm.group(1).strip()] = (f, m.group(0))
    return out


def replace_by_source_id(replacements: dict[str, str]) -> list[str]:
    """Swap whole ledger entries in place, addressed by source id — the
    upstream (Plaid `modified`) corrected a transaction it already delivered.
    Each replacement must target exactly one existing entry; the swap is
    atomic per file and bean-check rolls back everything on failure.
    Returns the vault-relative paths rewritten.
    """
    located = find_entries_by_source_id(set(replacements))
    missing = sorted(set(replacements) - set(located))
    if missing:
        raise SystemExit(f"replace_by_source_id: no ledger entry carries source id(s) "
                         f"{', '.join(missing)} — nothing rewritten")
    by_file: dict[Path, list[tuple[str, str]]] = {}
    for sid, (f, old_text) in located.items():
        by_file.setdefault(f, []).append((old_text, replacements[sid]))
    main = VAULT / "ledger" / "main.beancount"
    backups: dict[Path, str | None] = {}
    try:
        for f, swaps in by_file.items():
            backups[f] = text = f.read_text()
            for old_text, new_text in swaps:
                if text.count(old_text) != 1:
                    raise SystemExit(f"replace_by_source_id: entry not uniquely "
                                     f"addressable in {f.name} — nothing rewritten")
                text = text.replace(old_text, new_text if new_text.endswith("\n")
                                    else new_text + "\n")
            atomic_write(f, text)
    except OSError as e:
        _restore(backups)
        raise SystemExit(f"rewrite failed, rolled back: {e}") from e
    _bean_check_or_rollback(backups, main)
    return [str(p.relative_to(VAULT)) for p in by_file]
