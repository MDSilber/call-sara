# Expected findings — debt-crisis (Dre)

What a competent run against this vault MUST surface. Layer 1 is
deterministic (`tools/run run_checks.py`); layer 2 is the advisory layer
walking references/playbook.md + THESIS.md against the facts.

## Layer 1 — checks that must fire (deterministic)

| check | severity | finding |
|---|---|---|
| subscriptions | alert | Paying for Spotify twice (checking + Apex Visa) — cancel one, ≈ $144/yr |
| subscriptions | watch | Netflix crept $15.49 → $17.99 (+$30/yr) |
| subscriptions | info | 4 active recurring charges ≈ $97/mo |
| deadlines | watch | 2026-08-28 Apex 0% balance-transfer offer expires |
| anomaly | watch | Food ran ~4x its usual month in July (~$575 vs ~$145 median) |
| coverage | watch | Meridian Store4477: ~55d hole (May statement never imported) |
| reconciliation | info | ×3 — no balance assertion ever (checking, Apex, Meridian) |

## Layer 2 — advisory findings that must fire

1. **The 26.99% card is the emergency** (playbook: every recommendation
   priced; THESIS rule 1). Balance ≈ $8.0k at 26.99% ≈ **$2.2k/yr of
   interest** ($148→163/mo and rising). Nothing else — no investing, no
   savings-rate talk beyond the starter buffer — outranks it.
2. **Minimum < monthly interest = negative amortization.** $140 autopay vs
   $148–163/mo interest: the balance GREW ~$1.2k over six months of
   on-time payments. Must be stated plainly (paying forever and losing),
   with a move: raise the payment above interest (~$200+ floor), take the
   0% transfer before 2026-08-28 (3% fee ≈ $240 vs ≈ $2.2k/yr — do the
   math), or call Apex hardship line.
3. **Zero cushion** (playbook: cash & flows). No savings account; checking
   ends the month ≈ $2.2k against ≈ $2.5k/mo of outflows and a rent cliff
   on the 1st. One missed shift lands on the 27% card. Move: the $1,000
   starter buffer (THESIS rule 3) before any extra avalanche dollar.
4. **Duplicate streaming** — the twin Spotify ($11.99 on checking AND on
   the Visa, both live for 6 months) is ≈ **$144/yr**; cancel one today.
   Small, but it is the persona's "find me savings" smoke test: concrete
   merchant, dollar figure, surface (Spotify account page / card autopay).
5. **Utilization ≈ 89%** ($8.0k of $9.0k limit) — credit-score drag that
   keeps the APR trap shut; falls out naturally from the transfer/payoff
   plan and should be named.

## Bonus (credit, not required)
- July DoorDash binge (~$430 of the Food spike) named without moralizing,
  tied to the review gate; not averaged into the baseline (savings-hunt
  rule: never annualize from anomaly months).
- Meridian deferred-interest promo risk (29.99%, back-charges to day one).
- Re-pull the missing May Meridian statement before trusting store-card
  totals.

## Must NOT appear (false-positive guards)
- No investing/retirement/529 recommendations while the 26.99% balance
  lives (THESIS rules 1–3 pre-commit this).
- No "cut the lattes" moralizing about the July spike in place of the
  structural fix (the card), and no baseline computed FROM July.
- No balance-transfer churn pitch beyond the one live offer already on
  file.
