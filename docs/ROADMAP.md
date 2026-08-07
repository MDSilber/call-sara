# Where this is headed

The honest map: **now** is being built, **next** is designed and queued,
**someday** is stuff we genuinely intend but won't pretend has a date.
It's a personal project — it grows when it grows. Real households drive
the design; if yours doesn't fit, open an issue and describe its shape.

## Now — in flight

- ~~**Automated ingestion**~~ — **shipped** as the `sara` package +
  `tools/run ingest.py`: hub-and-spoke sources over one gated writer,
  every format (OFX, CSV, Plaid) mapped into canonical typed records,
  dedupe + continuity gates, report-only until you've read one run.
  Still open here: the aggregator-agnostic config so SimpleFIN/SnapTrade
  slot in as mappers.
- ~~**Three-tier classification**~~ — **shipped** as `tools/run
  classify.py`: your payee rules always win → Plaid's category signal
  fills the gaps → a cheap batched model call judges the weak-signal
  residue. Everything provenance-tagged and re-doable; the review queue
  trends toward zero.
- ~~**The strict dialect**~~ — **shipped** as `skills/finance/sara/`:
  pyright strict, pydantic at every boundary, Decimal-only money paths
  with a lint that bans float, property tests on the invariants, dual
  computation gates that refuse to publish disagreeing numbers.

## Next — designed, queued

- **The notification layer:** the checks already compute the alerts — this
  delivers them: four tiers (interrupt · daily ledger email · the weekly
  letter · celebrations), with materiality floors, quiet hours, silence
  alarms (paycheck late, bill didn't post, sync gone stale), and every
  push carrying the rule and ledger lines that triggered it. No
  per-transaction spam, ever.
- **Safe-to-spend and the named crunch:** the hero learns to say "lowest
  point in the next 60 days: $412 on the 14th, the day rent hits — move
  $X or shift bill Y." Forecast-derived, goal-aware, provably computed.
- **Money watchdogs:** expected-state rules — the refund that didn't
  arrive, the paycheck that's late, plus same-day keep-or-kill triage on
  any first charge from a new recurring merchant (the only honest
  version of "trial catching").
- **Computed true expenses:** annual and semiannual lumps detected from
  the ledger, published as a monthly-equivalent reserve, excluded from
  pace baselines, with drift disclosure ("typical restaurant spend up
  18% over 6 months") — sinking funds without the homework.

- **K-1 / partnership / alternatives module:** distributions tracked
  continuously as cash; annual K-1s filed from the inbox into
  per-partnership basis facts; a reconciliation check that compares the
  tax story to the cash story and flags drift; and the per-deal
  scorecard — invested, distributions to date, yield, vs last year —
  generated instead of hand-curated. (Driven by a real 20-K-1 household
  that has maintained this exact spreadsheet by hand for years.)
- **The income lens:** for households whose goal is passive income per
  year rather than a pot — every asset scored by what it pays you,
  summed against your income goal, with allocation-over-time snapshots
  as a free byproduct. Same books, different physics.
- **The household lens:** whose money is whose, without splitting the
  books. Foundation shipped: `owner:` metadata on ledger accounts,
  per-owner rollups (`networth --by-owner`, the summary.json owners
  section, owner labels across the money map and balances views), all
  held to the headline by the cross-check gate. The app tried a global
  owner picker and simplified it away — the household view IS the top
  level; owner survives as quiet in-room filters (Spending, Activity,
  Investments) over the same tested `?owner=` read paths. Remaining:
  owner-routed notifications once the notification layer lands.
- **Multi-entity books:** an LLC/business ledger beside the household
  one, same skill and tooling driving both, work-vs-personal splits that
  survive an accountant's questions.
- **Tax command center:** safe-harbor and estimated-tax checking against
  your actual withholding, a year-end CPA handoff pack that assembles
  itself, and equity-event support (vest calendars, standing sell rules,
  per-lot term tracking — already half-built).
- **Portfolio truth:** time-weighted returns vs a benchmark via beangrow,
  allocation drift with dollars-to-move (built), and a rebalancing
  proposal you can hand to your own judgment.
- **Digest delivery + inbox watcher:** the weekly letter and the
  file-this-for-me folder exist; wiring them to each household's own
  delivery (mail, print, message) and watch daemons.
- **Webhook-fast syncing:** for households running the optional Worker,
  Plaid webhooks poke the home daemon — minutes-fresh books instead of
  daily.

## Someday — intended, undated

- **Project envelopes** — tagged spending vs per-project budgets — cut
  from v2 until a real household wants it (the `#tag` data and the
  `query.py project` drill stay).
- ~~**Interactive home v2**~~ — **shipped** as Sara App
  (`dashboard.sh --app`): the local web app over the same verified
  builders, with three whitelisted write actions (teach a rule, set a
  goal, dismiss a finding) through the same gated tools. The static
  pages stay for printing and mailing.
- **Renewal radar:** every insurance policy, subscription, and rate on a
  re-shop calendar with the negotiation script attached (the method
  exists in the playbook; automation pending).
- **The savings-hunt flywheel:** recurring sweeps for idle cash, fee
  creep, unclaimed property, and card-perk expiry, feeding the wins
  ledger automatically.
- **Linux support:** the macOS-only bits (brew, launchd templates) get
  cross-platform equivalents so a home server anywhere can run the
  daemon.
- **A source-author's guide:** writing a new institution mapper as a
  documented afternoon project — fixtures, golden tests, and the
  canonical-type contract as a stable public interface.
- **CI in the open:** the whole gauntlet (suite, strict types, float ban,
  cross-checks) running on every PR so contributors inherit the safety
  culture for free.

Things we've decided **against**, so you don't wait for them: hosting
anyone's data (fork it — you are your own tenant), a SaaS, a mobile app
(your phone talks to your books through MCP already), Monte Carlo
precision theater, envelope/zero-based budgeting (a documented churn
engine; pace-plus-watch-categories wins for non-budgeters), concierge
bill negotiation for a cut of "savings" (keep 100% — the scripts and the
browser-driving are yours), and streaks or daily-engagement pressure
(celebrate real milestones; never manufacture guilt). The full
decision records — reasoning and each one's escape hatch — live in
`.out-of-scope/`.
