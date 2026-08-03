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

## Structure (this order works; deviate deliberately)
- **Hero: the thesis in one sentence + the two or three numbers that
  define the situation** (e.g., liquid vs. concentrated stake, headline vs.
  after-tax). If there's a concentrated position, a single composition
  bar telling the whole story at a glance.
- **Where you stand, in plain English** — 3 short tiles: what's excellent,
  what's fine (usually spending — say so if true), the one thing that
  needs a plan.
- **The big event** (if any) — IPO, sale, inheritance — explained in
  plain terms with the after-tax reality and a stat sidebar.
- **Where the money sits** — proportional bars per account, invested vs.
  cash distinguished.
- **The money moves** — the centerpiece. Grouped (cut taxes / beat the
  estate tax / protect it / small free wins), each a card: bold action,
  one plain-English "why it works", the dollar value in the margin.
  Include a "things you'll be pitched — say no" box (the client's known
  bad-fit products).
- **The order to do it in** — a 4-stop timeline, urgency-labeled.
- **A few ideas that explain everything** — collapsible teaching cards,
  one concept per recommendation. The client learns the "why" and can
  re-derive the rules.
- **Footer** — a reassurance line + the summary numbers table.

## Writing rules
- Plain language, no jargon without a one-line gloss. If a section needs
  a table of contents, it's too long — cut it.
- Every recommendation names the account, the exact click, and the
  dollars. "Consider optimizing your asset location" is banned;
  "move the $37K bond fund into the 401(k), ~$700/yr" is the standard.
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
  and a stable emoji favicon.

## After publishing
Note the artifact URL in the vault (`facts/household/profile.md`)
so future sessions republish to the same link. Tell the user what
changed, not everything the report contains.
