# Report — generating the household assessment artifact

The assessment is a published HTML artifact: the household's whole
financial life on one page, in plain language, with every "move" carrying
its dollar figure. Regenerate on demand ("update the report"); republish
to the SAME URL by writing to the same file path.

## Before writing a single number
1. Refresh from data: `tools/run reports.py` and `tools/run run_checks.py`
   so `reports/*.md` are current; use `references/querying.md` for anything deeper.
2. **Verify every dollar figure you intend to print** against actual
   holdings or a live account page. A "move" whose value hasn't been
   priced against this household's real positions does not go in. If a
   figure is an estimate, label it. This rule exists because unverified
   report numbers get quoted back to you as commitments.
3. Read THESIS.md — the report's tone and its "moves" must serve the
   client described there (a vigilant client gets reassurance-first framing
   and permission-to-spend; a spender gets guardrails).
4. Recompute headline totals from components — never trust a prior
   report's running total. Diff the hero numbers against `reports/*.md`
   and resolve every mismatch; averaging is not resolving.

## Structure (this order works; deviate deliberately)
- **Hero: the thesis in one sentence + the two or three numbers that
  define the situation** (e.g., liquid vs. concentrated stake, headline vs.
  after-tax). If there's a concentrated position, a single composition
  bar telling the whole story at a glance.
- **Where you stand, in plain English** — 3 short tiles: what's excellent,
  what's fine (usually spending — say so if true), the one thing that
  needs a plan.
- **Financial health score** — five dimensions, each 0–100, each a fixed
  weighted roll-up so two sessions score alike. *Cash flow*: savings rate
  40 · expense-to-income 30 · income stability 15 · buffer vs the
  liquidity model 15 (savings-rate anchors: <5% critical, 5–15 thin,
  15–20 good, 20+ excellent, 50+ exceptional). *Debt*: DTI 35 · weighted
  average rate 25 · debt-to-asset 20 · any >8% APR outstanding 20.
  *Investments*: diversification 30 · thesis-appropriate allocation 25 ·
  fee drag 20 (flag any ER >0.20%) · asset location 15 · concentration 10
  — employer stock scores by the playbook's concentration rules, not a
  generic threshold. *Retirement trajectory*: nest egg vs age multiple 40
  (1x income at 30, 3x at 40, 6x at 50, 10x at 67) · contribution rate 25
  · horizon 15 · withdrawal sustainability 20. *Protection*: cushion vs
  the sizing matrix 30 · life adequacy 25 · own-occupation disability 20
  · health/HSA 15 · estate currency 10. Every sub-score cites the
  ledger/facts number it derives from, with the as-of date — scores come
  from data, never vibes. Anchors are calibration, not law: when the
  thesis overrides one, score to the thesis and say so. Roll up to an
  overall letter grade, then a prioritized **90-day action plan**: 3–5
  items, each with its $ impact and the surface (which login or site) it
  happens on.
- **The big event** (if any) — IPO, sale, inheritance — explained in
  plain terms with the after-tax reality and a stat sidebar.
- **Where the money sits** — proportional bars per account, invested vs.
  cash distinguished.
- **The next 60 days** (only when `tools/run forecast.py` shows a projected
  minimum near $0 or a `[fixed_balances]` floor) — the crunch date, its
  driving flows, and the fix, every figure explicitly labeled a projection;
  a clean runway is one reassurance line in the footer, not a section.
- **The money moves** — the centerpiece. Grouped (cut taxes / beat the
  estate tax / protect it / small free wins); within each group, order
  moves by (annual $ ÷ owner-hours) and show both numbers. Each is a
  card: bold action, one plain-English "why it works", the dollar value
  in the margin. Include a "things you'll be pitched — say no" box (the
  client's known bad-fit products).
- **The order to do it in** — a 4-stop timeline, urgency-labeled.
- **A few ideas that explain everything** — collapsible teaching cards,
  one concept per recommendation. The client learns the "why" and can
  re-derive the rules.
- **Footer** — a reassurance line + the summary numbers table + the
  mandatory one-liner (on EVERY published assessment): decision support,
  not tax/investment/legal advice; figures as of <date>.

## Writing rules
- Plain language, no jargon without a one-line gloss. If a section needs
  a table of contents, it's too long — cut it.
- Every recommendation names the account, the exact click, and the
  dollars. "Consider optimizing your asset location" is banned;
  "move the $37K bond fund into the 401(k), ~$700/yr" is the standard.
- Action lists are grouped by WHERE the owner does them — "In <brokerage>
  (5 clicks): …", "In <bank>: …" — ordered fastest-first, with separate
  buckets for "needs a login we haven't cracked" and send-a-message
  items, and headed by "verified against live data <date>." The owner
  should be able to knock out a whole surface in one sitting.
- Fresh-eyes check, after drafting and before publishing: re-derive the
  three load-bearing numbers (headline net worth, the biggest move's $,
  the most-changed score) from `tools/run query.py` and the facts NOW,
  not from session memory — memory drifts, and the artifact outlives the
  session. A mismatch means fix the data or the draft before shipping.
- Corrections go IN the report when a prior version was wrong — the
  client trusts a document that admits and fixes its numbers.

## Design (load the artifact-design skill; these are the non-negotiables)
- One committed visual identity per household; keep it stable across
  regenerations (same favicon, same palette) so it feels like *their*
  document evolving, not a new report each time.
- Both light and dark themes via `:root` tokens with `data-theme`
  overrides winning over `prefers-color-scheme`. `tabular-nums` on all
  figures. Self-contained: inline all CSS/SVG, no external hosts (the
  artifact CSP blocks them).
- Print stylesheet, since clients keep and print these.
- Publish via the Artifact tool with a stable file path (keeps the URL)
  and a stable emoji favicon. Remind the user, each publish: this page
  is their complete financial picture — share deliberately, if at all.
- No Artifact tool? Write the same HTML into the vault's `reports/` and
  open it locally — same file, same regeneration rules.

## After publishing
Note the artifact URL in the vault (`facts/household/profile.md`)
so future sessions republish to the same link. Tell the user what
changed, not everything the report contains.
