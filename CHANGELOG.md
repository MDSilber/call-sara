# Changelog

What shipped and why — the failure each change kills, or the move that
defines it. Plain markdown, newest first, written by hand. No version
numbers: the repo is the release, and `git pull` + `./install.sh` is
the upgrade.

## 2026-08-07

**Five tools on the phone (was fourteen).** The MCP connector's nine
no-input lookups collapsed into one `finance_detail(topic)`, and
`finance_freshness` retired because every answer already carries its
snapshot stamp. Same payloads, same resources — but a model picking
from five tools routes right on the first try, and "which tool was
that" stops being a thing anyone debugs.

**The app gets its own face.** The visual identity stops borrowing:
one ink family with real depth, warm neutral surfaces, a single
ledger-gold accent, and semantic color reserved for verdicts — when
something reads red, it means something. The hero greets like a
person, and the small hours belong to the night owls. Kills the
dashboard that looks like every other dashboard, and color that cries
wolf.

## 2026-08-06

**Sara App v2 — the read path grows up.** No GET parses the ledger
anymore: snapshot rooms serve from the write-side summary, and
everything exploratory queries the DuckDB shadow through parameterized
server-side SQL, hot-swapped on every regeneration. On top of it:
real search and keyset infinite scroll, bulk teach-a-rule, per-lot
investments with term badges, a statement-style register for any
account, a command palette, Plaid connection management, and a
drag-drop zone that takes any statement through identify → dry-run →
confirm → the gated writer. Kills the app that's fast only while your
ledger is small.

**The classifier goes local.** The model tier becomes a ladder of
interchangeable brains — the model already inside the Mac first, a
local Ollama daemon next, the API demoted to optional backstop — each
passing only its unsure residue down, every applied posting naming the
brain and confidence that judged it. Kills phoning home (and paying)
to categorize a grocery run: merchant strings never leave the machine
on the free path.

**Cross-source dedupe, proven on real books.** The writer learns that
entries from different sources never share ids: a units-exact matcher
for investment rows (same commodity, units quantized to the ledger's
precision, trade date within five days, cash within a cent) and a
cash-mirror facet for brokerage rows that shadow stories the ledger
already tells. The audit of our own history that followed surfaced
and removed 209 double-booked pairs. Kills the same trade counted
twice because two exports both saw it.

**The owner layer.** Accounts carry `owner:` metadata and the whole
system learns whose is whose — per-owner rollups behind their own
dual-computation gate, owner labels on the money map, a whose-is-whose
briefing for the phone, and clearing accounts learned they're transit,
never anyone's wealth. Kills answering "our net worth" to a question
about one person's money.

**The analytics shadow.** Every reports run also rebuilds a disposable
DuckDB — posting-grain schema, daily balance snapshots, Parquet twins —
behind three in-database cross-checks that refuse to emit on
disagreement, plus a starter notebook that asks the first three
questions. Kills exporting CSVs to answer a question the ledger
already knows.

**Onboarding grows the optional layers.** After the core founding,
six layers offered as arrow-key menus with minutes and tradeoffs on
every option — the app, the document inbox, the weekly letter, live
sync, daily automation, phone Sara. Every picked layer is a resumable
checklist whose last box is a proof, "later" is an answer that's
respected, and doctor's layers panel remembers how far you got. Kills
the upgrade that only exists if you read the docs.
