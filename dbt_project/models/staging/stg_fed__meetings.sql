{#- One row per FOMC meeting (past and scheduled). The statement is released on the last day.
    The calendar page writes dates as text, for example "27-28", "30-1" (month rollover, month
    shown as "Apr/May") or "22 (notation vote)". We take the end day and, when two months are
    shown, the later month. -#}

with parsed as (

    select
        cast({{ jstr('year') }} as integer)                           as meeting_year,
        {{ jstr('month_text') }}                                      as month_text,
        {{ jstr('date_text') }}                                       as date_text,
        {{ jstr('statement_path') }}                                  as statement_path,
        split_part({{ jstr('month_text') }}, '/', -1)                 as end_month_text,
        cast(regexp_extract({{ jstr('date_text') }}, '^(?:\d+-)?(\d+)', 1) as integer)
                                                                      as end_day,
        {{ to_utc_naive('ingested_at') }}                             as ingested_at
    from {{ source('bronze', 'fed_fomc_meetings') }}

),

with_date as (

    select
        *,
        make_date(
            meeting_year,
            month(strptime(left(end_month_text, 3), '%b')),
            end_day
        ) as meeting_end_date,
        statement_path is not null as has_statement
    from parsed

),

deduplicated as (

    select
        *,
        row_number() over (
            partition by meeting_end_date
            order by ingested_at desc
        ) as row_rank
    from with_date

)

select * exclude (row_rank)
from deduplicated
where row_rank = 1
