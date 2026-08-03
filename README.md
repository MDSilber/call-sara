# finance-system — a personal CFO in a git repo

The **method** for running a household's finances with Claude: a skill that
(a) builds a private financial **vault** from scratch — ledger, facts, an
investment thesis, categorization rules — by interviewing you and pulling
data from every bank / broker / payroll site you use, and (b) acts as your
ongoing financial advisor over that vault: monthly reviews, decision advice,
instant answers, and a regenerable household assessment.

Everything about *you* lives in your vault (a separate **private** repo,
default `~/Finance`). This repo holds only the method and the code —
share it freely; **never share your vault.**

## Install
```bash
git clone <this-repo> ~/code/finance-system
cd ~/code/finance-system && ./install.sh   # symlinks the skill into ~/.claude/skills/
brew install gitleaks                          # secret scanner — the vault refuses to commit without it
brew install poppler                           # pdftotext, used to identify downloaded statements
```
Optional: `brew install git-filter-repo` (history surgery if a secret ever
slips in), and the **Claude in Chrome** extension so the skill can drive
your logged-in financial sites (you type passwords; it only reads and
downloads).

## First run
Open Claude Code anywhere and say **"set up my finances."** With no vault
present the skill runs onboarding: scaffolds `~/Finance` (or
`$FINANCE_VAULT`) from `skills/finance/vault-template/`, inventories your
institutions, interviews you to write your investment thesis, walks you
through exporting data from each site, and ends with a first published
assessment. ~90 minutes.

## Daily use
- "let's do the monthly" — 15-minute review, one open item.
- "here's my new statement" / drop a PDF — filed, categorized, reconciled.
- "what did we spend on X?" — answered from the ledger, with the as-of date.
- "should I …" — advice grounded in your written thesis.
- "update the report" — regenerate the assessment artifact.

## What's where
- `skills/finance/SKILL.md` — the entry point and modes.
- `skills/finance/references/` — onboarding, the advisory playbook, querying,
  report generation, the browser-fetch workflow, institution site behavior.
- `skills/finance/tools/` — deterministic importers, queries, checks, and
  report generators, parameterized entirely by the vault (`FINANCE_VAULT`,
  `rules.toml`, `facts/goals`). Arithmetic is code; judgment is the agent.
- `skills/finance/vault-template/` — the canonical vault skeleton.
- `skills/finance/scripts/init_vault.sh` — scaffold a fresh vault from it.

## Design principles
Facts first, opinions second. Verify every dollar figure against real
holdings before stating it. Do things (drive the browser), don't just
describe. Optimize the human's utility, not the spreadsheet. Sensitive
documents never enter git, account numbers are last-4 only, and a fail-closed
pre-commit scanner enforces it — in the vault and in this repo.
