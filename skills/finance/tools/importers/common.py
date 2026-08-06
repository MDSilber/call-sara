"""DEPRECATED SHIM — the shared importer machinery now lives in the `sara`
package: canonical models + sanitizers in `sara.sources.model`, OFX parsing
in `sara.sources.ofx`, and the dedupe / continuity / atomic-write path in
`sara.ledger.writer`. This module re-exports the old names so existing
imports (inbox.py, recategorize.py, tests, user scripts) keep working.

One deliberate change rides along: money values are `decimal.Decimal` now,
never float — `parse_ofx_amount` returns Decimal, and the emit/continuity
helpers expect Decimal. Callers that only pass strings/paths (everything in
this repo) are unaffected.
"""
import sys
from pathlib import Path

SARA_DIR = Path(__file__).resolve().parent.parent.parent / "sara"
sys.path.insert(0, str(SARA_DIR))

try:
    from sara.ledger.writer import (  # noqa: F401
        DISCREPANCY,
        UNVERIFIABLE,
        VERIFIED,
        append_to_ledger,
        assertion_date,
        atomic_write,
        check_continuity_ledger,
        check_continuity_rows,
        emit,
        existing_assertion_dates,
        existing_ids,
        existing_index,
        hash_is_duplicate,
        import_hash,
        is_duplicate,
        payee_key,
        render_assertion,
    )
    from sara.sources.model import escape, parse_ofx_amount  # noqa: F401
    from sara.sources.ofx import acctid, read_ofx  # noqa: F401
    from sara.vault import routing_help  # noqa: F401
except ImportError as exc:  # an existing vault venv that predates the package
    raise SystemExit(
        f"the sara package (and its dependencies) aren't importable from this "
        f"python ({exc}).\nOne-time fix for an existing vault:\n"
        f"  <your-vault>/.venv/bin/pip install -e {SARA_DIR}\n"
        f"(scripts/doctor.sh checks this and prints the exact command)") from exc
