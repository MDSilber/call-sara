# Call Sara ☎️

Sara is my accountant. Well, a fictional version of her that lives in
Claude Code, keeps my family's entire financial life in a plain-text
vault on my disk, and roasts my streaming subscriptions. I built her
because I wanted an advisor who actually knows my numbers, and honestly
she's become the best money thing I have. Now she can be yours too!

This is her, mid-checkup (demo data, but this is really what it's like):

```
> how are we doing

Liquid net worth $115,014 as of Aug 1 — July added $6,046 ($11,412
in, $5,366 out). Cash is fine: $49,621 in the Ally HYSA against
your ~$40K six-month floor. One thing while I was in there: you're
paying for Netflix, Hulu, AND Max — three overlapping streamers,
$50/mo. Keep one, drop two, that's ~$420/yr back. Want the cancel
pages?
```

She does the whole job. Onboards you with an interview, pulls
statements from your logged-in bank sites (you type passwords, you
click anything that moves money, always), keeps a real double-entry
ledger reconciled to the penny, runs monthly reviews (we call it the
cheshbon), forecasts your next 60 days, hunts for wasted money like
it's a sport, and writes it all down so next session she still knows
you. Setting her up on my own house surfaced a six-figure account
nobody was tracking. Honestly a bit embarrassing. I'm still not over it.

![the dashboard](docs/dashboard.png)

## Try her

1. Fork this repo, then:
   ```bash
   git clone <your-fork> ~/code/call-sara
   cd ~/code/call-sara && ./install.sh
   ```
   install.sh does everything: links the skill, arms the secret
   scanner, checks every dependency.
2. Open a NEW Claude Code session. Skills load at startup.
3. Say **"set up my finances."** That's it! She takes it from there
   (~90 min, she's thorough).

That sentence is the whole interface, by the way. She builds the vault
first, then offers everything else herself, arrow-key menus all the way
down, and "later" is an answer she actually respects:

- **Day one** — the vault: she interviews you, files your first
  statements, writes down who you are, hands you a real assessment.
- **Level two** — the books sync themselves: your own free Plaid keys,
  a verification report you'll actually read, then a daily pull while
  you sleep. Leftover uncategorized stuff has an autopilot too — your
  rules, then Plaid's guess, then a cheap haiku call, dry-run first.
- **Level three** — Sara everywhere: the little local app, her weekly
  letter, her voice on your phone with your real numbers.

You never set any of it up from docs. She walks you there, and
`doctor.sh` always knows how far you got.

Not ready to hand over real data? Same, at first:
```bash
cd ~/code/call-sara
skills/finance/scripts/init_vault.sh --demo /tmp/demo-vault
skills/finance/scripts/dashboard.sh --vault /tmp/demo-vault   # opens your browser, random local port
skills/finance/scripts/dashboard.sh --vault /tmp/demo-vault --home   # Sara Home — the morning page (spend pace, needs-you, goals)
skills/finance/scripts/dashboard.sh --vault /tmp/demo-vault --app    # Sara App — the interactive one (see below)
```
My favorite thing lately is Sara App (`--app` above). It's a little
local web app that serves the whole picture live: spending pace, a
transaction feed, the money map, investments, goals, autopilot. Click
an uncategorized transaction and teach her the rule right there, and
she recategorizes your history on the spot (same gated tools she uses
herself, bean-check and all). It runs on 127.0.0.1 only, the page ships
prebuilt so there's no node anything to install, and writes need a
per-launch token the page carries, so random browser tabs can't poke
it. The static pages still exist because you can't email a server to
your spouse.

Two-of-you bonus: tag accounts with `owner:` metadata (you, them,
joint) and the whole system learns whose is whose — `query.py networth
--by-owner` splits the pot, the money map labels the boxes, and Sara
starts addressing whoever's money it actually is.

The fava view ships with chart dashboards (net worth, allocation donuts,
income vs expenses) plus fava-investor's tax-loss-harvest and allocation
pages — see the Dashboards and Investor tabs in the sidebar (the
Dashboards tab needs `--writable`; the script prints a hint).
If anything's weird, `skills/finance/scripts/doctor.sh --vault /tmp/demo-vault`
tells you what. (Brand-new Mac? Set `git config --global user.email you@wherever.com`
first, the vault is a git repo and will ask.)

macOS + Python 3.11+ (install.sh handles python). The Claude in Chrome
extension makes statement-pulling magical but is optional. Fair warning:
driving a logged-in bank session may not be kosher with your bank's
terms of use. Your call, your account.

## Already running her? (updating an older clone)

`git pull`, `./install.sh` again (it only fills gaps), then
`scripts/doctor.sh` and do the one thing it says — the importers grew
into a real package (`sara`), and doctor prints the exact pip line
that puts it in your vault's venv. That's the whole migration. Your
ledger doesn't change, and every import command you already know
prints the same entries it always did.

Want the new app? First `dashboard.sh --app` installs its own server,
then one `tools/run reports.py` fills in the app's read model and the
SQL shadow (`reports/analytics.duckdb`) — the page itself tells you if
it's waiting on that. Everything else stays opt-in and silent until
you ask: `owner:` tags for the household lens, your own Plaid keys,
the classifier's model tier. If anything looks off, doctor.sh again —
it always knows how far you got.

## Where your data actually goes

Your disk and your private git remote. That's the design. The eight
honest exceptions, in full:

1. Whatever Sara reads in a session enters Claude's model context, same
   as pasting it into a chat.
2. Pushes go to your private remote, if you set one up.
3. Published assessments are private-by-default claude.ai pages.
4. With the Chrome extension, Claude reads bank pages you're logged into.
5. Price refreshes send ticker symbols (never balances) to quote APIs.
6. Savings research sends merchant terms (never identities) to web search.
7. If you set up the optional Plaid feeds, your transactions flow from
   your bank through Plaid's API (under your own keys) to your disk.
8. If you give Sara an Anthropic API key for the categorizer's model
   tier, uncategorized payees/amounts (never balances, never account
   numbers) go to the Claude API under your key. No key, no calls.

Account numbers are last-4 only. Statements never enter git. A
fail-closed gitleaks scanner blocks any commit that breaks the rules.
Sara never needs a password, SSN, or full account number typed into
chat, ever. If any of the eight is a dealbreaker for you, don't use this!

## Sara in your pocket (optional)

The vault already writes `reports/summary.json` — a machine-readable
twin of the reports, same verified math. `integrations/cloudflare-mcp/`
is a tiny read-only MCP server for Cloudflare Workers that serves it
straight from your private vault repo, so Claude on your phone can
answer "how are we doing?" with your real numbers while your laptop
sleeps. Opt-in, bearer-gated, one read-only GitHub token, no second
copy of your data anywhere. Full walkthrough in that directory's README.

## The feeds (optional)

New: Sara can pull the everyday accounts herself. You mint your own free
Plaid keys (took me ten minutes, the walkthrough with the exact wording is
in `references/fetching.md`), link an institution with one command, and
`tools/run ingest.py` syncs it into the ledger. It refuses to write until
every count reconciles, it prints a verification report that compares your
bank's reported balance against the ledger to the cent, and re-running it
is always free because dedupe is exact. First run is report-only, you read
it, then you flip `--write`. Your keys, your machine, no middleman, and the
10 lifetime Plaid links are guarded so you never burn one by accident
(broken connections repair for free). Statements you still drag in by hand
work exactly like before, same ledger, same dedupe.

For the spreadsheet-brained: every reports run also drops
`reports/analytics.duckdb` — your whole ledger as a real SQL database
(parquet twins included, cross-checked to the cent before it's allowed to
exist) — and `docs/notebooks/first-questions.ipynb` asks it the first
three questions.

## How it's built

One rule: **Sara handles the fuzzy stuff, code owns every number.**
LLMs are amazing at reading a blurry receipt and terrible at summing
400 rows, so all arithmetic lives in deterministic tools over a
[Beancount](https://beancount.github.io/docs/) ledger that nobody
hand-edits and a test suite keeps honest. Everything she's told is
inspectable, starting at `skills/finance/SKILL.md`:

| Path | What's in it |
|---|---|
| `skills/finance/SKILL.md` | The entry point, literally what the model reads |
| `skills/finance/references/` | The method: onboarding, playbook, figures, site notes |
| `skills/finance/tools/` | Importers, queries, checks, forecast. The math |
| `skills/finance/vault-template/` | Vault skeleton (demo variant next door in `vault-template-demo/`) |
| `skills/finance/scripts/` | init, doctor, prices, dashboard, filing |

## Make her yours

It's all markdown and small Python. Rename her! Change her whole
personality! Teach her your bank's weird download page! Retune the
playbook for your country! If you build something everyone could use,
PR it, I'd love that.

## The fine print

Sara is decision support, not a licensed anything. No CPA, CFP, or
fiduciary duty here. For filings, estate docs, and big irreversible
moves, see a real professional. She'll tell you when, she's good like
that.

MIT. Go nuts.
