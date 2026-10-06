{#- One row per Kalshi market ticker.
    Bronze holds the same market many times (live snapshots, historical archive, reruns).
    We keep the most recently ingested record, which carries the final status and result. -#}

with unioned as (

    select payload, ingested_at, 'live' as tier from {{ source('bronze', 'kalshi_markets') }}
    union all
    select payload, ingested_at, 'live' as tier from {{ source('bronze', 'kalshi_live_markets') }}
    union all
    select payload, ingested_at, 'historical' as tier from {{ source('bronze', 'kalshi_historical_markets') }}

),

parsed as (

    select
        {{ jstr('ticker') }}                                         as market_ticker,
        {{ jstr('event_ticker') }}                                   as event_ticker,
        split_part({{ jstr('event_ticker') }}, '-', 1)               as series_ticker,
        {{ jstr('title') }}                                          as title,
        {{ jstr('subtitle') }}                                       as subtitle,
        {{ jstr('status') }}                                         as status,
        nullif({{ jstr('result') }}, '')                             as result,
        {{ jstr('strike_type') }}                                    as strike_type,
        try_cast({{ jstr('floor_strike') }} as double)               as floor_strike,
        try_cast({{ jstr('cap_strike') }} as double)                 as cap_strike,
        {{ jstr('expiration_value') }}                               as expiration_value,
        {{ to_utc_timestamp(jstr('open_time')) }}                    as open_at,
        {{ to_utc_timestamp(jstr('close_time')) }}                   as close_at,
        {{ to_utc_timestamp(jstr('expected_expiration_time')) }}     as expected_expiration_at,
        {{ to_utc_timestamp(jstr('occurrence_datetime')) }}          as occurrence_at,
        {{ to_utc_timestamp(jstr('settlement_ts')) }}                as settled_at,
        {{ dollars_to_probability(jstr('last_price_dollars')) }}     as last_price_probability,
        try_cast(coalesce({{ jstr('volume_fp') }}, {{ jstr('volume') }}) as double) as volume,
        tier,
        {{ to_utc_naive('ingested_at') }}                          as ingested_at
    from unioned

),

deduplicated as (

    select
        *,
        row_number() over (
            partition by market_ticker
            order by ingested_at desc
        ) as row_rank
    from parsed

)

select * exclude (row_rank)
from deduplicated
where row_rank = 1
