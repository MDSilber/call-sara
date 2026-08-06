#!/usr/bin/env python3
"""Import a Chase credit-card CSV export into Beancount transactions.

DEPRECATED SHIM — the importer now lives in the `sara` package
(skills/finance/sara/sara/cli/chase_csv.py); this wrapper keeps the
historical `tools/run importers/chase_csv.py` invocation working unchanged.
Flags, output, and behavior are identical.
"""
import sys
from pathlib import Path

SARA_DIR = Path(__file__).resolve().parent.parent.parent / "sara"
sys.path.insert(0, str(SARA_DIR))

try:
    from sara.cli.chase_csv import main
except ImportError as exc:  # an existing vault venv that predates the package
    raise SystemExit(
        f"the sara package (and its dependencies) aren't importable from this "
        f"python ({exc}).\nOne-time fix for an existing vault:\n"
        f"  <your-vault>/.venv/bin/pip install -e {SARA_DIR}\n"
        f"(scripts/doctor.sh checks this and prints the exact command)") from exc

if __name__ == "__main__":
    main()
