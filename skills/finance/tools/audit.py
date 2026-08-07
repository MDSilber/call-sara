#!/usr/bin/env python3
"""Cross-source cash duplicate audit (report-only by default).

SHIM — the tool lives in the `sara` package (skills/finance/sara/sara/
audit.py); this wrapper keeps the `tools/run audit.py` invocation working.
Flags: [--write] [--min-amount 1.00]. Run the module for the full contract.
"""
import sys
from pathlib import Path

SARA_DIR = Path(__file__).resolve().parent.parent / "sara"
sys.path.insert(0, str(SARA_DIR))

try:
    from sara.audit import main
except ImportError as exc:  # an existing vault venv that predates the package
    raise SystemExit(
        f"the sara package (and its dependencies) aren't importable from this "
        f"python ({exc}).\nOne-time fix for an existing vault:\n"
        f"  <your-vault>/.venv/bin/pip install -e {SARA_DIR}\n"
        f"(scripts/doctor.sh checks this and prints the exact command)") from exc

if __name__ == "__main__":
    main()
