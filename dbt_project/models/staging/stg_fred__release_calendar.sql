{#- One row per scheduled release. FRED gives the date only; the usual local release time is
    stored next to it by the ingestion code, and combined here into a UTC timestamp.
    Converting through the America/New_York zone handles daylight saving time correctly. -#}

with parsed as (

    select
        {{ jstr('series_key') }}                                      as series_key,
        cast({{ jstr('release_id') }} as integer)                     as fred_release_id,
        {{ jstr('release_name') }}                                    as release_name,
        cast({{ jstr('release_date') }} as date)                      as release_date,
        {{ jstr('release_time_local') }}                              as release_time_local,
        {{ jstr('timezone') }}                                        as timezone,
        {{ to_utc_naive('ingested_at') }}                             as ingested_at
    from {{ source('bronze', 'fred_release_calendar') }}

),

with_timestamp as (

    select
        *,
        cast(
            (cast(release_date as varchar) || ' ' || release_time_local || ':00')::timestamp
                at time zone timezone
            at time zone 'UTC'
            as timestamp
        ) as release_at
    from parsed

),

deduplicated as (

    select
        *,
        row_number() over (
            partition by series_key, release_date
            order by ingested_at desc
        ) as row_rank
    from with_timestamp

)

select * exclude (row_rank)
from deduplicated
where row_rank = 1
