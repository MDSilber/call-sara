"""Sync every configured Plaid item into the vault — Sara's ingest daemon.

Usage:
  python -m sara.ingest [--write] [--item <alias>] [--verbose]
  (or: tools/run ingest.py — same flags)

REPORT-ONLY BY DEFAULT: fetches, maps, dedupes, and prints the verification
report below, writing NOTHING. The staged-trust ramp is deliberately short —
run it once, read the report, then flip --write: dedupe is exact
(plaid-id primary), the writer bean-checks and rolls back, and every count
reconciles before a single entry lands. --write applies through the single
writer, saves sync cursors, regenerates reports, and commits the vault
(pushing if a remote is configured).

CONFIG — $VAULT/rules.toml, aggregator-agnostic shape ([sources.<kind>]):

  [sources.plaid.items.ally]
  access_token_env = "PLAID_ALLY_ACCESS_TOKEN"   # var lives in .secrets/plaid.env
  products = ["transactions"]                     # add "investments" for brokerages
  [sources.plaid.items.ally.accounts]
  "<plaid_account_id>" = "Assets:US:Ally:Checking1234"

  sara.link prints this block ready to paste after each successful link.
  Credentials: $VAULT/.secrets/plaid.env holds PLAID_CLIENT_ID, PLAID_SECRET,
  optional PLAID_ENV (production|sandbox), and one access-token var per item
  (0600, .secrets/ gitignored). Cursors: $VAULT/.secrets/plaid-cursors.json,
  advanced ONLY after a successful --write so a crash can never skip data.

THE VERIFICATION REPORT — data integrity is the contract:
  * per item: fetched vs mapped vs pending-excluded vs unmapped counts must
    reconcile EXACTLY; a mismatch aborts (exit 2) and blocks --write.
  * every non-imported row is listed with a reason (pending, unmapped with
    its payload reference, deduped by stage, unrouted account). Nothing is
    ever dropped silently.
  * per account: entry count + sum of amounts that would be written, and a
    balance line comparing Plaid's reported current balance (sign-adjusted
    for liabilities) against the ledger balance after this import — MATCH or
    DELTA, with pending netted into the explanation. A DELTA never blocks
    (it is the pre-vault-history case, like the invest MISMATCH) but prints
    the opening-balance seed recipe; bean-check remains the final wall.
  * upstream `modified` entries are replaced in place by plaid-id;
    `removed` ids are reported LOUDLY and never auto-deleted.
  * pending transactions are excluded by design: ids/amounts are not final
    until they post (the posted row arrives with its own id later).

TEST SEAM: SARA_PLAID_FIXTURE=<dir> reads <alias>.sync.json /
<alias>.investments.json / <alias>.holdings.json instead of the network —
how the golden tests drive this exact pipeline end to end.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sara.cli.shared import err, reject_unknown_flags
from sara.ledger.invest import build, cash_amount, payee_for, reconcile
from sara.ledger.queries import ledger_balance_asof
from sara.ledger.writer import (
    FAMILY_PLAID,
    TOLERANCE,
    AccountDedupe,
    Entry,
    append_to_ledger,
    emit,
    existing_ids,
    find_entries_by_source_id,
    replace_by_source_id,
)
from sara.plaid_api import (
    PlaidCreds,
    api_error_summary,
    get_investments,
    make_client,
    sync_transactions,
)
from sara.rules import categorize
from sara.sources.model import CanonInvestTxn, CanonTxn
from sara.sources.plaid_src import PlaidAccount, map_investments, map_sync_response
from sara.typed import as_dict, as_dicts, as_list
from sara.vault import (
    PLAID_CURSORS_FILE,
    PLAID_ENV_FILE,
    VAULT,
    check_secret_permissions,
    load_env_file,
    require_vault,
    rules,
    write_secret_file,
)

FLAGS = frozenset({"--write", "--verbose"})
ZERO = Decimal(0)
INVEST_FIRST_LOOKBACK_DAYS = 730  # first sync reaches back two years
INVEST_OVERLAP_DAYS = 5  # re-fetch overlap; dedupe makes it free


# ------------------------------------------------------------------ config
def plaid_items() -> dict[str, dict[str, Any]]:
    items = as_dict(as_dict(as_dict(rules().get("sources")).get("plaid")).get("items"))
    return {alias: as_dict(cfg) for alias, cfg in items.items()}


def merged_env() -> dict[str, str]:
    """plaid.env overlaid by the process environment (explicit wins)."""
    env = load_env_file(PLAID_ENV_FILE)
    env.update({k: v for k, v in os.environ.items() if k.startswith("PLAID_")})
    return env


def load_cursors() -> dict[str, Any]:
    if PLAID_CURSORS_FILE.is_file():
        try:
            data = as_dict(json.loads(PLAID_CURSORS_FILE.read_text()))
            if data:
                return data
        except ValueError:
            err(f"; WARNING: {PLAID_CURSORS_FILE} is unreadable JSON — starting cursors fresh")
    return {"version": 1, "items": {}}


def save_cursors(cursors: dict[str, Any]) -> None:
    write_secret_file(PLAID_CURSORS_FILE, json.dumps(cursors, indent=2, sort_keys=True) + "\n")


# ------------------------------------------------------------- fixture seam
def _fixture_dir() -> Path | None:
    d = os.environ.get("SARA_PLAID_FIXTURE")
    return Path(d) if d else None


def _fixture_json(path: Path) -> Any:
    return json.loads(path.read_text()) if path.is_file() else None


# ------------------------------------------------------------------ report
class ItemRun:
    """Everything one item's sync produced, ready to report and apply."""

    def __init__(self, alias: str) -> None:
        self.alias = alias
        self.lines: list[str] = []  # the item's section of the report
        self.new_entries: list[Entry] = []
        self.replacements: dict[str, str] = {}
        self.next_cursor: str | None = None
        self.invest_through: str | None = None
        self.integrity_ok = True
        self.hard_error: str | None = None

    def add(self, line: str = "") -> None:
        self.lines.append(line)


def _plaid_account_label(a: PlaidAccount) -> str:
    mask = f" ...{a.mask}" if a.mask else ""
    return f"{a.name}{mask}".strip() or a.account_id


def _sum(amounts: list[Decimal]) -> Decimal:
    return sum(amounts, ZERO)


def _balance_line(account: str, plaid_acct: PlaidAccount | None,
                  written_sum: Decimal, pending_note: str) -> str:
    if plaid_acct is not None and plaid_acct.type == "investment":
        # Plaid's "current" for a brokerage is MARKET VALUE; the ledger's USD
        # balance is cash. Comparing them would manufacture a fake DELTA —
        # the positions reconcile below is this account's real integrity check.
        return ("    balance: investment account — Plaid reports market value; "
                "the positions reconcile below is the check that matters")
    if plaid_acct is None or plaid_acct.ledger_signed_current() is None:
        return "    balance: plaid reported no current balance — UNVERIFIABLE"
    want = plaid_acct.ledger_signed_current()
    assert want is not None
    prior, n = ledger_balance_asof(account, date.today())
    if prior is None:
        return "    balance: ledger not queryable (vault venv missing?) — UNVERIFIABLE"
    have = prior + written_sum
    delta = want - have
    if abs(delta) <= TOLERANCE:
        return f"    balance: plaid {want:,.2f} vs ledger after import {have:,.2f} — MATCH"
    hints = [pending_note] if pending_note else []
    hints.append(
        "no ledger history yet — seed a dated opening balance (Equity:Opening-Balances) "
        f"for exactly {delta:,.2f}" if n == 0 else
        f"seed/adjust an opening balance for {delta:,.2f}, or pull older history")
    return (f"    balance: plaid {want:,.2f} vs ledger after import {have:,.2f} — "
            f"DELTA {delta:+,.2f} ({'; '.join(hints)})")


# ------------------------------------------------------------------- sync
def run_item(alias: str, cfg: dict[str, Any], env: dict[str, str],
             cursors: dict[str, Any], verbose: bool) -> ItemRun:
    run = ItemRun(alias)
    account_map: dict[str, str] = {k: str(v) for k, v in as_dict(cfg.get("accounts")).items()}
    products = [str(p) for p in as_list(cfg.get("products"))] or ["transactions"]
    token_var = str(cfg.get("access_token_env") or "")
    token = env.get(token_var, "")
    fixture = _fixture_dir()
    if not fixture and (not token_var or not token):
        run.hard_error = (f"[{alias}] no access token: set {token_var or 'access_token_env'} "
                          f"in {PLAID_ENV_FILE} (sara.link writes it)")
        return run
    item_cursors = as_dict(as_dict(cursors.get("items")).get(alias))
    cursor = str(item_cursors.get("cursor") or "")

    # ---- fetch ----
    try:
        if fixture:
            pages = as_list(_fixture_json(fixture / f"{alias}.sync.json"))
            inv_pages = as_list(_fixture_json(fixture / f"{alias}.investments.json"))
            holdings = as_dict(_fixture_json(fixture / f"{alias}.holdings.json"))
        else:
            creds = PlaidCreds(client_id=env.get("PLAID_CLIENT_ID", ""),
                               secret=env.get("PLAID_SECRET", ""),
                               environment=env.get("PLAID_ENV", "production"))
            if not creds.client_id or not creds.secret:
                run.hard_error = (f"[{alias}] PLAID_CLIENT_ID / PLAID_SECRET missing — "
                                  f"put them in {PLAID_ENV_FILE}")
                return run
            client = make_client(creds)
            pages = sync_transactions(client, token, cursor)
            inv_pages, holdings = [], None
            if "investments" in products:
                through = str(item_cursors.get("investments_through") or "")
                start = (datetime.strptime(through, "%Y-%m-%d").date()
                         - timedelta(days=INVEST_OVERLAP_DAYS)) if through else \
                    (date.today() - timedelta(days=INVEST_FIRST_LOOKBACK_DAYS))
                inv_pages, holdings = get_investments(client, token, start, date.today())
    except SystemExit:
        raise
    except Exception as e:  # ApiException etc. — one item must not sink the run
        run.hard_error = f"[{alias}] Plaid API error — {api_error_summary(e)}"
        return run

    batch = map_sync_response(as_dicts(pages))
    run.next_cursor = batch.next_cursor or cursor or None
    plaid_accts = {a.account_id: a for a in batch.accounts}

    cursor_note = "first sync (no cursor)" if not cursor else "resumed"
    synced = item_cursors.get("last_synced")
    if synced:
        cursor_note += f" (last synced {str(synced)[:10]})"
    run.add(f"[{alias}] cursor: {cursor_note}")
    run.add(f"  fetched: {batch.fetched_added} added, {batch.fetched_modified} modified, "
            f"{len(batch.removed_ids)} removed -> mapped {len(batch.added) + len(batch.modified)}"
            f" | pending excluded {len(batch.excluded_pending)}"
            f" | unmapped {len(batch.unmapped)}"
            + ("  ✓ counts reconcile" if batch.reconciles()
               else "  ✗ COUNTS DO NOT RECONCILE — refusing to write"))
    if not batch.reconciles():
        run.integrity_ok = False
    for u in batch.unmapped:
        run.add(f"    UNMAPPED (never silent): {u.raw_ref} — {u.reason}")

    # ---- route + dedupe + render, per configured account ----
    ledger_hashes, ledger_sids = existing_ids()
    known_sids = {s for sids in ledger_sids.values() for s in sids}
    by_plaid_id: dict[str, list[CanonTxn]] = {}
    unrouted: dict[str, int] = {}
    mods_replace: list[CanonTxn] = []
    for t in batch.added:
        if t.account_key in account_map:
            by_plaid_id.setdefault(t.account_key, []).append(t)
        else:
            unrouted[t.account_key] = unrouted.get(t.account_key, 0) + 1
    for t in batch.modified:
        if t.account_key not in account_map:
            unrouted[t.account_key] = unrouted.get(t.account_key, 0) + 1
        elif t.source_id in known_sids:
            mods_replace.append(t)
        else:  # corrected upstream before we ever imported it -> a plain add
            by_plaid_id.setdefault(t.account_key, []).append(t)

    def render_txn(t: CanonTxn, account: str, h: str) -> str:
        counter = categorize(t.rule_text, t.kind, t.amount, account)
        meta: dict[str, str] = {"plaid-type": t.kind, "import-hash": h,
                                "plaid-id": t.source_id}
        meta.update({k: v for k, v in t.meta})
        return emit(t.date, t.payee, meta, account, t.amount, counter)

    # Dedupe state is per LEDGER account and shared when several Plaid
    # accounts route to one; the report stays per Plaid account so every
    # configured account gets its balance line, activity or not.
    dedupers: dict[str, AccountDedupe] = {}
    ledger_targets: dict[str, int] = {}
    for account in account_map.values():
        ledger_targets[account] = ledger_targets.get(account, 0) + 1
    for pid, account in sorted(account_map.items(), key=lambda kv: (kv[1], kv[0])):
        txns = sorted(by_plaid_id.get(pid, []), key=lambda t: t.date)
        deduper = dedupers.setdefault(
            account, AccountDedupe(account, ledger_hashes, ledger_sids))
        kept: list[CanonTxn] = []
        skipped: list[tuple[CanonTxn, str]] = []
        for t in txns:
            h = deduper.hash_for(t.date, t.amount, t.payee)
            why = deduper.check(t.date, t.amount, t.payee, t.source_id, h)
            if why:
                skipped.append((t, why))
                continue
            deduper.record(h, t.source_id)
            run.new_entries.append((t.date, render_txn(t, account, h)))
            kept.append(t)
        plaid_acct = plaid_accts.get(pid)
        label = f" <- plaid {_plaid_account_label(plaid_acct)}" if plaid_acct else ""
        run.add(f"  {account}{label}")
        run.add(f"    new {len(kept)} (sum {_sum([t.amount for t in kept]):,.2f} USD), "
                f"deduped {len(skipped)}"
                + (f" ({', '.join(sorted({w for _, w in skipped}))})" if skipped else ""))
        for t, why in skipped:
            run.add(f"      deduped ({why}) {t.date} {t.amount:.2f} {t.payee}")
        if ledger_targets[account] > 1:
            run.add("    balance: multiple Plaid accounts route to this ledger account "
                    "— compare by hand")
            continue
        pend = len(batch.excluded_pending)
        pending_note = (f"{pend} pending excluded (post later)" if pend else "")
        run.add(_balance_line(account, plaid_acct, _sum([t.amount for t in kept]),
                              pending_note))

    # modified -> replacements, by plaid-id
    for t in mods_replace:
        account = account_map[t.account_key]
        h = AccountDedupe(account, ledger_hashes, ledger_sids, enabled=False) \
            .hash_for(t.date, t.amount, t.payee)
        run.replacements[t.source_id] = render_txn(t, account, h)
    if mods_replace:
        run.add(f"  modified upstream -> replace in place by plaid-id: "
                f"{len(mods_replace)} entr{'y' if len(mods_replace) == 1 else 'ies'}")
        for t in mods_replace:
            run.add(f"    ~ {t.date} {t.amount:.2f} {t.payee} [{t.source_id}]")

    # removed -> LOUD report, never delete
    if batch.removed_ids:
        located = find_entries_by_source_id(set(batch.removed_ids))
        for rid in batch.removed_ids:
            if rid in located:
                f, snippet = located[rid]
                run.add(f"  REMOVED UPSTREAM but present in {f.name} — NOT deleted; "
                        f"review by hand: {snippet.splitlines()[0]}")
            else:
                run.add(f"  removed upstream, never imported (likely pending): {rid} — nothing to do")

    for pid, n in list(unrouted.items()):
        run.add(f"  UNROUTED plaid account {pid}: {n} transaction(s) not imported — add it "
                f"under [sources.plaid.items.{alias}.accounts] in rules.toml")

    # ---- investments ----
    if inv_pages or holdings:
        inv = map_investments(as_dicts(inv_pages), holdings or None)
        run.add(f"  investments: fetched {inv.fetched} -> mapped {len(inv.actions)}"
                f" | unmapped {len(inv.unmapped)}"
                + ("  ✓ counts reconcile" if inv.reconciles()
                   else "  ✗ COUNTS DO NOT RECONCILE — refusing to write"))
        if not inv.reconciles():
            run.integrity_ok = False
        for u in inv.unmapped:
            run.add(f"    UNMAPPED (never silent): {u.raw_ref} — {u.reason}")
        inv_routed: dict[str, list[CanonInvestTxn]] = {}
        for a in inv.actions:
            acct = account_map.get(a.account_key)
            if acct:
                inv_routed.setdefault(acct, []).append(a)
            else:
                run.add(f"    UNROUTED plaid account {a.account_key}: investment row "
                        f"{a.date} not imported — add it to rules.toml")
        for account in sorted(inv_routed):
            deduper = dedupers.setdefault(
                account, AccountDedupe(account, ledger_hashes, ledger_sids))
            kept_units: dict[str, Decimal] = {}
            kept_sum = ZERO
            kept_n, first_activity = 0, None
            skipped_n = 0
            for a in sorted(inv_routed[account], key=lambda x: x.date):
                payee = payee_for(a)
                amt = cash_amount(a)
                h = deduper.hash_for(a.date, amt, payee)
                why = deduper.check_invest(a.date, amt, payee, a.source_id,
                                           ticker=a.ticker, units=a.units,
                                           family=FAMILY_PLAID, h=h)
                if why:
                    skipped_n += 1
                    run.add(f"      deduped ({why}) {a.date} {amt:.2f} {payee}")
                    continue
                deduper.record(h, a.source_id)
                entry, deltas, _used = build(a, account, payee, h)
                # Plaid provenance belongs in plaid-* metadata, not the OFX keys
                entry = entry.replace('  fitid: "', '  plaid-id: "') \
                             .replace('  ofx-type: "', '  plaid-type: "')
                run.new_entries.append((a.date, entry))
                kept_n += 1
                kept_sum += amt
                first_activity = first_activity or a.date
                for tk, u in deltas.items():
                    kept_units[tk] = kept_units.get(tk, ZERO) + u
            run.add(f"    {account}: new {kept_n} (cash effect {kept_sum:,.2f} USD), "
                    f"deduped {skipped_n}")
            positions = [p for pid, plist in inv.positions.items()
                         if account_map.get(pid) == account for p in plist]
            if positions:
                stated: dict[str, Decimal] = {}
                for p in positions:
                    if p.ticker:
                        stated[p.ticker] = stated.get(p.ticker, ZERO) + p.units
                _tag, _matched, lines = reconcile(account, stated, date.today(),
                                                  kept_units, first_activity)
                run.lines.extend("    " + line.lstrip("; ").rstrip()
                                 for line in lines)
        run.invest_through = date.today().isoformat()

    if verbose and run.new_entries:
        run.add("  --- entries ---")
        for _, e in sorted(run.new_entries):
            run.lines.extend("  " + line for line in e.splitlines())
    return run


# -------------------------------------------------------------- afterparty
def regenerate_reports() -> None:
    """reports.py + run_checks.py under the vault venv, best effort."""
    tools = Path(__file__).resolve().parents[2] / "tools"
    py = VAULT / ".venv" / "bin" / "python"
    if not tools.is_dir() or not py.exists():
        err("; note: tools/ or vault venv not found — reports not regenerated")
        return
    for tool in ("run_checks.py", "reports.py"):
        r = subprocess.run([str(py), str(tools / tool)], capture_output=True, text=True,
                           env={**os.environ, "FINANCE_VAULT": str(VAULT)})
        if r.returncode != 0:
            err(f"; WARNING: {tool} failed after ingest — run it by hand "
                f"({(r.stderr or r.stdout).strip().splitlines()[-1] if (r.stderr or r.stdout).strip() else 'no output'})")


def ensure_secrets_ignored() -> None:
    """Never let .secrets/ reach the vault git history."""
    gi = VAULT / ".gitignore"
    text = gi.read_text() if gi.is_file() else ""
    if ".secrets" not in text:
        gi.write_text(text + ("" if text.endswith("\n") or not text else "\n") + ".secrets/\n")


def commit_vault(message: str) -> None:
    if not (VAULT / ".git").exists():
        err("; note: vault is not a git repo — nothing committed")
        return
    ensure_secrets_ignored()
    subprocess.run(["git", "-C", str(VAULT), "add", "-A"], capture_output=True)
    r = subprocess.run(["git", "-C", str(VAULT), "commit", "-m", message],
                       capture_output=True, text=True)
    if r.returncode != 0:
        err("; nothing to commit in the vault")
        return
    remotes = subprocess.run(["git", "-C", str(VAULT), "remote"],
                             capture_output=True, text=True).stdout.split()
    if remotes:
        p = subprocess.run(["git", "-C", str(VAULT), "push"], capture_output=True, text=True)
        err("; vault committed and pushed" if p.returncode == 0
            else "; vault committed (push failed — push by hand)")
    else:
        err("; vault committed (no remote configured)")


# ------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    usage = __doc__ or ""
    item_filter: str | None = None
    if "--item" in argv:
        i = argv.index("--item")
        item_filter = argv[i + 1] if i + 1 < len(argv) and not argv[i + 1].startswith("--") else None
        if not item_filter:
            raise SystemExit(f"--item needs an alias\n\n{usage}")
        argv = argv[:i] + argv[i + 2:]
    reject_unknown_flags(argv, FLAGS, usage)
    write = "--write" in argv
    verbose = "--verbose" in argv
    require_vault()
    items = plaid_items()
    if not items:
        raise SystemExit(
            "no [sources.plaid.items.<alias>] configured in rules.toml — link an "
            "institution first: python -m sara.link <alias>  (see references/fetching.md)")
    if item_filter:
        if item_filter not in items:
            raise SystemExit(f"no [sources.plaid.items.{item_filter}] in rules.toml "
                             f"(configured: {', '.join(sorted(items))})")
        items = {item_filter: items[item_filter]}
    for p in (PLAID_ENV_FILE, PLAID_CURSORS_FILE):
        warning = check_secret_permissions(p)
        if warning:
            err(f"; WARNING: {warning}")

    env = merged_env()
    cursors = load_cursors()
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = "WRITE" if write else "REPORT ONLY (run with --write to apply)"
    print(f"== sara ingest — {stamp} — {mode} ==")
    runs = [run_item(alias, cfg, env, cursors, verbose)
            for alias, cfg in sorted(items.items())]
    total_new = 0
    hard_errors: list[str] = []
    for run in runs:
        print()
        if run.hard_error:
            print(run.hard_error)
            hard_errors.append(run.hard_error)
            continue
        for line in run.lines:
            print(line)
        total_new += len(run.new_entries)
    print()
    integrity_bad = [r.alias for r in runs if not r.integrity_ok]
    if integrity_bad:
        raise SystemExit(2)  # the report above already shouted per item
    n_repl = sum(len(r.replacements) for r in runs)
    if not write:
        print(f"report only — {total_new} new entr{'y' if total_new == 1 else 'ies'}"
              + (f" + {n_repl} in-place replacement(s)" if n_repl else "")
              + " would be written. Re-run with --write to apply.")
        sys.exit(1 if hard_errors else 0)

    # ---- apply ----
    entries = [e for r in runs for e in r.new_entries]
    replacements = {sid: text for r in runs for sid, text in r.replacements.items()}
    wrote = []
    if entries:
        wrote = append_to_ledger(sorted(entries, key=lambda e: e[0]))
    if replacements:
        wrote += replace_by_source_id(replacements)
    if not entries and not replacements:
        print("nothing new to write.")
    else:
        print(f"wrote {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}"
              + (f" + {len(replacements)} replacement(s)" if replacements else "")
              + f" to {', '.join(sorted(set(wrote)))} (bean-check passed)")
    for run in runs:
        if run.hard_error:
            continue
        item = cursors.setdefault("items", {}).setdefault(run.alias, {})
        if run.next_cursor:
            item["cursor"] = run.next_cursor
        if run.invest_through:
            item["investments_through"] = run.invest_through
        item["last_synced"] = datetime.now().isoformat(timespec="seconds")
    save_cursors(cursors)
    regenerate_reports()
    commit_vault(f"sara ingest: {len(entries)} new"
                 + (f", {len(replacements)} replaced" if replacements else "")
                 + f" ({', '.join(sorted(r.alias for r in runs if not r.hard_error))})")
    sys.exit(1 if hard_errors else 0)


if __name__ == "__main__":
    main()
