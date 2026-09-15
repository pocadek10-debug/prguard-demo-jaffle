with orders as (

    select * from {{ ref('orders') }}

)

select
    order_id,
    customer_id,
    order_date,
    status,
    amount

from orders
