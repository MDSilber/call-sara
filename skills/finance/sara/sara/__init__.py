"""Call Sara's ingestion engine.

`sara.sources` maps every statement format (OFX, Chase CSV, Plaid) into one
set of canonical frozen models; `sara.ledger` is the single audited write
path into the Beancount vault. The CLI entry points under `sara.cli`,
`sara.ingest`, and `sara.link` are what `tools/run` and the launchd daemon
invoke.
"""

__all__ = ["__version__"]
__version__ = "1.0.0"
