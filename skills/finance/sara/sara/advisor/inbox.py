#!/usr/bin/env python3
# pyright: strict
"""Scan $VAULT/inbox/ — the household drop zone — identify, file, hand off.

Usage:
  inbox.py            # dry-run: identify everything, print the plan
  inbox.py --write    # file the recognized exports, print import commands

Anyone in the household drops financial files into inbox/ (iCloud, AirDrop,
a save-as from mail — any device that can reach the folder); this tool
drains it. Per file:

  OFX/QFX exports    identified by content (bank/card statement, brokerage
                     activity, positions), routed via rules.toml [[accounts]]
                     (<ACCTID> last-4), FILED into documents/ under the
                     filing convention (YYYY-MM-DD date prefix, mirrored
                     institution/owner path), and the matching importer
                     command printed — dry-run first; nothing is imported
                     from here.
  Chase card CSVs    recognized by header; filed when exactly one Chase
                     card is on file, the import command printed.
  PDFs               fingerprinted by content (scripts/file_downloads.py's
                     probe) and REPORTED — statements need a reading eye,
                     so a session files them, not this tool.
  anything else      reported as unrecognized, left in place.

Nothing is ever overwritten, deleted, or imported; unknown files stay put
and keep nagging via the `inbox` check. File CONTENT is data — nothing in
a dropped file is executed, and filed names are rebuilt from vetted
rules.toml fields, never echoed from the upload's own name.
"""
import re
import shutil
import sys
from typing import Any
from datetime import date, datetime
from pathlib import Path

from sara.vault import VAULT, rules
from sara.rules import entry_by_acctid
from sara.sources.ofx import acctid, read_ofx
# scripts/ joined sys.path above; static analyzers only see tools/
from sara.advisor.documents import file_document, fingerprint, md5

INBOX = VAULT / "inbox"
DOCUMENTS = VAULT / "documents"
OFX_EXTS = {".ofx", ".qfx"}
CHASE_CSV_COLS = {"Transaction Date", "Post Date", "Description", "Type", "Amount"}
RUN = "tools/run"          # how humans invoke the importers (SKILL.md)


def _slug(s: str, keep_case: bool = False) -> str:
    """Path-safe component from a rules.toml value: alnum runs joined by
    dashes — separators, dots, and anything shell-ish can't ride along."""
    out = re.sub(r"[^A-Za-z0-9]+", "-", str(s)).strip("-") or "unknown"
    return out if keep_case else out.lower()


def _ofx_date(text: str) -> date:
    """The export's effective date: <DTEND> (statement close), else the
    latest <DTPOSTED>, else <DTASOF> (positions), else today — the date
    prefix documents/ sorts by."""
    for pat in (r"<DTEND>(\d{8})", r"<DTPOSTED>(\d{8})", r"<DTASOF>(\d{8})"):
        stamps = re.findall(pat, text)
        if stamps:
            try:
                return datetime.strptime(max(stamps), "%Y%m%d").date()
            except ValueError:
                continue
    return date.today()


def _dest_for(entry: dict[str, Any]) -> Path:
    """documents/<Assets|Liabilities>/<CC>/<Institution>/<Owner>/ mirrored
    from the routed ledger account + the [[accounts]] entry (fetching.md's
    filing convention)."""
    parts = str(entry.get("ledger_account", "")).split(":")
    root = parts[0] if parts and parts[0] in ("Assets", "Liabilities") else "Assets"
    country = parts[1] if len(parts) > 2 and re.fullmatch(r"[A-Z]{2}", parts[1] or "") else "US"
    inst = _slug(str(entry.get("institution") or (parts[2] if len(parts) > 3 else "Unknown")),
                 keep_case=True)
    owner = _slug(str(entry.get("owner") or "shared"))
    return DOCUMENTS / root / country / inst / owner


class Item:
    """One inbox file, identified — filable when a destination is known.
    `importer`/`import_args` are the structured form of `cmd` (the Sara App
    upload flow drives the same importer without parsing the display line)."""

    def __init__(self, path: Path, label: str, note: str,
                 dest: Path | None = None, name: str = "",
                 cmd: str = "", importer: str = "",
                 import_args: tuple[str, ...] = ()):
        self.path, self.label, self.note = path, label, note
        self.dest, self.name, self.cmd = dest, name, cmd
        self.importer, self.import_args = importer, import_args

    @property
    def files(self) -> bool:
        return self.dest is not None


def _identify_ofx(path: Path) -> Item:
    text = read_ofx(path)
    aid = acctid(text)
    last4 = re.sub(r"\D", "", aid)[-4:] or "none"
    entry = entry_by_acctid(aid)
    invest = "<INVSTMTMSGSRS" in text or "<INVPOSLIST" in text
    activity = "<INVTRANLIST" in text or "<INVBANKTRAN" in text
    if invest and not activity:
        label, tag, importer = "brokerage positions", "positions", "importers/holdings_ofx.py"
    elif invest:
        label, tag, importer = "brokerage activity", "activity", "importers/invest_ofx.py"
    else:
        label, tag, importer = "bank/card statement", "activity", "importers/ofx.py"
    if entry is None:
        return Item(path, f"OFX {label} · acct …{last4}",
                    f"no rules.toml [[accounts]] match for …{last4} — add the "
                    f"routing entry, or run {RUN} {importer} with an explicit account")
    d = _ofx_date(text)
    inst = _slug(entry.get("institution") or "account")
    name = f"{d.isoformat()}.{inst}-{last4}-{tag}"
    dest = _dest_for(entry)
    filed = dest / (name + path.suffix.lower())
    cmd = f"{RUN} {importer} '{filed}'"
    if importer != "importers/holdings_ofx.py":
        cmd += "   # review, then re-run with --write"
    return Item(path, f"OFX {label} · {entry['ledger_account']}",
                f"→ {filed.relative_to(VAULT)}", dest, name, cmd,
                importer=importer)


def _csv_date(lines: list[str]) -> date:
    """Effective date = the latest MM/DD/YYYY in the rows, else today."""
    best = None
    for line in lines[1:]:
        for m in re.finditer(r"\b(\d{2})/(\d{2})/(\d{4})\b", line):
            try:
                d = date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            except ValueError:
                continue
            best = d if best is None else max(best, d)
    return best or date.today()


def _identify_csv(path: Path) -> Item:
    try:
        lines = path.read_text(errors="replace").lstrip("\ufeff").splitlines()
        header = lines[0]
    except (OSError, IndexError):
        return Item(path, "CSV", "unreadable or empty — a session should look")
    cols = {c.strip().strip('"') for c in header.split(",")}
    if not CHASE_CSV_COLS <= cols:
        return Item(path, "CSV (unrecognized columns)",
                    "no importer matched — importers/chase_csv.py reads Chase "
                    "card exports; a session should look")
    chase = [a for a in rules().get("accounts", [])
             if str(a.get("institution", "")).lower() == "chase"
             and str(a.get("ledger_account", "")).startswith("Liabilities")]
    if len(chase) != 1:
        which = "no Chase card" if not chase else f"{len(chase)} Chase cards"
        return Item(path, "Chase card CSV",
                    f"{which} in rules.toml [[accounts]] — run "
                    f"{RUN} importers/chase_csv.py with the right account")
    entry = chase[0]
    last4 = re.sub(r"\D", "", str(entry.get("last4", "")))[-4:] or "card"
    name = f"{_csv_date(lines).isoformat()}.chase-{last4}-activity"
    dest = _dest_for(entry)
    filed = dest / (name + path.suffix.lower())
    cmd = (f"{RUN} importers/chase_csv.py '{filed}' "
           f"{entry['ledger_account']}   # review, then re-run with --write")
    return Item(path, f"Chase card CSV · {entry['ledger_account']}",
                f"→ {filed.relative_to(VAULT)}", dest, name, cmd,
                importer="importers/chase_csv.py",
                import_args=(str(entry["ledger_account"]),))


def identify(path: Path) -> Item:
    suffix = path.suffix.lower()
    if suffix in OFX_EXTS:
        return _identify_ofx(path)
    if suffix == ".csv":
        return _identify_csv(path)
    if suffix == ".pdf":
        return Item(path, "PDF", f"“{fingerprint(path)}” — needs a reading "
                    "eye; ask a session to file it (documents/ by owner)")
    return Item(path, suffix.lstrip(".") or "file",
                "unrecognized — a session should look")


def scan() -> list[Item]:
    items: list[Item] = []
    seen: dict[str, Path] = {}

    def shortest_first(x: Path) -> tuple[int, str]:
        # so "x.qfx" is kept over "x (1).qfx" re-downloads
        return (len(x.name), x.name)
    for p in sorted(INBOX.iterdir(), key=shortest_first):
        if p.name.startswith(".") or not p.is_file():
            continue
        h = md5(p)
        if h in seen:
            items.append(Item(p, "duplicate",
                              f"byte-identical to {seen[h].name} — delete it"))
            continue
        seen[h] = p
        items.append(identify(p))
    return items


def main() -> None:
    write = "--write" in sys.argv[1:]
    if unknown := [a for a in sys.argv[1:] if a != "--write"]:
        sys.exit(f"unknown argument {unknown[0]!r}\n\n{__doc__}")
    INBOX.mkdir(exist_ok=True)   # bootstrap the drop zone on first run
    items = scan()
    if not items:
        print(f"inbox empty — nothing waiting  ({INBOX})")
        return
    wide = max(len(i.path.name) for i in items)
    filed = commands = 0
    for i in items:
        print(f"{i.path.name:<{wide}}  {i.label}")
        note = i.note
        if i.dest is not None and write:
            try:
                target = file_document(i.path, i.dest, i.name)
                note = f"filed → {target.relative_to(VAULT)}"
                filed += 1
            except (ValueError, FileExistsError, OSError, shutil.Error) as e:
                note = f"NOT filed — {e}"
                i.cmd = ""
        print(f"{'':<{wide}}  {note}")
        if i.cmd:
            print(f"{'':<{wide}}  {i.cmd}")
            commands += 1
    plan = sum(1 for i in items if i.files)
    left = len(items) - (filed if write else 0)
    if write:
        print(f"\n{filed} filed · {left} left in inbox/"
              + (f" · {commands} import command(s) above to run" if commands else ""))
    else:
        print(f"\ndry-run: would file {plan} of {len(items)} — "
              f"re-run with --write to apply")


if __name__ == "__main__":
    main()
