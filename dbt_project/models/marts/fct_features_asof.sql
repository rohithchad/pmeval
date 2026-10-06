{#- Point-in-time features for every contract: only information known at forecast_time.

    STRICT RULE: a value is included only when its available_at <= forecast_time. The latest
    figure that satisfies this is "last"; earlier ones fill "prev" and the trailing mean.
    Anything released after forecast_time (including revisions) is invisible here by construction.
    The custom test not_after_forecast_time re-checks every timestamp column below.

    Grain: event x contract. Features of the underlying series are the same for all contracts of
    an event; contract-specific columns are the strike and, for Fed decisions, the action size. -#}

with events as (

    select * from {{ ref('dim_event') }}

),

candidates as (

    select
        events.event_id,
        metrics.observation_date,
        metrics.headline_value,
        metrics.available_at,
        row_number() over (
            partition by events.event_id
            order by metrics.observation_date desc
        ) as recency_rank
    from events
    inner join {{ ref('int_fred__headline_metrics') }} as metrics
        on metrics.series_key = events.series_key
        and metrics.available_at <= events.forecast_time

),

event_features as (

    select
        event_id,
        max(case when recency_rank = 1 then headline_value end)       as last_value,
        max(case when recency_rank = 1 then observation_date end)     as last_observation_date,
        max(case when recency_rank = 1 then available_at end)         as last_value_available_at,
        max(case when recency_rank = 2 then headline_value end)       as prev_value,
        max(case when recency_rank = 2 then available_at end)         as prev_value_available_at,
        avg(case when recency_rank between 2 and 13 then headline_value end) as mean_prior_12,
        max(available_at)                                             as latest_feature_available_at
    from candidates
    where recency_rank <= 13
    group by event_id

),

latest_statement as (

    select
        events.event_id,
        statements.statement_path,
        statements.published_at,
        row_number() over (
            partition by events.event_id
            order by statements.published_at desc
        ) as recency_rank
    from events
    inner join {{ ref('stg_fed__statements') }} as statements
        on events.series_key = 'fed'
        and statements.published_at <= events.forecast_time

),

contracts as (

    select
        events.event_id,
        events.series_key,
        events.forecast_time,
        markets.market_ticker,
        markets.floor_strike                                          as strike,
        regexp_extract(markets.market_ticker, '-([^-]+)$', 1)         as contract_code
    from events
    inner join {{ ref('stg_kalshi__markets') }} as markets
        on markets.event_ticker = events.event_id

)

select
    contracts.event_id,
    contracts.market_ticker,
    contracts.series_key,
    contracts.forecast_time,
    contracts.strike,
    contracts.contract_code,
    -- Fed contracts: H25 = hike 25 bps, C25 = cut 25 bps, H0 = hold. Others are not Fed actions.
    case
        when contracts.series_key = 'fed' and contracts.contract_code = 'H0' then 0
        when contracts.series_key = 'fed' and contracts.contract_code like 'H%'
            then try_cast(regexp_extract(contracts.contract_code, '(\d+)', 1) as integer)
        when contracts.series_key = 'fed' and contracts.contract_code like 'C%'
            then -try_cast(regexp_extract(contracts.contract_code, '(\d+)', 1) as integer)
    end                                                               as contract_bps,
    event_features.last_value,
    event_features.last_observation_date,
    event_features.prev_value,
    event_features.last_value - event_features.prev_value             as change_1,
    event_features.mean_prior_12,
    contracts.strike - event_features.last_value                      as strike_minus_last_value,
    latest_statement.statement_path                                   as latest_statement_path,
    event_features.last_value_available_at,
    event_features.prev_value_available_at,
    latest_statement.published_at                                     as latest_statement_published_at,
    event_features.latest_feature_available_at
from contracts
left join event_features
    on contracts.event_id = event_features.event_id
left join latest_statement
    on contracts.event_id = latest_statement.event_id
    and latest_statement.recency_rank = 1
