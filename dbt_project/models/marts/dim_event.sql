{#- One row per scheduled release (a Kalshi event such as KXU3-26JUN).

    release_at is the OFFICIAL scheduled release time, found in this order:
      1. FRED release calendar (BLS/BEA series): release date from FRED + usual release time.
      2. Fed statement publication time (FOMC): the statement page's stated release time, or
         14:00 America/New_York on the meeting's last day when the statement is not yet out.
      3. Fallback: the market close time Kalshi sets. This is flagged in release_time_source
         so it can be reviewed.
    Series match on the ticker without its KX prefix: older Kalshi events are named
    FEDDECISION-23DEC while current ones are KXFEDDECISION-26JUN.
    Matching to the calendar uses the New York calendar date of the market close time.

    forecast_time = release_at minus forecast_hours_before (see macro forecast_time). -#}

with markets as (

    select * from {{ ref('stg_kalshi__markets') }}

),

registry as (

    select * from {{ ref('series_registry') }}

),

events as (

    select
        markets.event_ticker                                          as event_id,
        registry.kalshi_series_ticker,
        registry.series_key,
        registry.release_name,
        min(markets.open_at)                                          as event_open_at,
        min(markets.close_at)                                         as market_close_at,
        count(*)                                                      as market_count,
        bool_and(markets.result is not null)                          as is_resolved,
        min(markets.rules_primary)                                    as resolution_definition
    from markets
    inner join registry
        on markets.series_root = regexp_replace(registry.kalshi_series_ticker, '^KX', '')
    group by markets.event_ticker, registry.kalshi_series_ticker, registry.series_key,
        registry.release_name

),

with_close_date as (

    select
        *,
        cast((market_close_at at time zone 'UTC') at time zone 'America/New_York' as date)
            as close_date_et
    from events

),

fed_releases as (

    select
        meetings.meeting_end_date,
        coalesce(
            statements.published_at,
            cast(
                (cast(meetings.meeting_end_date as varchar) || ' 14:00:00')::timestamp
                    at time zone 'America/New_York'
                at time zone 'UTC'
                as timestamp
            )
        ) as release_at
    from {{ ref('stg_fed__meetings') }} as meetings
    left join {{ ref('stg_fed__statements') }} as statements
        on meetings.statement_path = statements.statement_path

),

joined as (

    select
        with_close_date.*,
        calendar.release_at                                           as calendar_release_at,
        fed_releases.release_at                                       as fed_release_at
    from with_close_date
    left join {{ ref('stg_fred__release_calendar') }} as calendar
        on with_close_date.series_key = calendar.series_key
        and with_close_date.close_date_et = calendar.release_date
    left join fed_releases
        on with_close_date.series_key = 'fed'
        and with_close_date.close_date_et = fed_releases.meeting_end_date

)

select
    event_id,
    kalshi_series_ticker,
    series_key,
    release_name,
    coalesce(calendar_release_at, fed_release_at, market_close_at)    as release_at,
    case
        when calendar_release_at is not null then 'fred_calendar'
        when fed_release_at is not null then 'fed_calendar'
        else 'kalshi_close_time'
    end                                                               as release_time_source,
    {{ forecast_time('coalesce(calendar_release_at, fed_release_at, market_close_at)') }}
                                                                      as forecast_time,
    event_open_at,
    market_close_at,
    market_count,
    is_resolved,
    resolution_definition
from joined
