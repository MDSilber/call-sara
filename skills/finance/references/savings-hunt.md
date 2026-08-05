# Savings hunt — finding deals, waste, and free money (SHAREABLE)

Reusable mechanics for the advisor's standing money-saving mandate. Nothing
household-specific lives here — the household's actual merchants, findings,
and decisions go in the vault (`notes/` for the hunt log, `THESIS.md` for the
mandate, `facts/household/calendar.md` for the recurring chores). Run these
plays for any household; write what you FIND to their vault.

## The method
1. **Mine the ledger first.** Every hunt starts from the household's real
   data, not generic tips: recurring charges (4+ months), fees/interest paid,
   top merchants, duplicate same-amount charges, unexplained lines. bean-query
   over `Expenses` gives all of it. A tip that doesn't match a real merchant
   in their ledger is noise.
2. **Fan out research agents by genre**, each fed the household's ACTUAL
   merchant list (anonymized of names/addresses): retention offers, hidden
   perks in memberships already paid for, employer benefits, settlements and
   found money, card optimization. Demand $/yr and a source URL per find;
   have agents rank and be honest about what to skip.
3. **Verify before recommending.** Agents get stale facts wrong (prices,
   dead programs, changed terms); spot-check the top finds. Never annualize
   a recurring charge from months containing an anomaly — compute the
   baseline from the median months and check whether the spike is already
   fixed. A genuine billing accident gets one polite support ticket for a
   courtesy credit plus usage alerts, not a cancel-this recommendation.
   Kill anything whose catch outweighs the gain — and say so in the vault
   so it isn't re-pitched (e.g. "X: SKIP, reason"). Record VOID findings
   WITH the correction ("baseline is $N/mo; the spike was a one-time
   accident, since fixed") so no future hunt re-flags them.
4. **Log findings as a checklist in the vault** with status marks and $/yr,
   deliver the ranked list, and put an annual re-negotiation chore on the
   household calendar — discounts expire in ~12 months by design. One
   status legend everywhere: `[ ]` todo · `[→]` in progress · `[x]` done ·
   `[~]` owner's call, parked · `[i]` info. When a finding moves to `[x]`,
   record the ACTUAL $/yr realized in its own column — estimates and
   captures never blend — and quote the cumulative "this practice has
   found $N/yr so far" figure in every hunt brief and the annual report.
   Record rejections and VOIDs inline with the reason and "do not
   re-raise" — future hunts must never re-pitch a decided item. When the
   owner says "leave it" on a negotiable bill, keep the negotiation
   mechanics in the log anyway, marked for the annual re-check only.
5. **Deadline items first**, then recurring $/yr by size, then one-time
   sweeps. Never let a settlement or enrollment window lapse while polishing
   the list.

## Hunt genres (what each looks for)
- **Fees & duplicates** (pure ledger): late fees (call for refunds; fix
  autopay-full-balance + due-date alignment), ATM/foreign fees (fee-refunding
  checking accounts), duplicate annual fees across spouses (product-change,
  keep the credit line), same-amount double charges within days (dispute).
  Set a $0.01-threshold transaction alert at each bank — a standing
  tripwire for fraud and weird fees.
- **Recurring-charge audit**: annualize every 4+/month merchant; for each ask
  cancel? downgrade tier? annual billing? bundle swap? employer stipend
  covers it? A subscription nobody defends is a leak. Start from the
  checks run (`tools/run run_checks.py`) — it flags cadence-locked
  merchants, price-creep, and twin billing mechanically — not a fresh
  ledger scrape.
- **Cash yield audit**: rate-audit EVERY cash-like balance, not just the
  excess — list each account's APY; brokerage sweep defaults and legacy
  savings accounts are the classic leaks. Flag any balance over $10K
  earning >1% below the current Treasury MMF yield, and compare AFTER-TAX
  yield: Treasury interest is state-tax-exempt, which beats a nominally
  higher HYSA in a high-tax state. Parked cash ≥ $25K? Check the current
  bank-bonus tables (e.g. Doctor of Credit) — a move routinely adds
  $300-$1,000+.
- **Retention offers**: the 4-step that works across vendors — (1) know the
  competitor's exact price first, (2) say "cancel" to reach retention, not
  the front line, (3) name your number, then silence-and-decline the first
  offer, (4) get the new rate + end date in writing and calendar the re-call.
  Chat flows (news subscriptions) and cancel-flow screens (streaming) beat
  phone for many; some vendors only win-back AFTER a real lapse. Never
  negotiable: regulated utilities, transit, Apple.
- **Hidden perks already paid for**: card benefit pages (credits, free
  subscriptions, purchase protection/extended warranty — file claims!),
  memberships (auto clubs: free notary, rental discounts, DMV desks),
  retailer bundles (grocery/delivery perks inside Prime-type memberships),
  loyalty programs' member rates, employer discount portals. Activate-once
  items go on the checklist with their deadline. First week of December,
  run the card-credit sweep: calendar-year credits (airline incidental,
  hotel, statement) expire Dec 31 — burn them, check points expiry (any
  qualifying activity resets most clocks), and redeem expiring transfer
  bonuses.
- **Employer benefits audit**: stipend wallets (check balances/expiry —
  use-it-or-lose-it), FSA/DCFSA claims actually filed (funding without
  claiming = forfeiting), pre-tax commuter, backup childcare, legal plans
  (cover estate documents), charitable match, group-rate insurance.
- **Medical bills & healthcare**: mine the ledger for medical/pharmacy
  merchants to seed the audit. Never pay a medical bill before the
  insurer's EOB arrives and matches it; for bills over ~$200 request the
  itemized bill and cross-check billed codes against services actually
  received — errors are the norm, not the exception. Price every
  recurring prescription's copay against GoodRx/cash; insurance often
  isn't cheapest. Wrong-bill escalation ladder: evidence → billing dept →
  supervisor + formal complaint → 30-day warning letter → small claims
  (~$30-40 to file; economically irrational for providers to fight).
- **Card optimization** (no churning-for-sport): match the 2-3 card setup to
  the household's real top merchants; one welcome bonus at a time funded by
  organic spend; card-linked offer sweeps monthly; points transferred to the
  partner they actually use — before devaluation deadlines. Two-player
  plays for couples: stagger identical card freebies (one spouse activates
  now, the other near the enrollment deadline — nearly double the free
  months); before either spouse applies for anything, the other sends a
  referral link first; product-change a duplicate annual-fee card to its
  no-fee sibling instead of canceling (keeps the credit line and history);
  and the last-resort retention play — one spouse cancels, the other
  re-signs as a new customer. Pay-taxes-by-card check: IRS processors
  charge ~1.8% (verify the current fee at irs.gov/payments first) — worth
  it ONLY against a welcome-bonus minimum spend or an earn rate above the
  fee; estimated payments are the cleanest recurring vehicle for
  otherwise-unreachable minimums. Gift-card stack for planned spend at
  merchants already in the ledger: discounted gift cards (resale
  marketplaces, grocery fuel-point stacking) + portal + card-linked offer
  on top — real planned spend only, never hold large balances. All
  subject to decided items: a duplicate the household chose to keep
  stays kept.
- **Found money / one-time sweeps**: state unclaimed-property databases
  (the highest-yield sweep — method below; portals in `institutions.md`);
  class-action settlements matching their merchant footprint — bucket each
  hit as open-now-with-deadline (file every eligible household member),
  auto-included (note only), or not-yet-open (record the claim URL in the
  vault so a later session files the moment it opens); forgotten
  401(k)/HSA/equity at ex-employers (password-reset old brokerage logins;
  DOL lost-and-found; force-cashed buyout proceeds escheat to the state);
  billing-spike courtesy credits (one polite ticket); the password-manager
  audit (below).
- **Credit-report sweep** (annual, or before any big borrow): pull all
  three bureaus (annualcreditreport.com — free weekly), redact identifiers
  before any file enters context; dispute errors (wrong balances, closed
  accounts reporting open, not-mine lines); then the top-3 score levers,
  ranked by impact for THIS report — typically utilization timing (pay
  before statement close, not due date), a limit-increase request on the
  oldest card (no hard pull if the issuer allows), and keeping the oldest
  line open. Price the payoff in borrowing terms: a score band jump
  repriced at the next mortgage/auto/insurance renewal is the $ figure.
- **Deduction & credit sweep** (tax season, or on a life change): a short
  structured interview — employment shifts, side income, home/vehicle use,
  medical, education, energy upgrades, dependents, charitable — mapped
  against the ledger's actual spend so nothing claimed is unsupported and
  nothing supported goes unclaimed. Output is a candidates-with-evidence
  list for the CPA (or the return), never a filing on its own.
- **Insurance re-shop** (annual): re-quote home/auto direct at carrier
  sites — auto varies ~2x between carriers for identical coverage, home
  10-20%. Raise deductibles to what the cash cushion absorbs, and never
  file a claim below ~2x the deductible (rate hikes outlast the payout).
  Ask for alarm/safety (15-20% off home), low-mileage/pay-per-mile tiers,
  and pay-in-full. Umbrella sanity price: ~$150-350/yr for the first $1M,
  ~$100/M after — paying much more means shop it. Skip rental-car
  coverage a card already provides.
- **Big-ticket purchases** (car, renovation): pre-negotiated buying
  programs played against each other, model-year-end timing,
  independent-broker quotes vs direct, bundle math, resident tax exemptions.

## The unclaimed-property sweep (do it properly once)
State databases reward thoroughness, not cleverness:
- **Search every household name in BOTH nickname and formal forms** —
  they return different results. Then every old address, deceased
  relatives, and family business names.
- **Confirmation tells**: a shared old address or a recognized co-owner
  name on a listing is what separates the household's property from a
  stranger's with the same name. Ask the owner to confirm ambiguous ones.
- **Route each hit to the right claim path**: the owner files as self;
  a living co-owner claims directly as surviving owner; a deceased
  relative's property is an heir claim (death certificate + proof of
  heirship); business property goes through the company's authorized
  officer or successor. Estate-tied hits feed the estate-attorney agenda.
- **Claim even blind**: many states hide dollar amounts until a claim is
  filed, and decades-old stock or insurance positions can have grown —
  claim anything plausible.
- **The advisor surfaces; the OWNER files.** Claims require the owner's
  SSN and ID, so hand each person their own list with the claim steps.
  Filing is free; never pay a "finder."
- **Run a second round with each relative as the primary search name** —
  co-owned property indexes under either name, so this reliably surfaces
  hits round one missed.

## Password-manager audit as asset discovery
A one-time cleanup of the household's password manager doubles as a
forgotten-asset sweep: flag every entry for a brokerage, 401(k)
recordkeeper, HSA custodian, or equity platform the vault doesn't know
about, and hand the owner a short "do you hold anything here?" list.
Side benefit: corrected usernames and URLs make the statement-fetch
pipeline work on the first try.

## Guardrails
- Clever-and-legal only: no fraud, no fake returns, no ToS-violating
  manufactured spend, no lying to underwriters. If a play needs a lie, skip.
- Respect the household's decided items: anything the vault marks decided or
  "do not re-raise" stays closed regardless of what a hunt surfaces.
- Don't moralize lifestyle spending; the target is money leaking without
  buying anything.
- Present findings with a dollar figure and the exact fix. A finding without
  a next action is trivia.
