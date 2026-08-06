#!/usr/bin/env python3
"""Sync every configured Plaid item into the vault (report-only by default).

SHIM — the daemon lives in the `sara` package (skills/finance/sara/sara/
ingest.py); this wrapper keeps the `tools/run ingest.py` invocation working.
Flags: [--write] [--item <alias>] [--verbose]. Run the module for the full
usage text, config schema, and the verification-report contract.
"""
import sys
from pathlib import Path

SARA_DIR = Path(__file__).resolve().parent.parent / "sara"
sys.path.insert(0, str(SARA_DIR))

try:
    from sara.ingest import main
except ImportError as exc:  # an existing vault venv that predates the package
    raise SystemExit(
        f"the sara package (and its dependencies) aren't importable from this "
        f"python ({exc}).\nOne-time fix for an existing vault:\n"
        f"  <your-vault>/.venv/bin/pip install -e {SARA_DIR}\n"
        f"(scripts/doctor.sh checks this and prints the exact command)") from exc

if __name__ == "__main__":
    main()
