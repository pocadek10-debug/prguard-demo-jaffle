with source as (

    select * from {{ ref('raw_signups') }}

),

renamed as (

    select
        id as signup_id,
        user_id as customer_id,
        signup_date,
        channel

    from source

)

select * from renamed
