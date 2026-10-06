{#- The resolved result of each event contract. outcome is 1 when the contract resolved YES and
    0 when it resolved NO. Unresolved contracts, and contracts with a non-binary ("scalar" or
    empty) result, have outcome NULL and are excluded from scoring. -#}

select
    events.event_id,
    markets.market_ticker,
    markets.strike_type,
    markets.floor_strike,
    markets.cap_strike,
    markets.result,
    case markets.result
        when 'yes' then 1
        when 'no' then 0
    end                                                               as outcome,
    markets.settled_at,
    markets.expiration_value
from {{ ref('dim_event') }} as events
inner join {{ ref('stg_kalshi__markets') }} as markets
    on markets.event_ticker = events.event_id
