#!/usr/bin/env python3
"""Identify, dedupe, rename, and file freshly-downloaded PDFs into the vault.

Reads PDFs from ~/Downloads (or a given dir), fingerprints each by content,
drops byte-identical duplicates (portals hand back the same file as "(1)"),
and prints a plan. It never guesses a destination silently — pass a mapping
or run interactively so a human confirms the moves.

Usage:
  file_downloads.py inspect [dir]            # show what's there + fingerprints
  file_downloads.py dedupe  [dir]            # delete byte-identical duplicates
  file_downloads.py move SRC DEST_DIR NAME   # rename to YYYY-MM-DD.NAME.pdf and file

NAME must start with the doc's effective date, e.g. "2025-06-12.iso-grant-ES-1957".
"""
import hashlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

DOWNLOADS = Path.home() / "Downloads"
PROBE = re.compile(
    r"grant number|account number|statement period|options \(|total number of|"
    r"vesting commencement|policy number|payer|1099|w-2", re.I)


def pdfs(d):
    return sorted(Path(d).glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)


def fingerprint(p):
    try:
        txt = subprocess.run(["pdftotext", "-l", "1", str(p), "-"],
                             capture_output=True, text=True).stdout
    except FileNotFoundError:
        return "(pdftotext not installed)"
    lines = [line.strip() for line in txt.splitlines() if PROBE.search(line)]
    return " | ".join(lines[:4]) or (txt.strip()[:120].replace("\n", " ") or "(no text)")


def md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def inspect(d):
    for p in pdfs(d):
        print(f"{md5(p)[:8]}  {p.name}\n          -> {fingerprint(p)}")


def dedupe(d):
    seen = {}
    # shortest name first, so "statement.pdf" is kept over "statement (1).pdf"
    for p in sorted(pdfs(d), key=lambda x: (len(x.name), x.name)):
        h = md5(p)
        if h in seen:
            print(f"dup  {p.name}  (== {seen[h].name}) -> deleted")
            p.unlink()
        else:
            seen[h] = p
    print(f"{len(seen)} unique remain")


def move(src, dest_dir, name):
    src, dest_dir = Path(src), Path(dest_dir)
    if not re.match(r"^\d{4}-\d{2}-\d{2}\.", name):
        sys.exit("NAME must start with YYYY-MM-DD. — the date makes documents/ sort right")
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / (name + src.suffix)
    if target.exists():
        sys.exit(f"refusing to overwrite {target}")
    shutil.move(str(src), str(target))  # rename fails across filesystems (Downloads -> cloud drive)
    print(f"filed {target}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "inspect":
        inspect(args[1] if len(args) > 1 else DOWNLOADS)
    elif args[0] == "dedupe":
        dedupe(args[1] if len(args) > 1 else DOWNLOADS)
    elif args[0] == "move" and len(args) == 4:
        move(args[1], args[2], args[3])
    else:
        sys.exit(__doc__)
