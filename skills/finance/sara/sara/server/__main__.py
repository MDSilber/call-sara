"""Launch Sara App:  python -m sara.server [--port 8787] [--vault DIR]

Binds 127.0.0.1 only. FINANCE_VAULT (or --vault) picks the vault; the
frontend is served prebuilt from inside this package.
"""
import argparse
import os
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m sara.server",
                                 description=__doc__)
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--vault", type=Path, default=None,
                    help="vault directory (default: $FINANCE_VAULT or ~/Finance)")
    args = ap.parse_args()

    if args.vault is not None:
        os.environ["FINANCE_VAULT"] = str(args.vault.expanduser().resolve())

    # tools/vault.py resolves the vault when first imported — env is set now
    from sara.vault import LEDGER, VAULT
    if not LEDGER.exists():
        sys.exit(f"no ledger at {LEDGER} — set FINANCE_VAULT or pass --vault "
                 f"(scaffold one with scripts/init_vault.sh)")

    import uvicorn

    from .app import create_app
    app = create_app(port=args.port)
    print(f"✓ Sara App on http://127.0.0.1:{args.port}  "
          f"(vault: {VAULT}, local only — Ctrl-C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
