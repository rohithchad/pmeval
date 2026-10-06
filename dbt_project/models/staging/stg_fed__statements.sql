{#- One row per FOMC statement with its publication timestamp in UTC.
    The page says "For release at 2:00 p.m. EDT"; we read the clock time from it. EDT/EST both
    map to America/New_York so daylight saving is handled by the zone database. When the
    line is missing we assume 14:00 and flag it with `release_time_is_assumed`. -#}

with parsed as (

    select
        {{ jstr('path') }}                                            as statement_path,
        'https://www.federalreserve.gov' || {{ jstr('path') }}        as statement_url,
        strptime({{ jstr('date_text') }}, '%B %d, %Y')::date          as statement_date,
        {{ jstr('release_line') }}                                    as release_line,
        regexp_extract({{ jstr('release_line') }}, '(\d{1,2}:\d{2} [ap])\.m\.', 1)
                                                                      as clock_text,
        {{ jstr('body_text') }}                                       as body_text,
        {{ to_utc_naive('ingested_at') }}                             as ingested_at
    from {{ source('bronze', 'fed_fomc_statements') }}

),

with_timestamp as (

    select
        *,
        coalesce(clock_text, '') = ''                                  as release_time_is_assumed,
        cast(
            (
                cast(statement_date as varchar) || ' '
                || cast(
                    coalesce(
                        try_strptime(nullif(clock_text, '') || 'm', '%I:%M %p')::time,
                        time '14:00:00'
                    ) as varchar
                )
            )::timestamp
                at time zone 'America/New_York'
            at time zone 'UTC'
            as timestamp
        ) as published_at
    from parsed

),

deduplicated as (

    select
        *,
        row_number() over (partition by statement_path order by ingested_at desc) as row_rank
    from with_timestamp

)

select * exclude (row_rank)
from deduplicated
where row_rank = 1
