---
name: verify
description: How to verify changes to the finance skill's tools end-to-end — scaffold a scratch vault and drive the real CLI surfaces.
---

# Verifying finance-system changes

The runtime surface is the CLI tools under `skills/finance/tools/` (run via
the `tools/run` wrapper) and the shell scripts under `skills/finance/scripts/`.

**First verification step — the importer test suite** (hermetic, plain
python3, builds its own scratch vault; set `FINANCE_TEST_VENV` to any
vault's `.venv` to also run the bean-query-backed paths):

```bash
python3 skills/finance/tools/importers/tests/run_tests.py            # must end ALL PASS
FINANCE_TEST_VENV=~/Finance/.venv \
python3 skills/finance/tools/importers/tests/run_tests.py            # venv paths too
```

The importers are shims into the `sara` package (skills/finance/sara/) —
its own suite covers the canonical models, writer gates, Plaid mappers,
and the ingest golden path, and the interpreter needs the package deps
(pydantic; any vault venv after init has them, or `pip install -e
'skills/finance/sara[dev]'` into a scratch venv):

```bash
cd skills/finance/sara
python -m pytest tests/ -q                                   # hermetic slice
FINANCE_TEST_VENV=~/Finance/.venv python -m pytest tests/ -q # + bean-check paths
pyright && ruff check sara tests                             # strict, zero errors
```

The Plaid lane verifies with no network via the fixture seam:

```bash
SARA_PLAID_FIXTURE=skills/finance/sara/tests/fixtures \
FINANCE_VAULT=<scratch-vault> tools/run ingest.py            # verification report
# then the same with --write: entries land, cursor advances, vault commits
```

Then drive the surfaces against a scratch vault:

```bash
V=$(mktemp -d)/vault
bash skills/finance/scripts/init_vault.sh "$V"          # scaffolds template + venv + git + gitleaks hook
# (init writes the ~/.finance-vault pointer only with an explicit --remember,
#  so scratch vaults like this never capture the default vault lookup)
export FINANCE_VAULT="$V"
T=skills/finance/tools
"$T/run" reports.py && "$T/run" run_checks.py           # must run clean on an EMPTY vault (no check errors)
"$T/run" query.py networth | balances | spend | uncategorized
"$T/run" forecast.py                                    # projections (informative "no streams" on an empty vault)
"$T/run" importers/ofx.py <file.qfx> [ledger-account]   # routing by ACCTID last-4 via rules.toml [[accounts]]
"$T/run" importers/chase_csv.py <activity.csv> <liability-account>
"$T/run" importers/invest_ofx.py <file.qfx> [account]   # brokerage activity (INVSTMTMSGSRS)
"$T/run" importers/holdings_ofx.py <file.qfx>           # positions -> price directives + holdings table
"$T/run" ingest.py [--write]                             # Plaid sync (fixture seam above for offline)
"$T/run" recategorize.py [--write]                       # rules.toml -> ledger rewrite loop
bash skills/finance/scripts/update_prices.sh --vault "$V"  # informative exit when nothing is tagged
bash skills/finance/scripts/dashboard.sh --vault "$V"        # Sara App (FastAPI, port 8787) — the default
bash skills/finance/scripts/dashboard.sh --vault "$V" --fava # fava drill-down on 127.0.0.1 (Ctrl-C to stop)
bash skills/finance/scripts/dashboard.sh --vault "$V" --home # print glance -> reports/home.html
$V/.venv/bin/bean-check $V/ledger/main.beancount        # ledger must validate after any import
```

Probes worth repeating: re-run init on an existing vault (must refuse);
typo'd query command (usage + exit 1); OFX with an ACCTID not in rules.toml
and no account arg (skip message, no crash); `FINANCE_VAULT=/nonexistent
tools/run …` (clear error); commit a full account number inside the vault
(the gitleaks pre-commit hook must block it); a `[[payee_rules]]` block with
no predicate (must warn + be ignored, not match everything).

The Sara App server (skills/finance/sara/sara/server/) has its own lane:

```bash
cd skills/finance/sara
FINANCE_TEST_VAULT=<built-demo-vault> python -m pytest tests_server -q   # own session: one process binds one vault
# contract tests: every GET 200, eight figures vs query.py to the dollar,
# categorize/set-goal/dismiss e2e on a throwaway copy (source vault untouched)
cd ../../../app && npm run build && npm run lint          # rebuild static + eslint
SARA_E2E_VAULT=<built-demo-vault> npm run e2e             # Playwright, SYSTEM Chrome
# (playwright uses channel:'chrome' on purpose — no browser downloads;
#  keep PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 on any install)
```

`npm run build` regenerates `sara/server/static/` — commit it with the
frontend change; installs never run node.

For the real vault (`~/Finance`), never leave the ledger modified: check
`git -C ~/Finance status` before and after, and run `bean-check`.
