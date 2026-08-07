"""Drag-drop ingestion: file → inbox → plan → confirm → gated import.

The upload door accepts .ofx/.qfx/.csv/.pdf up to 15MB. The client's
filename is NEVER used in a filesystem path — the server writes
``inbox/upload-<uuid>.<ext>`` where the extension comes from the CONTENT
sniff (a sanitized slug of the claimed name survives only as display
metadata). Nothing uploaded is ever executed; file content is data.

Flow (two token-gated POSTs):

1. ``plan``     save to $VAULT/inbox/, identify through tools/inbox.py's own
                ``identify()`` (the same brain the folder drop zone uses),
                and — for a recognized statement — run the matching importer
                DRY, returning its full verification report (dedupe +
                continuity included). Nothing is written.
2. ``confirm``  file into documents/ under the vetted naming convention,
                run the importer ``--write`` (single audited writer,
                bean-check, rollback), then regenerate reports so the
                snapshot/DB watchers pick up the new numbers. Streams every
                step. Unknown documents (PDFs, unmatched files) file under
                ``documents/Unfiled/`` with a "Sara will read this" note.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast

from sara.vault import VAULT

from . import regen
from .actions import ActionError

MAX_BYTES = 15 * 1024 * 1024
PLAN_TTL_SECONDS = 60 * 60
ALLOWED_EXTS = (".ofx", ".qfx", ".csv", ".pdf")
INBOX = VAULT / "inbox"
UNFILED = VAULT / "documents" / "Unfiled"


def _slug(name: str) -> str:
    """Display-safe slug of the CLAIMED filename (metadata only, and the one
    sanitized fragment allowed into an Unfiled document name)."""
    stem = Path(name or "upload").stem
    out = re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()
    return out[:40] or "upload"


def _family(ext: str) -> str:
    return "ofx" if ext in (".ofx", ".qfx") else ext.lstrip(".")


def _agree(sniffed: str, claimed_ext: str) -> str:
    """Content wins the extension; a claimed extension from a DIFFERENT
    family is refused rather than silently rewritten."""
    if claimed_ext and _family(claimed_ext) != _family(sniffed):
        raise ActionError(
            f"the file says {claimed_ext} but its content is "
            f"{_family(sniffed).upper()} — rename or re-export it so the two "
            f"agree, and Sara will take it")
    return sniffed


def sniff(head: bytes, claimed_ext: str) -> str:
    """The validated extension, derived from CONTENT (the claimed extension
    only breaks ties inside a family). Raises on a mismatch or unknown."""
    upper = head[:4096].upper()
    if upper.startswith(b"%PDF"):
        return _agree(".pdf", claimed_ext)
    if b"<OFX" in upper or b"OFXHEADER" in upper:
        return _agree(claimed_ext if claimed_ext in (".ofx", ".qfx") else ".ofx",
                      claimed_ext)
    try:
        text = head[:4096].decode("utf-8", errors="strict")
    except UnicodeDecodeError as e:
        raise ActionError(
            "unrecognized file content — not OFX/QFX, not a PDF, and not "
            "text. Sara imports .ofx/.qfx/.csv statements and files .pdf "
            "documents.") from e
    first_line = text.splitlines()[0] if text.strip() else ""
    if claimed_ext == ".csv" and "," in first_line:
        return ".csv"
    if claimed_ext == ".csv":
        raise ActionError("that .csv has no comma-separated header row — "
                          "export the account's activity CSV and try again")
    raise ActionError(
        f"content does not match a supported statement type "
        f"(claimed {claimed_ext or 'no extension'}) — Sara imports "
        f".ofx/.qfx/.csv and files .pdf documents")


@dataclass
class UploadRecord:
    upload_id: str
    saved: Path
    ext: str
    display: str
    created: float
    files: bool
    label: str
    note: str
    dest: str | None
    name: str
    importer: str
    import_args: tuple[str, ...]
    plan_report: str
    confirmed: bool = False


@dataclass
class _Registry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    records: dict[str, UploadRecord] = field(
        default_factory=lambda: dict[str, UploadRecord]())


_REG = _Registry()


def _sweep() -> None:
    cutoff = time.time() - PLAN_TTL_SECONDS
    with _REG.lock:
        for k in [k for k, r in _REG.records.items() if r.created < cutoff]:
            del _REG.records[k]


def _inbox_mod() -> Any:
    """sara.advisor.inbox, imported lazily (heavier module)."""
    from sara.advisor import inbox
    return inbox


def _importer_module(importer: str) -> str:
    """inbox names importers by their historical CLI path
    ("importers/ofx.py"); the implementations live in sara.cli."""
    stem = importer.rsplit("/", 1)[-1].removesuffix(".py")
    return f"sara.cli.{stem}"


def _run_importer(importer: str, args: tuple[str, ...], target: Path,
                  write: bool) -> tuple[int, str]:
    argv = [sys.executable, "-m", _importer_module(importer), str(target), *args]
    if write:
        argv.append("--write")
    proc = subprocess.run(argv, capture_output=True, text=True, cwd=str(VAULT),
                          env={**os.environ, "FINANCE_VAULT": str(VAULT)})
    body = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr.strip() else "")
    return proc.returncode, body.strip()


# ---------------------------------------------------------------- the plan
def plan(data: bytes, claimed_name: str) -> dict[str, object]:
    """Save the upload under a server-generated name and return the filing
    plan + the importer's dry-run verification report."""
    _sweep()
    if len(data) == 0:
        raise ActionError("the upload arrived empty")
    if len(data) > MAX_BYTES:
        raise ActionError(f"file is over the {MAX_BYTES // (1024 * 1024)}MB cap")
    claimed_ext = Path(claimed_name or "").suffix.lower()
    if claimed_ext and claimed_ext not in ALLOWED_EXTS:
        raise ActionError(f"{claimed_ext} is not accepted — drop .ofx/.qfx/.csv "
                          f"statements or .pdf documents")
    ext = sniff(data, claimed_ext)
    upload_id = uuid.uuid4().hex[:12]
    INBOX.mkdir(exist_ok=True)
    saved = INBOX / f"upload-{upload_id}{ext}"
    saved.write_bytes(data)
    saved.chmod(0o600)

    inbox = _inbox_mod()
    item = inbox.identify(saved)
    display = _slug(claimed_name)
    plan_report = ""
    importer = str(getattr(item, "importer", "") or "")
    import_args = tuple(str(a) for a in getattr(item, "import_args", ()))
    if item.files and importer:
        code, plan_report = _run_importer(importer, import_args, saved, write=False)
        if code != 0:
            # the importer refused the dry run — surface it, keep nothing
            saved.unlink(missing_ok=True)
            raise ActionError(f"the importer refused this file:\n{plan_report}")
    rec = UploadRecord(
        upload_id=upload_id, saved=saved, ext=ext, display=display,
        created=time.time(), files=bool(item.files and importer),
        label=str(item.label), note=str(item.note),
        dest=str(item.dest) if item.dest else None, name=str(item.name),
        importer=importer, import_args=import_args, plan_report=plan_report)
    with _REG.lock:
        _REG.records[upload_id] = rec
    unknown_note = ("Sara will read this next time you call her — it files "
                    "safely under documents/Unfiled/ for now."
                    if not rec.files else "")
    return {
        "upload_id": upload_id,
        "kind": ext.lstrip("."),
        "display": display,
        "label": rec.label,
        "note": rec.note,
        "recognized": rec.files,
        "files_to": (str(Path(rec.dest).relative_to(VAULT) / (rec.name + ext))
                     if rec.dest else f"documents/Unfiled/"
                     f"{date.today().isoformat()}.{display}-{upload_id[:6]}{ext}"),
        "import_cmd": rec.importer or None,
        "report": rec.plan_report,
        "unknown_note": unknown_note,
    }


# ------------------------------------------------------------- the confirm
def confirm_stream(upload_id: str) -> Iterator[str]:
    """Apply a planned upload: file it, import --write, regenerate."""
    with _REG.lock:
        rec = _REG.records.get(upload_id)
        if rec is not None and rec.confirmed:
            rec = None
        if rec is not None:
            rec.confirmed = True
    if rec is None:
        yield "✗ unknown or already-applied upload — drop the file again\n"
        return
    if not rec.saved.is_file():
        yield "✗ the staged file vanished from inbox/ — drop it again\n"
        return
    inbox = _inbox_mod()
    if rec.files and rec.dest:
        yield f"filing → {Path(rec.dest).relative_to(VAULT) / (rec.name + rec.ext)}\n"
        try:
            filed = cast(Path, inbox.file_document(rec.saved, Path(rec.dest), rec.name))
        except (ValueError, FileExistsError, OSError, shutil.Error) as e:
            yield f"✗ not filed — {e}\n"
            return
        yield f"importing via {rec.importer} --write\n\n"
        code, report = _run_importer(rec.importer, rec.import_args, filed, write=True)
        yield report + "\n"
        if code != 0:
            yield "\n✗ import refused — the ledger is unchanged (see the report above)\n"
            return
        yield "\nregenerating reports (checks, pages, summary, analytics)…\n"
        ok, error = regen.run_sync()
        if not ok:
            yield f"⚠ regeneration hiccup: {error}\n"
        yield "\n✓ imported and verified — every room refreshes on its own\n"
    else:
        UNFILED.mkdir(parents=True, exist_ok=True)
        target = (UNFILED /
                  f"{date.today().isoformat()}.{rec.display}-{upload_id[:6]}{rec.ext}")
        if target.exists():
            yield f"✗ {target.name} already exists — nothing moved\n"
            return
        shutil.move(str(rec.saved), target)
        yield (f"filed → {target.relative_to(VAULT)}\n"
               f"✓ Sara will read this next time you call her — no numbers "
               f"changed\n")
