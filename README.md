# finance-system

A financial-advisor practice grounded in **your real ledger** — a Claude
Code skill that builds a private beancount vault of your household's money
(every transaction, the facts, an agreed investment thesis) and then
advises over it, session after session (decision support, not a licensed
advisor — details below). Not a chatbot with amnesia.

## Quickstart
```bash
git clone <this-repo> ~/code/finance-system
cd ~/code/finance-system && ./install.sh   # symlinks the skill into ~/.claude/skills/
```
Start a **new** Claude Code session (skills register at session start)
and say **"set up my finances."** Onboarding interviews you, pulls your
data, writes your thesis, and ends with a first assessment — ~90 minutes.

No real data yet? `skills/finance/scripts/init_vault.sh --demo <dir>`
seeds a synthetic household to poke at.
`skills/finance/scripts/doctor.sh` health-checks an install. **macOS only** for now (assumes `brew`, `screencapture`,
`~/Downloads`); requires Python **3.11+**, plus `brew install gitleaks
poppler` (secret scanner + `pdftotext`). Optional: `git-filter-repo` for
history surgery, and the **Claude in Chrome** extension for
browser-assisted fetching (you type passwords; it only reads and
downloads). Heads up: driving a logged-in bank session may conflict with
your institution's terms of use and can trip bot detection — your call,
your account.

## Where your data goes
Everything about *you* lives in your vault — plain text on your own disk
(default `~/Finance`; for a custom location, `init_vault.sh <path>` records it in `~/.finance-vault` so the tools find it; `FINANCE_VAULT=/path` overrides everything and can live in your shell profile to
relocate it) plus whatever **private** git remote you give it. This repo
holds only the method and the code; share it freely, never share your
vault. Beyond your disk and your remote, exactly four things leave your
machine:
- Vault contents the agent reads during a session enter Claude's model
  context, like anything you'd paste into a chat.
- Pushes go to your private remote, if you configure one.
- Assessments published as claude.ai artifacts are private-by-default
  pages under your account.
- With the Chrome extension, Claude reads the pages of financial sites
  you're logged into.

Sensitive documents never enter git, account numbers are last-4 only, and
a fail-closed `gitleaks` pre-commit scanner enforces it — in the vault
and in this repo. The skill never needs a password, SSN, or full account
number typed into chat; last-4 identifies everything. If any of the above
is out of bounds for you, don't use this.

## Who this is for
You're comfortable in a terminal, you want your financial life local-first
and inspectable, and you're not willing to upload your finances to a SaaS.
You'd rather own a plain-text ledger than trust a dashboard.

## What it does
The parts you won't find elsewhere:
- **Thesis-grounded advice** — decisions are answered from a written
  investment thesis you agreed to, not improvised per session.
- **Savings hunts** — mines the ledger for waste, bad rates, unclaimed
  money; findings come with $/yr and the exact fix.
- **Browser statement fetching** — drives your logged-in bank/broker
  sites to pull statements; you handle passwords and anything that moves
  money.
- **A visual dashboard** — `skills/finance/scripts/dashboard.sh` serves
  your whole financial picture as a local-only web page (fava): net-worth
  curve, spending drill-downs, a query console. Nothing leaves your machine.
- **Sara** — an advisor voice that opens with the number, notices what
  you didn't ask about, and never scolds.

And the expected parts: monthly reviews, statement filing and
categorization, instant ledger queries with as-of dates, a regenerable
published assessment, life-event interviews.

## Demo
A session against the bundled demo vault (`init_vault.sh --demo <dir>` —
all numbers synthetic):

```
> how are we doing

Liquid net worth $115,014 as of Aug 1 — July added $6,046 ($11,412
in, $5,366 out). Cash is fine: $49,621 in the Ally HYSA against
your ~$40K six-month floor. One thing while I was in there: you're
paying for Netflix, Hulu, AND Max — three overlapping streamers,
$50/mo. Keep one, drop two, that's ~$420/yr back. Want the cancel
pages?
```

## Philosophy
The vault is the source of truth; the model reads it fresh every session.
Facts first, opinions second — the complete picture loads before any
recommendation. Advice is grounded in a thesis you've agreed to in
writing, so decisions get made once. Arithmetic is code; judgment is the
agent. Do things (drive the browser), don't just describe them.

## The ledger (beancount)
The money itself lives in a [Beancount](https://beancount.github.io/docs/)
ledger — plain-text, double-entry: every transaction dated, categorized,
balancing to zero, with `bean-check` validating the whole history on
every change. You never hand-edit it; the importers write it and the
agent maintains it. Importers ship for generic **OFX/QFX** and **Chase
CSV**; anything else, the agent reads the document and writes the entries.

## What's where
- `skills/finance/SKILL.md` — the entry point: read exactly what the
  model is told.
- `skills/finance/references/` — onboarding, the advisory playbook,
  current-year figures, querying, report generation, browser fetching,
  institution site behavior.
- `skills/finance/tools/` — deterministic importers, queries, checks, and
  report generators, parameterized entirely by the vault.
- `skills/finance/vault-template/` — the canonical vault skeleton.
- `skills/finance/scripts/` — `init_vault.sh` (scaffold, `--demo` for a
  synthetic household), `doctor.sh` (install health check),
  `file_downloads.py` (identify and file downloaded statements).

## Not a licensed advisor
This is educational decision support, not legal, tax, or investment
advice — no CPA, CFP, or fiduciary duty behind it. For tax filings,
estate documents, and large irreversible moves, see a licensed
professional; the skill itself will tell you when.

## License
MIT — see `LICENSE`.
