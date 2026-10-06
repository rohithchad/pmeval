{#- One row per executed trade. yes_price is the YES contract price in dollars, which is also
    the market-implied probability of YES at that moment. Live and historical tiers share the
    same field names, so the two sources are unioned and de-duplicated on trade_id. -#}

with unioned as (

    select payload, ingested_at from {{ source('bronze', 'kalshi_live_trades') }}
    union all
    select payload, ingested_at from {{ source('bronze', 'kalshi_historical_trades') }}

),

parsed as (

    select
        {{ jstr('trade_id') }}                                       as trade_id,
        {{ jstr('ticker') }}                                         as market_ticker,
        {{ to_utc_timestamp(jstr('created_time')) }}                 as traded_at,
        {{ dollars_to_probability(jstr('yes_price_dollars')) }}      as yes_probability,
        {{ dollars_to_probability(jstr('no_price_dollars')) }}       as no_probability,
        try_cast({{ jstr('count_fp') }} as double)                   as contracts,
        {{ jstr('taker_side') }}                                     as taker_side,
        coalesce(try_cast({{ jstr('is_block_trade') }} as boolean), false) as is_block_trade,
        {{ to_utc_naive('ingested_at') }}                          as ingested_at
    from unioned

),

deduplicated as (

    select
        *,
        row_number() over (partition by trade_id order by ingested_at desc) as row_rank
    from parsed

)

select * exclude (row_rank)
from deduplicated
where row_rank = 1
