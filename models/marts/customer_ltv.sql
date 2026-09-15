with customers as (

    select * from {{ ref('dim_customers') }}

),

revenue as (

    select
        customer_id,
        sum(revenue_amount) as lifetime_value

    from {{ ref('fct_revenue') }}

    group by customer_id

)

select
    customers.customer_id,
    customers.first_name,
    customers.last_name,
    revenue.lifetime_value

from customers

left join revenue
    on customers.customer_id = revenue.customer_id
