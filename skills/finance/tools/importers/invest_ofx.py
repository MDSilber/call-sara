#!/usr/bin/env python3
"""Import brokerage ACTIVITY from an investment OFX/QFX into Beancount.

DEPRECATED SHIM — the importer now lives in the `sara` package
(skills/finance/sara/sara/cli/invest_ofx.py); this wrapper keeps the
historical `tools/run importers/invest_ofx.py` invocation working
unchanged. Flags, output, booking dialect, and the positions reconcile are
identical; run the module for the full usage text.
"""
import sys
from pathlib import Path

SARA_DIR = Path(__file__).resolve().parent.parent.parent / "sara"
sys.path.insert(0, str(SARA_DIR))

try:
    from sara.cli.invest_ofx import main
except ImportError as exc:  # an existing vault venv that predates the package
    raise SystemExit(
        f"the sara package (and its dependencies) aren't importable from this "
        f"python ({exc}).\nOne-time fix for an existing vault:\n"
        f"  <your-vault>/.venv/bin/pip install -e {SARA_DIR}\n"
        f"(scripts/doctor.sh checks this and prints the exact command)") from exc

if __name__ == "__main__":
    main()
