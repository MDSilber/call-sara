---
name: verify
description: How to verify changes to the finance skill's tools end-to-end — scaffold a scratch vault and drive the real CLI surfaces.
---

# Verifying finance-system changes

The runtime surface is the CLI tools under `skills/finance/tools/` (run via
the `tools/run` wrapper) and `skills/finance/scripts/init_vault.sh`. There is
no test suite — verification is driving those against a scratch vault.

```bash
V=$(mktemp -d)/vault
bash skills/finance/scripts/init_vault.sh "$V"          # scaffolds template + venv + git + gitleaks hook
export FINANCE_VAULT="$V"
T=skills/finance/tools
"$T/run" reports.py && "$T/run" run_checks.py           # must run clean on an EMPTY vault (no check errors)
"$T/run" query.py networth | balances | spend | uncategorized
"$T/run" importers/ofx.py <file.qfx> [ledger-account]   # routing by ACCTID last-4 via rules.toml [[accounts]]
"$T/run" importers/chase_csv.py <activity.csv> <liability-account>
"$T/run" recategorize.py [--write]                       # rules.toml -> ledger rewrite loop
$V/.venv/bin/bean-check $V/ledger/main.beancount        # ledger must validate after any import
```

Probes worth repeating: re-run init on an existing vault (must refuse);
typo'd query command (usage + exit 1); OFX with an ACCTID not in rules.toml
and no account arg (skip message, no crash); `FINANCE_VAULT=/nonexistent
tools/run …` (clear error); commit a full account number inside the vault
(the gitleaks pre-commit hook must block it); a `[[payee_rules]]` block with
no predicate (must warn + be ignored, not match everything).

For the real vault (`~/Finance`), never leave the ledger modified: check
`git -C ~/Finance status` before and after, and run `bean-check`.
