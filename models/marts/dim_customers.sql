with customers as (

    select * from {{ ref('customers') }}

)

select
    customer_id,
    first_name,
    last_name,
    first_order,
    most_recent_order,
    number_of_orders

from customers
