with orders as (

    select * from {{ ref('orders') }}

),

payments as (

    select * from {{ ref('stg_payments') }}

)

select
    orders.order_id,
    orders.customer_id,
    orders.order_date,
    payments.payment_method,
    payments.amount as revenue_amount

from orders

left join payments
    on orders.order_id = payments.order_id
