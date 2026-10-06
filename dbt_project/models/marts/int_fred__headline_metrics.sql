{#- The number each Kalshi contract is actually about, built from FIRST-RELEASE values only.

    Kalshi asks about a derived headline figure, not the raw FRED level:
      unemployment, fed   level as published
      payrolls            monthly job change, persons (PAYEMS is in thousands)
      cpi                 year-over-year % change of the index
      gdp                 annualized quarter-over-quarter % growth of real GDP
    The transform and its look-back come from the series_registry seed. The prior value is found
    by date (for example 12 months earlier), not by row position, so a missing release such as the
    skipped October 2025 CPI cannot shift the comparison.

    available_at is when the figure could first be known: the later of the two first-release times
    involved. Release times are the registry's usual local time (America/New_York). Never-revised
    series (Fed target) are treated as known from the end of the observation day.

    Approximation: the prior value is also a first release, whereas an agency headline figure uses
    the prior month as revised in the same report. See docs/DECISIONS.md. -#}

with observations as (

    select
        registry.series_key,
        registry.headline_transform,
        registry.transform_lag_months,
        observations.observation_date,
        observations.value,
        case
            when registry.is_revised then
                cast(
                    (
                        cast(observations.first_released_on as varchar) || ' '
                        || registry.release_time_et || ':00'
                    )::timestamp
                        at time zone 'America/New_York'
                    at time zone 'UTC'
                    as timestamp
                )
            else cast(observations.observation_date + interval 1 day as timestamp)
        end as available_at
    from {{ ref('stg_fred__observations') }} as observations
    inner join {{ ref('series_registry') }} as registry
        on observations.fred_series_id = registry.fred_series_id
    where observations.value is not null

),

with_prior as (

    select
        current_obs.series_key,
        current_obs.headline_transform,
        current_obs.observation_date,
        current_obs.value,
        current_obs.available_at,
        prior_obs.value                                               as prior_value,
        prior_obs.available_at                                        as prior_available_at
    from observations as current_obs
    left join observations as prior_obs
        on current_obs.series_key = prior_obs.series_key
        and prior_obs.observation_date
            = current_obs.observation_date - to_months(current_obs.transform_lag_months::integer)

)

select
    series_key,
    observation_date,
    case headline_transform
        when 'level' then value
        when 'change_x1000' then (value - prior_value) * 1000
        when 'yoy_pct' then (value / prior_value - 1) * 100
        when 'annualized_qoq_pct' then (power(value / prior_value, 4) - 1) * 100
    end                                                               as headline_value,
    greatest(available_at, coalesce(prior_available_at, available_at)) as available_at
from with_prior
where headline_transform = 'level' or prior_value is not null
