# sara — the ingestion engine

Every dollar that enters the vault passes through this package. It exists to
make one promise easy to trust: **what's in the ledger is exactly what the
institutions said, once, with proof.**

## The shape: hub and spoke

```
   OFX/QFX files     Chase CSVs      Plaid API        (future: SimpleFIN, SnapTrade)
        │                │               │
   sources/ofx.py  sources/chase_csv.py  sources/plaid_src.py     ← mappers: parse, sanitize,
        └────────────────┼───────────────┘                          account for every row
                         ▼
              CANONICAL FROZEN MODELS                              ← sources/model.py: the contract
        CanonTxn · CanonInvestTxn · CanonPosition · CanonBalance     (Decimal money, real dates,
                         │                                           escaped text, source_id identity)
                         ▼
                  ledger/writer.py                                 ← ONE write path: dedupe tiers,
        dedupe (source_id → hash → fuzzy) · continuity gates         continuity, dry-run default,
        dry-run default · atomic append · bean-check rollback        atomic + rollback
                         │
                         ▼
                ledger/*.beancount                                 ← the vault (plain text, yours)
```

Spokes only ever widen on the left. A new institution or aggregator is a new
mapper into the same canonical types; the writer, the gates, and the ledger
never learn its name.

## The rules the code lives by

- **Decimal only.** Money is `decimal.Decimal` from parse to print. The name
  `float` may not appear anywhere in `sara/sources` or `sara/ledger` —
  `tests/test_float_ban.py` walks the AST and fails the build if it does.
  Plaid's JSON floats cross once, `float → str → Decimal`, exact for 2dp money.
- **Parse, don't validate.** Untrusted bytes (statements, CSVs, API JSON,
  rules.toml) become typed values at the boundary or become a *reported* miss.
  Nothing downstream re-checks shapes; nothing malformed travels.
- **One write path.** Every entry, from every source, lands through
  `ledger/writer.py`: source-id-first dedupe, balance-continuity gates,
  dry-run by default, atomic tmp+fsync+rename, and an in-process `bean-check`
  that rolls every touched file back on failure.
- **Account for every row.** A fetched row is written, deduped (with its
  tier named), excluded-as-pending, unrouted, or unmapped — each one listed.
  The ingest verification report reconciles the counts exactly and refuses
  to write when they don't.
- **The engine is swappable.** Only `sara/ledger` knows the ledger is
  Beancount (its files, `bean-query`, `bean-check`). Swap the engine and the
  mappers, models, and daemon never notice.
- **Identity survives edits.** `source_id` (bank FITID / Plaid transaction id)
  is the primary dedupe key and is persisted as metadata on every entry; the
  content hash is the fallback, the ±5-day fuzzy match exists only for
  entries that predate both. Re-importing anything is always free.

## The pieces

| Module | Why it exists |
|---|---|
| `sara/sources/model.py` | The canonical contract + boundary sanitizers |
| `sara/sources/{ofx,chase_csv,invest_ofx}.py` | File-format mappers (pure; notes, never prints) |
| `sara/sources/plaid_src.py` | Plaid JSON mappers (pure; fixture-testable, no network) |
| `sara/ledger/writer.py` | The single gated write path |
| `sara/ledger/queries.py` | bean-query reads, Decimal out |
| `sara/ledger/invest.py` | Lot rendering + the positions reconcile |
| `sara/cli/*.py` | The four importer CLIs (`tools/run importers/...` compatibility) |
| `sara/ingest.py` | The Plaid daemon: report-only sync, `--write` applies |
| `sara/link.py` | One-command local Plaid Link (repair mode, 10-slot guardrails) |
| `sara/plaid_api.py` | The one thin door to plaid-python (lazy, dict-out) |
| `sara/vault.py`, `sara/rules.py` | Vault location/config; the rules.toml categorizer |
| `sara/typed.py` | Typed views over untrusted JSON/TOML |

## Working on it

```bash
pip install -e '.[dev]'            # or: uv pip install -e '.[dev]'
pytest                             # unit + property + golden end-to-end
FINANCE_TEST_VENV=~/Finance/.venv pytest   # + bean-check/bean-query paths
pyright                            # strict, zero errors
ruff check sara tests
```

The legacy golden suite (`../tools/importers/tests/run_tests.py`) drives the
same code through the `tools/` shims and must also stay green — it is the
behavioral contract the rewrite was built against.
