select
    date_trunc('month', order_date) as revenue_month,
    payment_method,
    sum(revenue_amount) as total_revenue

from {{ ref('fct_revenue') }}

group by 1, 2
