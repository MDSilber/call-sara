---
type: goals
date: 2000-01-01
verified: 2000-01-01
source: to be set at the founding interview
---
# Goals & thresholds (machine-read by tools/checks.py)

The checks read the yaml block below. `null` = not yet set (the check
reports it as open rather than erroring).

```yaml
concentration_ceiling_pct: 15      # any single position above this % of net worth = flag
cash_cushion_months: 6             # advisor-set; dollars computed from the spend baseline
deadline_horizon_days: 45          # surface anything due within this window
anomaly_min_amount: 400            # ignore charges below this
savings_rate_target_pct: null      # set from the baseline
retirement_target: null            # set from the baseline
education_target: null             # per child, if any
house_downpayment: null            # a planned purchase seeds the home-page what-if toggle
house_year: null                   # target calendar year for it
```
