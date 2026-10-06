{#- One row per market, candle period and period end. Prices are probabilities in [0, 1].
    The live API returns nested fields with a _dollars suffix (price.close_dollars); the
    historical API drops the suffix (price.close). coalesce() reads whichever is present.
    A period with no trades has an empty price object, so trade prices are NULL there.
    `period_end_at` is when the candle closed: nothing in it was knowable before that moment. -#}

with unioned as (

    select payload, ingested_at from {{ source('bronze', 'kalshi_candlesticks') }}
    union all
    select payload, ingested_at from {{ source('bronze', 'kalshi_live_candlesticks') }}
    union all
    select payload, ingested_at from {{ source('bronze', 'kalshi_historical_candlesticks') }}

),

parsed as (

    select
        {{ jstr('ticker') }}                                          as market_ticker,
        cast({{ jstr('period_minutes') }} as integer)                 as period_minutes,
        {{ unix_to_utc_timestamp(jstr('end_period_ts')) }}            as period_end_at,
        {{ dollars_to_probability("coalesce(" ~ jstr('price.close_dollars') ~ ", " ~ jstr('price.close') ~ ")") }}
                                                                      as trade_close_probability,
        {{ dollars_to_probability("coalesce(" ~ jstr('price.previous_dollars') ~ ", " ~ jstr('price.previous') ~ ")") }}
                                                                      as previous_trade_probability,
        {{ dollars_to_probability("coalesce(" ~ jstr('yes_bid.close_dollars') ~ ", " ~ jstr('yes_bid.close') ~ ")") }}
                                                                      as yes_bid_close_probability,
        {{ dollars_to_probability("coalesce(" ~ jstr('yes_ask.close_dollars') ~ ", " ~ jstr('yes_ask.close') ~ ")") }}
                                                                      as yes_ask_close_probability,
        try_cast(coalesce({{ jstr('volume_fp') }}, {{ jstr('volume') }}) as double)
                                                                      as volume,
        try_cast(coalesce({{ jstr('open_interest_fp') }}, {{ jstr('open_interest') }}) as double)
                                                                      as open_interest,
        {{ to_utc_naive('ingested_at') }}                          as ingested_at
    from unioned

),

deduplicated as (

    select
        *,
        row_number() over (
            partition by market_ticker, period_minutes, period_end_at
            order by ingested_at desc
        ) as row_rank
    from parsed

)

select * exclude (row_rank)
from deduplicated
where row_rank = 1
