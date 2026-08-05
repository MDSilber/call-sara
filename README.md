# Call Sara

I wanted a financial advisor that actually knows my numbers. Not a
chatbot with amnesia, not another SaaS holding my bank logins. So I
built one with Claude: a skill that keeps your household's entire
financial life in a plain-text vault on your own disk (every
transaction, the facts, an investment thesis you actually agreed to)
and then advises over it, session after session. Her name is Sara.
She opens with the number, notices the thing you didn't ask about,
and never scolds.

Decision support, not a licensed advisor. Details below.

## What you get

- **Advice grounded in a written thesis.** Big decisions get decided
  once, in writing. Sara answers from that, not from vibes.
- **Savings hunts.** She mines your real ledger for waste, bad rates,
  and unclaimed money. Findings come with dollars per year and the
  exact fix. (Setting this up on my own household surfaced a six-figure
  account nobody was tracking. True story.)
- **Browser statement fetching.** She drives your logged-in bank sites
  to pull statements. You type passwords, you click anything that
  moves money. Always.
- **A dashboard.** One command serves your whole financial picture as
  a local-only web page: net worth curve, drill-downs, query console.
  Add `--pretty` for the static beauty view — a single self-contained
  HTML file (net worth curve, spend, 60-day forecast, findings) that
  regenerates with the reports; fava stays the drill-down.
- **The boring essentials, done right.** Monthly reviews, statement
  imports with dedupe and reconciliation, instant queries, a 60-day
  cash-flow forecast, a shareable household assessment.

![The vault as a local web dashboard, synthetic demo household](docs/dashboard.png)

Here's a real session against the bundled demo vault (all numbers
synthetic):

```
> how are we doing

Liquid net worth $115,014 as of Aug 1 — July added $6,046 ($11,412
in, $5,366 out). Cash is fine: $49,621 in the Ally HYSA against
your ~$40K six-month floor. One thing while I was in there: you're
paying for Netflix, Hulu, AND Max — three overlapping streamers,
$50/mo. Keep one, drop two, that's ~$420/yr back. Want the cancel
pages?
```

## Get started (5 minutes to install, ~90 to onboard)

1. **Fork this repo** (so your tweaks have a home), then:
   ```bash
   git clone <your-fork> ~/code/call-sara
   cd ~/code/call-sara && ./install.sh
   ```
   (install.sh grabs its two small helpers, gitleaks and poppler, via
   brew if you don't have them.)
2. **Start a NEW Claude Code session** (skills register at startup).
3. Say **"set up my finances."** Sara interviews you, pulls your data,
   writes your thesis, and hands you a first assessment.

Want to kick the tires first? Seed a fake household and poke around:
```bash
skills/finance/scripts/init_vault.sh --demo /tmp/demo-vault
skills/finance/scripts/dashboard.sh --vault /tmp/demo-vault
```
Something misbehaving? `skills/finance/scripts/doctor.sh` checks the
whole install.

Requirements: macOS for now (assumes brew, screencapture, ~/Downloads),
Python 3.11+. Optional but great: the Claude in Chrome extension for
statement fetching. Heads up that driving a logged-in bank session may
conflict with your bank's terms of use and can trip bot detection.
Your call, your account.

## Where your data goes

Everything about you lives in your vault: plain text on your own disk
(default `~/Finance`, relocatable with `FINANCE_VAULT=/path` or
`init_vault.sh --remember <path>`) plus whatever PRIVATE git remote you
give it. This repo holds only the method and the code. Share the repo
freely, never share your vault.

Beyond your disk and your remote, exactly six things leave your machine:

- Vault contents Sara reads during a session enter Claude's model
  context, like anything you'd paste into a chat.
- Pushes go to your private remote, if you configure one.
- Assessments published as claude.ai artifacts are private-by-default
  pages under your account.
- With the Chrome extension, Claude reads the pages of financial sites
  you're logged into.
- Price refreshes send your ticker symbols (only commodities you tag,
  never balances or share counts) to public quote APIs.
- Savings-hunt research sends merchant and category terms (never names,
  accounts, or identities) to web search.

Sensitive documents never enter git, account numbers are last-4 only,
and a fail-closed gitleaks pre-commit scanner enforces it in both repos.
Sara never needs a password, SSN, or full account number typed into
chat. If any of this is out of bounds for you, don't use this.

## Who this is for

You're comfortable in a terminal, you want your financial life
local-first and inspectable, and you'd rather own a plain-text ledger
than trust a dashboard startup with your bank credentials. If that's
you, welcome!

## How it works

The vault is the source of truth and the model reads it fresh every
session. Facts load before opinions form. Arithmetic is code, judgment
is the agent, and the split is strict: Sara handles fuzzy input, the
tools own every number. (Independent validation of why: one HN author
measured the same 457-row calculation done by CLI in 8 seconds and
~200 tokens, versus raw LLM arithmetic at 3+ minutes and 67,709 tokens
that still needed weekly re-verification. "It was right the last three
times" is not a foundation for a settlement system.)

The money itself lives in a
[Beancount](https://beancount.github.io/docs/) ledger: plain-text,
double-entry, every transaction dated and categorized, the whole
history re-validated on every change. You never hand-edit it. The
importers write it (generic OFX/QFX, investment OFX, Chase CSV, with
dedupe and statement-balance reconciliation built in) and the agent
maintains it. Anything weirder, Sara reads the document and writes the
entries herself.

## What's where

- `skills/finance/SKILL.md` is the entry point. Read exactly what the
  model is told, it's all inspectable.
- `skills/finance/references/` holds the method: onboarding, the
  advisory playbook, current-year figures, querying, reports, browser
  fetching, per-institution site notes.
- `skills/finance/tools/` is the deterministic layer: importers,
  queries, checks, forecast, report generators.
- `skills/finance/vault-template/` is the vault skeleton (plus a
  synthetic demo variant).
- `skills/finance/scripts/` has init, doctor, price refresh, the
  dashboard, and statement filing.

## Make it yours

It's a fork-first project. The skill is plain markdown and small
Python, so change anything: give Sara a different personality (or a
different name!), add your bank's quirks to the institution notes, add
importers, retune the playbook to your country's rules. If you build
something generally useful, PRs are very welcome. The test suite
(`skills/finance/tools/importers/tests/run_tests.py`) keeps the money
math honest while you hack.

## Not a licensed advisor

This is educational decision support, not legal, tax, or investment
advice. No CPA, CFP, or fiduciary duty behind it. For tax filings,
estate documents, and large irreversible moves, see a licensed
professional. Sara will tell you when.

## License

MIT. See `LICENSE`.
