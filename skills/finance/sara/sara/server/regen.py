"""Background report regeneration — the write side's refresh, single-flight.

After an action that rewrites the ledger (teach-a-rule, an import), the
snapshot and the analytics DB are stale until ``run_checks.py`` +
``reports.py`` rerun. This module kicks that regeneration in a background
thread (never blocking the action's response) and exposes its status so the
frontend can show a quiet "refreshing" state and refetch when the stamps
move. The mtime watchers in readmodel.py pick up the fresh artifacts
automatically — nothing here reloads anything by hand.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime

from sara.vault import VAULT


@dataclass
class _State:
    running: bool = False
    last_started: str | None = None
    last_finished: str | None = None
    last_ok: bool | None = None
    last_error: str = ""
    lock: threading.Lock = field(default_factory=threading.Lock)


_STATE = _State()
_TOOLS = ("run_checks", "reports")  # sara.advisor modules, run as -m
# One regeneration at a time IN THIS PROCESS: concurrent reports.py runs on
# the same vault would race each other's analytics tmp files.
_RUN_LOCK = threading.Lock()


def _execute() -> tuple[bool, str]:
    with _RUN_LOCK:
        for tool in _TOOLS:
            proc = subprocess.run(
                [sys.executable, "-m", f"sara.advisor.{tool}"],
                capture_output=True, text=True, cwd=str(VAULT),
                env={**os.environ, "FINANCE_VAULT": str(VAULT)},
            )
            if proc.returncode != 0:
                tail = (proc.stderr or proc.stdout).strip().splitlines()
                return False, f"{tool}: {tail[-1] if tail else 'no output'}"
    return True, ""


def _run() -> None:
    ok, error = _execute()
    with _STATE.lock:
        _STATE.running = False
        _STATE.last_finished = datetime.now().isoformat(timespec="seconds")
        _STATE.last_ok = ok
        _STATE.last_error = error


def run_sync() -> tuple[bool, str]:
    """Regenerate on the caller's thread, serialized with any background
    run — the upload confirm streams around this call."""
    return _execute()


def kick() -> bool:
    """Start a regeneration unless one is already running. Returns whether
    a new run started."""
    with _STATE.lock:
        if _STATE.running:
            return False
        _STATE.running = True
        _STATE.last_started = datetime.now().isoformat(timespec="seconds")
        _STATE.last_ok = None
        _STATE.last_error = ""
    threading.Thread(target=_run, name="sara-regen", daemon=True).start()
    return True


def status() -> dict[str, object]:
    with _STATE.lock:
        return {
            "running": _STATE.running,
            "last_started": _STATE.last_started,
            "last_finished": _STATE.last_finished,
            "last_ok": _STATE.last_ok,
            "last_error": _STATE.last_error,
        }
