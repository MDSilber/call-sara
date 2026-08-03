# Onboarding — build a household vault from nothing

Run this when there's no vault, or the user says "set me up." The goal
of session one is NOT completeness — it's a working skeleton with real
data flowing in, so every later session compounds. Budget ~90 minutes,
and say so up front. Narrate progress; long silences read as stalls.

## 0. Set expectations (one paragraph, then start)
Tell the user, briefly: what a vault is (their private financial memory
that any session can read), what we'll do today (inventory → interview →
first data pull → first assessment), and the two hard boundaries:
**they type every password/MFA themselves, and Claude never clicks
anything that moves money or changes an account** — read/download only.
Downloads need their OK once per session.

## 1. Scaffold the vault (2 min)
Run `scripts/init_vault.sh [path]` (default `~/Finance`). It copies
`vault-template/` — `CLAUDE.md` (the data contract), an empty `THESIS.md`,
`rules.toml`, the ledger skeleton, the facts tree — then builds a Python
venv with beancount, `git init`s, arms the gitleaks pre-commit hook, and
sets a `.gitignore` that keeps `documents/` and `inbox/` (sensitive PDFs)
OUT of git forever. It preflight-checks git/python3/gitleaks and refuses to
half-scaffold. Confirm `bean-check` passes on the empty ledger.

Explain the hosting model once, then apply it:
- **git repo (private GitHub) = the brain** — ledger, facts, thesis,
  reports. Text only, safe, versioned, reachable from any device.
- **Cloud drive / local disk = the archive** — the raw PDFs and statements.
  Never in git. `documents/` is gitignored on purpose.
- The pre-commit scanner blocks any commit containing an SSN or a full
  account number. Store account numbers as **last-4 only** in facts.
Offer to create the private GitHub repo now (`gh repo create <name>
--private --source=. --push`) — the user runs it if the tool is denied.

## 2. Inventory institutions & services (10 min) — DO THIS BEFORE THE INTERVIEW
People forget accounts; a checklist beats memory. Use AskUserQuestion in
batches. Walk the categories and capture EVERY yes into
`facts/household/institutions.md` (the private map of who-logs-into-what;
account numbers as last-4 only):
- Banking: checking, savings, HYSA, credit cards (which cards, which spouse)
- Investing: brokerages, robo-advisors, crypto exchanges
- Retirement: current 401(k)/403(b), OLD employer plans, IRAs (trad/Roth/
  SEP), HSA — for BOTH spouses. Old plans are the most-forgotten money.
- Equity comp: RSUs / options / ESPP portals (Shareworks, Carta, E*Trade,
  Fidelity), for both spouses; any private-company holdings, angel deals.
- Debt: mortgage, HELOC, student loans, auto, personal.
- Insurance: term life, whole life, disability (group + individual), umbrella,
  home/renters, auto, employer group life.
- Kids: 529s, custodial (UTMA), trusts.
- Property: home(s), other real estate, rental.
- Payroll & benefits: Workday/Rippling/ADP; the benefits portal.
- Tax: who prepares it (CPA name), where returns live.
- Estate: will? trust? guardians named? attorney?
For each institution note: which spouse logs in, what it holds, roughly
how much (a guess is fine — real numbers come from the pull), and how
often it changes (fast: checking/cards → automate; slow: 401k → quarterly).

## 3. The founding interview (20 min)
Load `references/playbook.md` Part 5 (behavioral craft) first — it tells
you what the answers MEAN. Use AskUserQuestion, four at a time,
tap-not-type options with a free-text escape. Cover:
- **The basics they'll assume you know:** ages/birth years (both spouses,
  kids), city/state, jobs, income, marital + kid situation, health flags,
  intended working horizon. Ask these EARLY — advising without ages is
  malpractice, and clients notice the gap.
- **What the money is for**, ranked. Then "what does it change if the
  windfall arrives" — the behavioral tell.
- **Risk:** the drawdown question ("if this fell 30% and stayed there a
  year, walk me through your reaction"), separately from capacity.
- **Money psychology:** which script (avoidance / worship / status /
  vigilance)? Family money history — one open question here surfaces
  more than ten closed ones. Anxiety around money is diagnostic, and the
  answer flips the advisory task (a vigilant client's risk is
  under-living, not overspending).
- **Cadence & style:** how often to check in, how blunt to be, who
  decides (one spouse, both, delegate).
- **Standing rules they want:** cash cushion (offer to set it for them),
  concentration ceiling, what to never do (leverage, options plays,
  crypto beyond a token).
- **The known unknowns:** anything they've been avoiding (an old policy
  they don't understand, a login that's broken, cash paid off-books).
Write the answers as `THESIS.md` — the investment policy statement — in
their voice, then read it back and get a "yes, that's me." Values first,
then rules, then goal placeholders the advisor will fill with numbers.
Also write `facts/people/<name>/index.md` for each family member,
`facts/household/profile.md` for the durable facts, and their state +
filing status into the profile (the playbook's state-specific rules key
off it). As accounts get named, add a `[[accounts]]` routing entry per
account to `rules.toml` (last-4 → ledger account).

## 3b. Set up guided browser export (5 min) — teach it once
Most users have never had software drive their bank site, so explain the
mechanism the first time and demonstrate it live on the FIRST account:
- The **Claude in Chrome extension** lets this session read pages and click
  in their browser. They install/enable it; load the browser tools in one
  batch (`tabs_context_mcp`, `navigate`, `computer`, `read_page`,
  `get_page_text`, `find`, `browser_batch`).
- Restate the boundaries so they trust it: they log in themselves; the
  agent reads and downloads only, never touches a transactional control;
  passwords/MFA/SSNs are never typed by the agent.
- Then do the first export TOGETHER, out loud: "I'll open the export page,
  you'll see it, I'll set the date range and click Download, then I'll
  find the file and file it." Seeing one full loop end-to-end is what
  makes them comfortable handing over the next twenty.
- For any site the extension can't reach (some admin surfaces block it),
  say so and fall back to the user clicking with the agent reading over
  their shoulder via screenshots — don't burn time fighting it.

## 3c. Hunt down data they already have (10 min)
Before pulling anything live, sweep for data that already exists — it's
faster and often deeper (multi-year history):
- **Downloads folder** — old statements, tax returns, W-2s, 1099s, plan
  documents. Run `scripts/file_downloads.py inspect` over it to identify
  financial PDFs by content, then file them.
- **Email search** — have the user search their inbox for "statement",
  "1099", "W-2", "confirmation", "your tax return"; forward or download.
- **Tax software / CPA portal** — prior-year returns are the richest
  single document; get every year available.
- **Prior tools** — Mint/Monarch/Personal Capital/YNAB exports (CSV), any
  old spreadsheet they kept. Import as historical transactions.
- **Equity portals** — grant agreements and vesting schedules are usually
  downloadable PDFs; pull them all once.
For each thing found: file the source, extract facts, add transactions.
Only THEN move to live pulls for what's missing or recent.

## 4. First live data pull (30 min) — biggest and fastest first
Order by (value × ease). Typically: the equity portal (largest number,
usually a clean summary page), then the main brokerage, then payroll (one
paystub explains income, deductions, and every automatic split), then
the checking account. For each, follow `references/fetching.md` and
consult `references/institutions.md` for that site's quirks. Bank on-screen
numbers into facts IMMEDIATELY, before any download — the screen is a
source. Ask for the most recent **tax return** and **latest paystubs** to
be dropped in; a filed return is the single most information-dense
document a household has (income breakdown, marginal rates, carryovers,
every account that generated income, payment/penalty history).

Record what each site taught you in `references/institutions.md` (site
behavior only — no personal identifiers), so the next household's pull
is one-shot.

## 5. Close the loop — the first assessment
Run the checks and reports, then produce the assessment artifact per
`references/report.md`, even if numbers are partial — mark what's
inferred. End with: what's built, what's still missing (a short list),
and the ONE thing to do next session. Commit the vault; push. The system
now compounds: every future session starts by reading the thesis and the
facts instead of starting cold.

## What "done" means for onboarding
Vault scaffolded and private-repo hosted (or explicitly deferred);
institution map complete; THESIS.md written and confirmed by the user;
household profile + people files; at least the top 2–3 accounts pulled
with facts filed; the tax return ingested if available; a first
assessment published; everything committed. Later sessions handle the
long tail of accounts, automation, and refinement.
