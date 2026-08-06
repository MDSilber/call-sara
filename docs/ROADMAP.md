# Where this is headed

Stuff we're actively building or have designed and queued. No promises, no
dates — this is a personal project that grows when it grows.

- **Automated ingestion (in flight):** hub-and-spoke sources over one gated
  writer; Plaid first (bring your own free keys), aggregator-agnostic config.
- **Three-tier classification:** your payee rules always win → Plaid's
  category signal fills gaps → a cheap batched model call handles the
  weak-signal residue, with everything re-doable and provenance-tagged.
- **K-1 / partnership module:** cash distributions tracked continuously via
  ingestion; annual K-1s filed from the inbox into per-partnership basis
  facts; a reconciliation check that compares the tax story to the cash
  story per investment and flags drift.
- **Multi-entity books:** a business ledger beside the household one
  (consulting LLCs, side businesses), same skill and tooling driving both.
- **Weekly digest delivery + document inbox watcher:** built, wiring into
  households' own delivery choices.

Have a shape of household we don't handle? Open an issue — real cases like
these drive the design.
