# Institution playbook — how each site behaves (SHAREABLE)

Site behavior, URLs, and export quirks: knowledge that transfers between
households. **No personal identifiers here** — no account numbers, no
last-4s, no owners, no employers, no balances. Who-logs-into-what lives in
each vault's `facts/household/institutions.md`.

Read the section for the institution you're pulling from. After the FIRST
successful pull from a new institution, add a section here: real app URL,
where statements/exports live, export format, date-range controls, widget
quirks. This file grows into the practice's institutional memory.

---

## Shareworks / Morgan Stanley at Work (employer equity)
Employer equity portal. **One employer per subdomain** —
`https://<company>.solium.com`; the generic login usually won't work, and
each spouse's employer is a separate login. Public-company plans show real
held shares on a "Share Purchase and Holdings" tab and RSU refreshes in the
awards table; watch for a **blackout banner** and note its end date.

- Login can bounce to the `www.morganstanley.com/atwork` marketing page; the
  real app is `https://shareworks.solium.com/solium/servlet/ui/dashboard`.
- **Dashboard** shows total / vested / unvested value plus, for private
  companies, **both the preferred price AND the 409A value** — capture both,
  and any upcoming vests. Bank all on-screen numbers as facts before touching
  documents; the screen is a source.
- **Portfolio → `.../ui/portfolio/awards`**: the grants table lives inside a
  widget the accessibility tree can't expose — scroll the table region and
  screenshot; ISO / NQ / RSU sections stack vertically.
- **Dates in table links are DD/MM/YYYY** — `12/06/2025` means 12 June 2025.
- **Documents → `.../ui/documents`**: expand `Grant Agreements (n)`. Each
  opens an inline viewer with a `Download this document (PDF)` link. Grant
  agreements are the authority on vesting (commencement dates, monthly
  schedules, double-trigger RSU language) — always worth downloading; the
  summary table hides schedule details that change advice.
- A certificate / transaction-report CSV also exports from Documents.
- **Activity → Reports → Account Summary** generates a statement for ANY date
  range (choose PDF + "Full" + Adjusted cost basis) — the fastest way to get
  realized-gain/sale detail for a tax year; sale breakdowns include gross
  proceeds, fees, and per-lot wash-sale-adjusted gains.
- File PDFs to `documents/Assets/US/Shareworks/<Owner>/`; facts to
  `facts/equity/<issuer>-<owner>/index.md`.

## Chase (checking / savings / cards)
- **Best route: the universal download page** —
  `https://secure.chase.com/web/auth/dashboard#/dashboard/transactions/downloads`.
  Deep-linking to per-account activity pages often renders BLANK for an
  automated tab; go via the overview and click through, or use the downloads
  page directly once logged in.
- The form has 3 selects: Account, File type, Activity. Pick **CSV for credit
  cards** (it carries Chase's Category column — feed to
  `importers/chase_csv.py`) and **QFX for checking/savings**
  (`importers/ofx.py`).
- **The layout shifts vertically after Account is chosen** (an info line
  appears) — never reuse click coordinates across steps; re-`read_page` /
  `find` or re-screenshot after each selection.
- "All transactions" hits a ~1,200-row cap on active cards → error page.
  Use "Year to date" / "Last year" / a date range. Low-volume accounts
  accept "All transactions".
- After each download a confirmation page shows a **"Download other
  activity"** button — click it to loop the next account. Each spouse's
  accounts need that spouse's login; sign out to switch users.
- Files land as `~/Downloads/Chase<last4>_Activity_<YYYYMMDD>.{csv,qfx}`.

## Ally (checking / savings)
- Login `secure.ally.com`; dashboard `/dashboard`. **The dashboard hangs on a
  loading skeleton if the agent navigates the tab itself** (bot detection) —
  it renders fine when the user loads it. Don't reload; if stuck, have the
  user click into an account, then drive from there.
- **Transactions:** account details → "Download Transactions" opens a panel
  with format (CSV/QFX) + range selects. Pick QFX, "All available dates"
  (~18mo). One file per account → `importers/ofx.py`.
- **Statements are the better source for balances:** `/bank/statements-and-forms`.
  Each monthly statement is ONE PDF covering ALL accounts with
  beginning/ending balances (perfect for `balance` assertions). Buttons open a
  blob viewer tab invisible to the extension — force real downloads by
  patching `URL.createObjectURL` to capture the blob, then `a.download` +
  click, looping the statement rows and naming files
  `ally-statement-YYYY-MM-DD.pdf`. Chrome blocks after the first with "wants
  to download multiple files" — have the user click Allow once; the rest
  queue through.
- A year-selector combobox switches statement year (~7 years available).
- Linked external accounts shown may be closed — confirm before importing.

## Vanguard (brokerage / IRAs)
- After login you land on `www.vanguard.com/en/investor/portfolio/dashboard/`.
- **Don't screen-scrape holdings — export.** The Download center is
  `https://personal1.vanguard.com/ofu-open-fin-exchange-webapp/ofx-welcome`.
- The form has THREE required steps or it silently reloads with a validation
  error you can't screenshot (that domain blocks screenshots):
  1. Step 1 radio — **"Quicken: All funds to a single account"** (the TOP
     radio). Yields a `.qfx` (OFX): holdings + prices + 18mo transactions +
     account IDs in one machine-readable file. Skipping the radio = "A valid
     option is required" and no download.
  2. Step 2 date range — **18 months** (the max per pull; older activity
     needs archived statements instead).
  3. Step 3 — check the **checkAll** box atop the account table.
  Then Download. File lands as `~/Downloads/OfxDownload.qfx`.
- **One `.qfx`, two importers** — the same download carries BOTH the
  positions snapshot and up to 18 months of activity (`INVSTMTMSGSRS`);
  there is no separate activity export. Both route accounts by the trailing
  digits of `<ACCTID>` (rules.toml `[[accounts]]`).
  - `importers/holdings_ofx.py` — price directives + a per-account holdings
    table to file as facts.
  - `importers/invest_ofx.py` — books the ACTIVITY into the ledger: buys and
    sells with lots, reinvestments, dividend / capital-gain distributions,
    and bank transfers (contributions/withdrawals via `[[payee_rules]]`).
    Dry-run first, `--write` to append; `import-hash:` dedupe makes
    overlapping 18-month pulls safe. At import it reconciles ledger units
    per commodity against the file's `<INVPOSLIST>` — MATCH gets a balance
    assertion, MISMATCH means the position predates the vault: seed the
    suggested opening lot, nothing blocks.
- **Booking**: buys land as `{cost}` lots and coexist with snapshot-seeded
  no-cost units under Beancount's default STRICT booking — no migration to
  start importing. The first sell facing several lots needs `"FIFO"` added
  to that account's `open` directive, and sells can never consume no-cost
  snapshot units — convert the snapshot into a costed opening lot first
  (full recipe in `invest_ofx.py`'s docstring).
- File to `documents/Assets/US/Vanguard/<Owner>/YYYY-MM-DD.holdings-18mo.qfx`.
- Aggregators sync poorly here (site blocks daytime hours); the export is
  the source of truth.
- Automation quirk: the mutual-fund Trade/Exchange pages' ACCOUNT dropdown
  ignores synthetic clicks (old `personal.vanguard.com` widgets) — if a picker
  won't respond, hand that one selection to the user rather than fighting it.
  The "Buy & sell" hub → "Trade mutual funds" → Exchange offers a
  "full exchange page" fallback that renders plain HTML checkboxes/inputs.

## Fidelity NetBenefits (employer 401k)
- Login `nb.fidelity.com`; lands on
  `workplaceservices.fidelity.com/mybenefits/navstation/navigation#/`.
- Home shows the portfolio total plus **every employer plan the person has
  ever had**, including $0 plans from old jobs — inventory all of them; old
  plans are the most-forgotten money.
- **Holdings: click "View investments"** → `.../app/portfolioInvestments`
  renders the full table (ticker, quantity, price, value, cost basis, gain).
  It's an SPA: `get_page_text` returns almost nothing — screenshot it. A
  single-fund plan needs no export.
- "View your statements" is an SPA link that may not navigate on click; if
  statements are needed, dig further rather than trusting that link.
- File facts to `facts/accounts/fidelity-<owner>-<plan>/`.

## Empower (employer 401k)
- Login `participant.empower-retirement.com`; dashboard at
  `.../participant/home/#/dashboard/retirement-income`. `get_page_text`
  works well here — no screenshots needed.
- Dashboard exposes balance, contribution %, YTD contributions, rate of
  return, beneficiary count, and Empower's retirement projection.
- Holdings: "View/manage investments" → an `.../my-investments` page whose
  text extracts cleanly.
- Statements: "View statements and documents" under "I want to..." — pull one
  quarterly to quantify any asset-based admin fee.
- Facts to `facts/accounts/empower-<owner>-<plan>/`.

## State 529 plans (example: NY, nysaves.org)
- Enter via the homepage's "Log In" link — deep-linking the login URL often
  404s.
- After login: account list → "View Details" → an overview page that has
  EVERYTHING as text: balance, fund units/price, principal vs earnings, YTD
  contributions, recent transactions, and a "Download Transactions" (Excel)
  link. `get_page_text` captures it all — no screenshots needed.
- Facts to `facts/accounts/<plan>529-<beneficiary>/`.

## Crypto (exchange & self-custody)
- **Coinbase fast path**: after login, `coinbase.com/home` via
  `get_page_text` yields balances (units + USD cash). For an immaterial
  "token" position, reading the home page quarterly beats maintaining a CSV
  pipeline; export only if taxes need lot detail.
- **The moment a position stops being immaterial — or anything sells —
  book properly**: FIFO on the crypto accounts, every acquisition with
  `{cost}`. Transfers (exchange ↔ self-custody) are NOT taxable, but basis
  rides along — the same `{cost}` on both legs. Every swap IS a
  disposition: `@ price` plus a gains income leg. Staking rewards are
  ordinary income at receipt FMV (the principal coming back is just a
  transfer). Dust under $10: note it, skip the bookkeeping.
- **Verify any 1099-DA against own lot records** — exchange-reported
  acquisition dates and basis are routinely wrong.
- **Filter scam tokens** (unicode look-alike tickers) before anything
  enters facts or the ledger.

## Private-fund / SPV platforms (AngelList and similar)
_(document on first pull)_ — capital-call notices, annual tax report / K-1;
typically all manual downloads.

## State unclaimed-property databases (found-money sweeps)
- **NY: `ouf.osc.ny.gov/app/claim-search`** (the old `ouf.osc.state.ny.us` URL 404s).
  SPA — the form CLEARS on each page load; re-enter fields after navigation.
  Exact name matches sort first; a broad fallback list follows (result counts in
  the hundreds are normal — only the top exact matches matter).
- **Search every name variant separately**: nickname vs legal first name return
  DIFFERENT result sets (e.g. a short form finds items the legal name misses),
  plus maiden names, middle initials, old addresses, parents' names, and family
  BUSINESS names. This is the single highest-yield trick.
- NY hides dollar amounts until a claim is started; Connecticut
  (`ctbiglist.gov`) shows amounts in results. NJ: `unclaimedfunds.nj.gov`;
  multi-state: `missingmoney.com`.
- Each NY row has a **Property ID** — pasting it into the search page's
  Property ID box jumps straight to that item (best way to hand someone their
  claim; there are no per-item URLs).
- Claiming: select CLAIM on each row → Continue to File Claim → relationship
  ("Owner (Self)" for own property; joint accounts still = Owner (Self);
  deceased owners = Surviving Spouse / Estate Representative / Other Heir,
  needing death cert + heirship proof) → SSN + ID upload. Multiple properties
  bundle into ONE claim; extra same-name items often auto-surface in the claim
  flow. Simple claims pay in ~2-8 weeks. Free — never a "finder" fee.
  THE OWNER files (SSN/ID step is theirs alone); the agent only finds and
  preps the list.

## Sources that age well
- `chrishutchins.com` play pages intermittently 503. The substack mirror —
  `chrishutchins.substack.com/p/<slug>` — serves the same posts reliably;
  fetch there first when the main site errors.
