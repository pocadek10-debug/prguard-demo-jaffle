# PRGuard demo — jaffle_shop

A [dbt](https://www.getdbt.com/) project used to demonstrate
[PRGuard](https://github.com/pocadek10-debug/prguard-dbt) on a real pull request.

It is dbt-labs' classic [`jaffle_shop`](https://github.com/dbt-labs/jaffle_shop)
sample, extended with a marts layer and two exposures so that a change to a
staging model has a downstream blast radius worth showing:

```
stg_customers ─┐
stg_orders ────┼─→ customers ─→ dim_customers ─┐
stg_payments ──┘                               ├─→ customer_ltv ─→ "Customer Health Report"
        │       └─→ orders ───→ fct_orders     │
        │                   └─→ fct_revenue ───┘
        └───────────────────────→ fct_revenue ─→ monthly_revenue ─→ "Exec Revenue Dashboard"
```

## What to look at

Open the pull request in this repo. PRGuard posts a single comment on it
reporting downstream impact, missing tests, best-effort breaking changes, and
the test-coverage delta — from dbt metadata alone, with no warehouse connection.

The PR deliberately contains three realistic mistakes:

1. `stg_payments.payment_method` is dropped from the model, while
   `fct_revenue` and `monthly_revenue` still select it — and the exec
   dashboard slices by it.
2. `stg_orders.customer_id` is renamed to `user_id`.
3. A new model, `stg_signups`, ships with no tests.

None of these fail `dbt parse`. That is the point: dbt happily compiles a
project that is about to serve wrong numbers.
