# Onboarding — build a household vault from nothing

Run this when there's no vault, or the user says "set me up." The goal
of session one is NOT completeness — it's a working skeleton with real
data flowing in, so every later session compounds. Budget ~90 minutes
of working time for the core — usually 2–3 sittings — and say so up
front; the optional layers at the end each carry their own small
estimate. Narrate progress; long silences read as stalls.

**The whole flow runs on the arrow-key picker.** Every decision point —
location, hosting, institutions, interview answers, what to pull first,
what to defer — goes through AskUserQuestion: short labels, the tradeoff
in each option's description, up to four related questions per batch,
a recommended option marked. Typing is reserved for genuinely open
answers (names, dollar amounts, stories). Onboarding should feel like a
great installer, not a form: tap-tap-tap, with Sara reacting between
batches.

## First act: write the progress file
Before anything else, write `ONBOARDING.md` at the vault root (create
the directory if the scaffold hasn't run yet) and keep it current:

```markdown
# Onboarding — live progress
Legend: [ ] todo · [→] in progress · [x] done · [~] deferred

- [ ] 0  expectations set (both browser boundaries stated)
- [ ] 1  vault scaffolded · bean-check passes · hosting decided
- [ ] 2  institution inventory → facts
- [ ] 3  founding interview → THESIS confirmed ("yes, that's me")
- [ ] 3b first browser export done together
- [ ] 3c existing-data sweep (Downloads / email / tax returns /
      prior tools / equity portals)
- [ ] 4  live pulls — pull queue below
- [ ] 5  first assessment published · committed · next step named
- [ ] 6  optional layers offered — picked blocks appended below

## Pull queue (ordered by value × ease)
| Institution | What it holds | Status | Notes |
|---|---|---|---|

## Deferred / blocked
```

Check items off AS THEY COMPLETE, not in a batch at the end — a dead
session costs nothing if this file is current, and the whole 90 minutes
if it isn't. If the file already exists, onboarding is mid-flight:
resume at the first unchecked item — optional-layer blocks included; a
half-built layer resumes exactly like a core phase. On completion (no
`[ ]` left anywhere, `[~]` counts as settled), move it to
`notes/YYYY-MM-DD.onboarding-log.md` — the file's absence is what says
onboarding is done. Tell the user up front: "usually 2–3 sittings; stop
anytime, we resume exactly where we left off."

## 0. Set expectations (one paragraph, then start)
Tell the user, briefly: what a vault is (their private financial memory
that any session can read), what we'll do today (inventory → interview →
first data pull → first assessment), and the two hard boundaries:
**they type every password/MFA themselves, and Claude never clicks
anything that moves money or changes an account** — read/download only.
Downloads need their OK once per session.

## 1. Scaffold the vault (2 min)
**Decide where it lives FIRST — as a picker, not a paragraph.** Use
AskUserQuestion (arrow-key TUI) with two questions, so the choice feels
like an installer, not homework:
- *"Where should the vault folder live?"* — `~/Finance (Recommended)`
  (default; every tool finds it with zero config) · `Somewhere else`
  (any path; init records it in `~/.finance-vault` so tools still find
  it) · `A cloud-synced folder` (works, but warn: file-sync services
  fight git — fine for solo use, prefer a git remote for real backup).
- *"Where should it be backed up?"* — `Private GitHub repo (Recommended)`
  (versioned, reachable anywhere; created in one command below) ·
  `Another private git host` (GitLab/Codeberg/self-hosted — same
  commands, their URL) · `Local only for now` (fine to start; set a
  deferred item to revisit — one disk is one bad day from zero).
Put the tradeoff in each option's description, keep labels short, and
respect the pick without relitigating. Google Drive/Dropbox/iCloud is a
*location* answer (synced folder), not a *backup* answer — git history
inside a sync folder can corrupt under concurrent sync; say so plainly
if they pick it, then proceed.

Then run `scripts/init_vault.sh [path]` (default `~/Finance`). It copies
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
Pushing to a remote this session didn't create? Verify it's private
first: `gh repo view --json visibility`.

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

**Reconcile as you go.** Every number gets a silent plausibility pass
before it lands in a file; from the third question-batch on, cross-check
against earlier answers — the story must add up. Anchors: take-home is
typically 60–80% of gross (equity comp and mega-backdoor deferrals skew
it — ask, don't assume); card APRs 19–29%, mortgages 3–8%, car loans
0–12%, and a number outside its band gets one casual "want to
double-check that?"; stated spending must fit income − taxes − savings,
and a gap gets resolved NOW, warmly, never accusatorially ("expenses
come to ~$10.3K but take-home is $9.5K — dipping into savings, or did we
miss an income source?"). Numbers the user isn't sure of land in facts
with `verified:` unset and an "(estimate)" marker, so the first
assessment labels them.

**Save after every batch.** Write THESIS.md, the profile, and the facts
files incrementally as answers land — never one big write at the end
(same logic as the progress file: a dead session should cost nothing).

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
- **Prior tools** — Mint (defunct 2024 — old exports still importable) /
  Monarch / Personal Capital (now Empower Personal Dashboard) / YNAB
  exports (CSV), any old spreadsheet they kept. Import as historical
  transactions.
- **Equity portals** — grant agreements and vesting schedules are usually
  downloadable PDFs; pull them all once.
For each thing found: file the source, extract facts, add transactions.
Only THEN move to live pulls for what's missing or recent.

Run the sweep as one multi-select picker ("which of these exist?") —
checking boxes beats recalling.

## 4. First live data pull (30 min) — biggest and fastest first
Order by (value × ease) — mirror the order in the progress file's
pull-queue table and keep Status current as each pull lands. Typically:
the equity portal (largest number, usually a clean summary page), then
the main brokerage, then payroll (one paystub explains income,
deductions, and every automatic split), then the checking account. For
each, follow `references/fetching.md` and consult
`references/institutions.md` for that site's quirks. Bank on-screen
numbers into facts IMMEDIATELY, before any download — the screen is a
source. Ask for the most recent **tax return** and **latest paystubs** to
be dropped in; a filed return is the single most information-dense
document a household has (income breakdown, marginal rates, carryovers,
every account that generated income, payment/penalty history).
Order the queue with the user via a picker — "which first?" with
value-and-ease noted per option — rather than announcing an order.

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

Then the victory lap: launch `scripts/dashboard.sh` and let them SEE
their whole financial life as a page — the moment the vault stops being
homework and starts being theirs.

## 6. Optional layers — how far she goes

Core onboarding built the house; these are the utilities. Offer the
menu right after the victory lap, while the dashboard is still open
and the mood is good. Nothing here is required, every layer skips
with "later", and later genuinely works: "set up the app / the feeds /
phone Sara / the letter" reopens this section directly, even months
after `ONBOARDING.md` retired — recreate the file with just that
layer's block, work it, retire it again.

Run the menu as ONE AskUserQuestion batch: two multiSelect questions,
ordered by value-per-effort, a minutes estimate and the honest
tradeoff in every description. An empty pick means "not today" and
gets a cheerful "the vault works great as-is" — never a second ask.

**"Around the house?"** (multiSelect)
- **The app** (~2 min) — your money as a live local web page, the
  daily driver. Near-zero setup; the vault already has everything.
- **Document inbox** (~1 min) — a drop folder any device can feed;
  statements start filing themselves.
- **Weekly letter** (~2 min) — Sara's 20-second Saturday note for
  both partners. Generated on your disk; never sent anywhere.

**"The plumbing?"** (multiSelect)
- **Live sync** (~20 min, one-time) — your own free Plaid keys, and
  the everyday accounts flow in with a to-the-cent verification
  report. The biggest upgrade on the menu; the cost is a signup with
  an identity check.
- **Daily automation** (~5 min, needs live sync) — the sync runs
  itself every morning; two independent watchdogs notice if it dies.
- **Phone Sara** (~25 min) — a tiny Cloudflare Worker serves your
  numbers read-only, so Claude on your phone answers "how are we
  doing?" while the laptop sleeps. The most setup, the most magic.

For each layer picked, append its block (below) to `ONBOARDING.md`
and work it like any phase: check items AS THEY PROVE, resume
mid-block next session if this one dies. The last box of every layer
is a verification — a layer is never done because its commands ran,
only because the proof came back. Bailing on a layer mid-walk is
fine — mark it `[~] later` and respect it without relitigating;
afterward `doctor.sh` keeps a
gentle map of what's lit (its layers panel), and a Review may offer
ONE unlit layer when the month's events would have used it — never
more, never twice in a row.

### The app (~2 min)

Launch it: `scripts/dashboard.sh --app` — first launch installs its
server into the vault's venv by itself, then the page opens on
127.0.0.1. Tour it like a house, one line per room: the **glance**
(verdict tiles — spending pace, net worth, autopilot, goals — and ONE
"next" line; the ten-second check), **Spending** (pace + category
bars, click any bar for what's behind it), **Activity** (the feed,
with the uncategorized queue), **Money map** (every account and
what's in it), **Investments** (positions, allocation), **Goals**
(targets with progress), **Autopilot** (the machine — each standing
lane green or not).

Then engineer the aha: find an uncategorized transaction in Activity,
click it, and teach the rule right there — she applies it to the
whole history on the spot, same gated tools a session uses,
bean-check and all. "I taught her and she fixed the past" is the
moment this stops being a dashboard and becomes Sara. No
uncategorized transaction yet? Set a goal target instead — same
lesson, the page writes to the vault.

```markdown
### Layer: the app
- [ ] launched — dashboard.sh --app serving on 127.0.0.1
- [ ] toured — the glance and the rooms, out loud
- [ ] taught — one rule (or one goal) through the page
- [ ] verified — the write landed in the vault (rules.toml or the goal fact)
```

### Document inbox (~1 min)

`inbox/` already exists at the vault root (gitignored, like
`documents/`). Explain it in one breath: anything financial, from any
device — an AirDropped statement, a save-as from mail, a tax PDF —
goes in the folder, and `tools/run inbox.py` identifies and files it;
the checks nag while anything sits there, so nothing rots. One
picker: keep the folder local-only, or put it in iCloud
Drive/Syncthing so phones can feed it (tradeoff: the sync service
sees those files). Prove it live: drop any PDF in, run `tools/run
inbox.py`, watch it get named; `tools/run run_checks.py` now carries
an `inbox` finding — that's the nag working. Then file or remove the
test file. Want filing automatic on arrival?
`scripts/inbox-watch.plist.example` is the documented recipe — three
edits, read it before loading, never auto-installed.

```markdown
### Layer: document inbox
- [ ] explained · local-vs-synced picked
- [ ] proved — test file dropped, inbox.py named it, the inbox check fired
- [ ] watcher installed and loaded (optional — [~] is fine)
```

### Weekly letter (~2 min)

Generate it: `scripts/dashboard.sh --digest` writes
`reports/digest.html` + `digest.txt` and opens the letter — Sara's
week in five short beats, sized for a phone. The delivery decision
belongs to the household and nobody else: the tool only ever WRITES
the files; nothing is sent, ever, by anything here. Picker: how does
it reach you two? Open it Saturday mornings · one of you iMessages
the .txt to the other · mail the .html by hand · print it for the
fridge. Record the choice on THESIS.md's cadence line so every
session honors it.

```markdown
### Layer: weekly letter
- [ ] generated — digest.html + digest.txt exist
- [ ] delivery chosen by the household · recorded in THESIS.md
- [ ] verified — read the letter together; its verdict matches the assessment
```

### Live sync — Plaid, your own keys (~20 min one-time)

The pitch, honestly: after this, checking accounts and cards flow in
daily without a browser session, every sync prints a verification
report that reconciles to the cent, and re-running is always free.
The cost: a one-time signup with an identity check, and 10
institution links for life on the free plan — plenty, spent
carefully. Read `references/fetching.md` § "The Plaid lane" NOW: it
is the canonical text for everything below, including the exact
use-case paragraph to paste.

1. **The signup** (dashboard.plaid.com, ~10 min). Walk it together in
   the browser. Where the form asks what they're building, paste the
   use-case paragraph from fetching.md verbatim — it describes this
   product exactly, and honest specifics are what auto-approves.
   Company name, website (their fork), expected users: fetching.md
   has the answers. Choose the TRIAL plan; complete the ID check.
   **No keys by the end of the sitting? Stop the layer here,
   cleanly:** mark the block `[→] waiting on Plaid's ID check`, tell
   them which email to watch for, and move to another layer. Nothing
   in this lane runs without keys, and that's a pause, not a failure.
2. **Keys into the vault.** The dashboard's client_id + secret go in
   `$VAULT/.secrets/plaid.env` (the three-line file fetching.md
   shows; chmod 600). `.secrets/` is gitignored, and doctor checks
   both the permissions and the ignore — keys can never reach git.
3. **First link — simplest institution first** (an Ally-style
   username/password bank; save OAuth banks for the second link). Say
   the 10-slot rule OUT LOUD before linking: 10 links for life,
   removing one does NOT refund it, and a broken connection always
   repairs free (`python -m sara.link --repair <alias>`), never
   re-links fresh. Then `python -m sara.link ally` (the vault venv's
   python): a local page
   hands off to Plaid's own window, they approve at the bank, and the
   exact rules.toml block prints with the discovered accounts — paste
   it, name each `ledger_account`, done.
4. **OAuth banks** (Chase, Vanguard…) need a redirect URI registered
   once at dashboard.plaid.com plus `PLAID_REDIRECT_URI` in
   plaid.env — `http://localhost:8484/` is the standard answer. An
   institution that insists on HTTPS is exactly why the phone layer's
   Worker ships a `/plaid-oauth` route: register
   `https://<your-worker>.workers.dev/plaid-oauth` instead — it only
   bounces the browser back to the local link page. (No Worker yet?
   A fine reason to do Phone Sara first.)
5. **The trust ramp** — short by design. `tools/run ingest.py` is
   report-only: read the report TOGETHER — every count reconciles
   exactly, every skipped row is named, and the bank's reported
   balance is compared against the ledger to the cent. When it reads
   clean, `tools/run ingest.py --write` — same sync, applied,
   committed.

```markdown
### Layer: live sync (plaid)
- [ ] signup done · keys in .secrets/plaid.env (0600)
- [ ] 10-slot rule said out loud · first institution linked · accounts routed
- [ ] report-only run read together
- [ ] --write run — balances MATCH (or the delta seeded and explained)
- [ ] verified — doctor.sh shows the plaid rows green
```

### Daily automation (~5 min — only after live sync has earned it)

The framing matters more than the plist: nothing in this system
installs a daemon for you, because a schedule is trust, and the
by-hand runs they just did are how it was earned. When they're ready:
`scripts/ingestd.plist.example` is the documented launchd recipe —
copy it to `~/Library/LaunchAgents/com.callsara.ingestd.plist`, edit
the three UPPERCASE placeholders (paths), READ the result together
(it runs as them), then `launchctl load` it. Prove it now rather than
tomorrow: `launchctl start com.callsara.ingestd`, then read the
verification report land in `/tmp/sara-ingestd.log`. And say what
watches the watcher: a feed more than 3 days quiet raises a
`plaid_freshness` finding and a doctor warning on its own — a dead
daemon cannot go silent.

```markdown
### Layer: daily automation
- [ ] plist copied · placeholders edited · reviewed by the user
- [ ] loaded — launchctl list shows com.callsara.ingestd
- [ ] verified — started one run now; its report is in /tmp/sara-ingestd.log
```

### Phone Sara (~25 min)

What it is, plainly: a tiny read-only server of their own on
Cloudflare's free tier, serving the vault's already-committed
`reports/summary.json` — so Claude on their phone answers with their
real numbers, laptop asleep. No new copy of the data anywhere; it
reads the private GitHub repo the vault already pushes to.

Prereqs first (this is the one layer with real ones): a private
GitHub remote (hosting deferred in phase 1? do that first),
`reports/summary.json` committed and pushed (`tools/run reports.py`
writes it), Node 20+, and a free Cloudflare account.

Then `integrations/cloudflare-mcp/README.md` § "Deploy it" IS the
walkthrough — drive it top to bottom together: the fine-grained
GitHub token (Contents: Read-only, ONE repo — the README is precise
about every toggle), the wrangler.toml vars, `wrangler login` on
their PERSONAL account, the KV namespace, the two secrets, `npm run
deploy`. Sara runs the commands; the user does the browser moments
(token creation, the Cloudflare login) with the page navigated and
waiting. Smoke-test with the README's curls: bare request → 401; with
the token → the tool list.

Connect the phone: Claude app → Settings → Connectors → add
`https://<worker>.workers.dev/mcp`, OAuth fields empty, paste the
owner token once on the consent page. Last, upload **Sara Lite**
(`skills/sara-lite/` in the repo) at claude.ai → Settings →
Capabilities → Skills, so phone chats answer in her voice with every
number pulled from the connector.

The real verification is the fun one: have them ask their phone "how
are we doing?" and watch her answer with their numbers and the
snapshot date.

```markdown
### Layer: phone sara
- [ ] prereqs — private remote · summary.json pushed · node 20+ · cloudflare
- [ ] worker deployed · curl smoke test passed (401 bare, tools with token)
- [ ] phone connected · sara-lite uploaded
- [ ] verified — asked the phone "how are we doing?", she answered with the date
```

## What "done" means for onboarding
Vault scaffolded and private-repo hosted (or explicitly deferred);
institution map complete; THESIS.md written and confirmed by the user;
household profile + people files; at least the top 2–3 accounts pulled
with facts filed; the tax return ingested if available; a first
assessment published; the optional-layers menu offered, every picked
layer proven (or marked `[~] later`); everything committed,
`ONBOARDING.md` retired to `notes/`. Later sessions handle the long
tail of accounts, deferred layers, and refinement.
