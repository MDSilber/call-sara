# test-scenarios — regression harness for the ADVISORY layer

The importers have golden-file tests; this directory is the equivalent for
the judgment layer. Each persona is a small synthetic vault with findings
deliberately planted in the ledger and facts. An eval = point the finance
skill at a persona, ask the standard prompts, score the answers against
the rubric and the persona's expected findings.

Personas (fully built):

- **equity-comp/** — Maya & Sam, pre-IPO equity-comp couple. Planted:
  supplemental-withholding gap, concentrated employer stock (three-layer),
  idle cash above cushion + FDIC breach, stale reconciliation, dead feed.
- **debt-crisis/** — Dre, renter. Planted: 26.99% APR carried, minimum
  below monthly interest (negative amortization), zero cushion, duplicate
  streaming, price creep, category spike, missed-statement hole.

Each persona ships `vault/` (overlay in the vault-template-demo format)
and `expected-findings.md` (what MUST fire, split into the deterministic
check layer and the advisory layer, plus must-NOT-appear guards).

Ledger sizes: equity-comp ~150 directives; debt-crisis ~250 — larger than
first planned because the price-creep plant needs 3 billing cycles at each
price (6 months minimum).

## Running an eval

1. Scaffold the persona into a scratch vault (never into ~/Finance):

   ```bash
   P=equity-comp                       # or debt-crisis
   V=$(mktemp -d)/vault
   bash skills/finance/scripts/init_vault.sh "$V"
   cp -R "test-scenarios/$P/vault/." "$V"/
   echo 'include "2026.beancount"' >> "$V/ledger/main.beancount"
   "$V/.venv/bin/bean-check" "$V/ledger/main.beancount"   # must be clean
   ```

   (If pip is blocked on the corporate index:
   `PIP_INDEX_URL=https://pypi.org/simple bash skills/finance/scripts/init_vault.sh "$V"`.)

2. Sanity-check the deterministic layer first — every layer-1 row in the
   persona's `expected-findings.md` must fire before the advisory eval
   means anything:

   ```bash
   FINANCE_VAULT="$V" skills/finance/tools/run run_checks.py
   ```

3. In a FRESH session with `FINANCE_VAULT="$V"` exported, invoke the
   finance skill with the standard prompt set, one conversation each:
   - "how are we doing"
   - "find me savings"
   - "make me an assessment"

4. Score each answer with `rubric.md` (0/1/2 per row; persona rows +
   craft rows). Compare coverage against `expected-findings.md` — every
   MUST row needs a non-zero score, and nothing from the must-NOT list
   may appear. Pass bar: ≥ 80% of points, no 0 on a MUST row.

5. Regressions: keep the filled rubric next to the run transcript. A
   change to SKILL.md, the references, or the checks should re-run at
   least one persona before shipping.

## Notes for fixture authors

- Everything here is synthetic; keep it that way. No real institutions'
  account formats, no real people.
- If you change a persona ledger, re-verify layer 1 fires (step 2) and
  keep `expected-findings.md` in sync — the harness is only as honest as
  that file.
- The demo vault (`skills/finance/vault-template-demo`) stays deliberately
  healthy; plant pathologies HERE, not there.
