# Expected findings — equity-comp (Maya & Sam)

What a competent run against this vault MUST surface. Two layers: the
deterministic checks (verify by running `tools/run run_checks.py` — exact
titles may drift, the finding must not), and the advisory layer (the agent
walking references/playbook.md against the facts — these do NOT come from
checks.py and are the real test).

## Layer 1 — checks that must fire (deterministic)

| check | severity | finding |
|---|---|---|
| concentration | watch | NIMBUS (illiquid paper) ≈ 52% of net worth vs 15% ceiling |
| deadlines | watch | 2026-09-01 Nimbus S-1 window / 10b5-1 + estate-freeze decision |
| reconciliation | watch | Checking7710 assertion is ~87d stale — re-anchor |
| reconciliation | info | Marcus Savings8802 never asserted |
| reconciliation | info | Golden State CU 2201 never asserted |
| coverage | watch | Golden State CU 2201 stale feed (~87d silent) |
| subscriptions | info | 2 active recurring charges ≈ $67/mo |

## Layer 2 — advisory findings that must fire (playbook rules)

1. **Supplemental-withholding gap** (playbook: tax cliff map, "RSU/bonus
   withheld at flat 22% but marginal is 32–37%"). Facts: ~$210k of 2026 RSU
   settlements withheld at 22%, marginal 35%. The gap must be stated in
   dollars: (35% − 22%) × $210k ≈ **$27k federal shortfall** (~$23k on the
   $180k settled YTD), on top of a 2025 return that already under-withheld
   (84% coverage, penalty paid). Move: estimated payments or W-4
   supplemental top-up NOW, sized against the 110%-of-2025 safe harbor
   ($113.3k). Credit for citing the December-settlement timing trap.
2. **Employer concentration — the three-layer rule** (playbook:
   concentration & liquidity events). NIMBUS ≈ 52% of net worth AND the
   salary AND the unvested 10,500 shares are the same company — the
   household is MORE concentrated than the balance sheet shows. Must
   connect stock + job + unvested grants, not just repeat the check's 52%.
   Moves that should appear: pre-commit the sell rule (THESIS rule 2 —
   credit for noticing it exists), 10b5-1 before the 2026-09-01 window,
   drawdown test framing. Must NOT price the stake at the $52 secondary
   number (thesis rule 1).
3. **Idle cash above cushion** (playbook: cash & flows — idle-drag + FDIC).
   Marcus ≈ **$298k** vs a $57k floor with NO earmark, growing $8k/mo, plus
   ≈ $67k sitting in checking against a ~$15k sweep line. Two findings in
   one: (a) ~$240k unearmarked cash needs a job (T-bills/ladder vs the
   thesis) with the annual drag priced in dollars; (b) **FDIC**: Marcus is
   over $250k at one institution — name the split move.

## Bonus (credit, not required)
- Golden State CU: ~$1.9k account paying $12.95/mo in service fees
  (~$155/yr) — close-or-justify is sitting in liquidity.md.
- No individual disability / 1x-salary-only life coverage against a
  contingent-equity household (playbook: insurance & protection).
- No estate documents while an IPO approaches (estate-freeze window is in
  the deadline).

## Must NOT appear (false-positive guards)
- No "diversify by selling NIMBUS now" — it is PRIVATE stock; the
  actionable moment is the liquidity event (concentration detail + thesis).
- No investment pitch for the cushion portion of cash.
- No treating USD.EQ paper as spendable net worth in the headline number.
