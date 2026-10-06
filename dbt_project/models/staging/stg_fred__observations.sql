{#- One row per series and observation period, as FIRST released.
    `first_released_on` is the date this value first appeared (FRED realtime_start). It is the
    only date on which the value may be used as an input. FRED encodes missing values as ".".
    Never-revised series (vintage_policy = 'unrevised', for example the Fed target rate) have
    no real first-release date, so the observation date itself is the availability date. -#}

with parsed as (

    select
        {{ jstr('series_id') }}                                       as fred_series_id,
        {{ jstr('vintage_policy') }}                                  as vintage_policy,
        cast({{ jstr('date') }} as date)                              as observation_date,
        try_cast(nullif({{ jstr('value') }}, '.') as double)          as value,
        cast({{ jstr('realtime_start') }} as date)                    as realtime_start_date,
        cast({{ jstr('realtime_end') }} as date)                      as realtime_end_date,
        {{ to_utc_naive('ingested_at') }}                             as ingested_at
    from {{ source('bronze', 'fred_observations') }}

),

with_availability as (

    select
        *,
        case
            when vintage_policy = 'unrevised' then observation_date
            else realtime_start_date
        end as first_released_on
    from parsed

),

deduplicated as (

    select
        *,
        row_number() over (
            partition by fred_series_id, observation_date
            order by ingested_at desc
        ) as row_rank
    from with_availability

)

select * exclude (row_rank)
from deduplicated
where row_rank = 1
