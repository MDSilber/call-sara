---
type: goals
date: 2026-08-01
verified: 2026-08-01
source: demo data (synthetic)
---
# Goals & thresholds (machine-read by tools/checks.py)

The checks read the yaml block below. Demo values — tuned so a fresh demo
vault starts with a clean bill of health.

```yaml
concentration_ceiling_pct: 15      # any single position above this % of net worth = flag
cash_cushion_months: 6             # advisor-set; dollars computed from the spend baseline
deadline_horizon_days: 45          # surface anything due within this window
anomaly_min_amount: 400            # ignore charges below this
savings_rate_target_pct: 20        # of net income
retirement_target: 2000000         # invested, household, by 2055
show_walkaway: true                # Sara Home's Independence room (walk-away number, what-if dials, history replay). Rule: this flag wins when set; with no flag the room appears exactly when retirement_target is set. The demo keeps it on so the full product shows.
education_target: 120000           # Riley's 529 by 2038
house_downpayment: 120000          # the house fund target (THESIS: by 2029)
house_year: 2029                   # target purchase year
project_budget_nursery_refresh: 900   # the #nursery-refresh envelope (Sara Home projects card)
milestone_net_worth_above: [100000, 250000]   # info finding when liquid net worth first crosses one (flat keys — the goals parser is flat)
milestone_net_worth_above_crossed: []         # auto-updated by tools/checks.py so each fires once; remove a value to re-arm it
```
