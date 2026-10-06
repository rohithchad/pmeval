{#- The MARKET's forecast: for each event and contract, the YES price at forecast_time.

    "Last price at or before forecast_time": nothing after forecast_time is used.
      1. Preferred: the last executed trade with traded_at <= forecast_time.
      2. Fallback: the last candlestick whose period ENDED at or before forecast_time, using
         its closing trade price, or the previous trade price when nothing traded in the period.
    A candle that ends after forecast_time is excluded even if it started earlier, because part of
    it happened after the forecast.
    A $1 binary contract priced at p dollars implies probability p, so the price is the forecast.
    market_probability is NULL when the contract had no price yet; evaluation drops those rows. -#}

with contracts as (

    select
        events.event_id,
        events.forecast_time,
        markets.market_ticker,
        markets.open_at,
        markets.close_at
    from {{ ref('dim_event') }} as events
    inner join {{ ref('stg_kalshi__markets') }} as markets
        on markets.event_ticker = events.event_id

),

last_trade as (

    select
        contracts.event_id,
        contracts.market_ticker,
        trades.traded_at                                              as price_observed_at,
        trades.yes_probability                                        as probability
    from contracts
    inner join {{ ref('stg_kalshi__trades') }} as trades
        on trades.market_ticker = contracts.market_ticker
        and trades.traded_at <= contracts.forecast_time
        and trades.yes_probability is not null
    qualify row_number() over (
        partition by contracts.event_id, contracts.market_ticker
        order by trades.traded_at desc, trades.trade_id desc
    ) = 1

),

last_candle as (

    select
        contracts.event_id,
        contracts.market_ticker,
        candles.period_end_at                                         as price_observed_at,
        coalesce(candles.trade_close_probability, candles.previous_trade_probability)
                                                                      as probability
    from contracts
    inner join {{ ref('stg_kalshi__candlesticks') }} as candles
        on candles.market_ticker = contracts.market_ticker
        and candles.period_end_at <= contracts.forecast_time
        and coalesce(candles.trade_close_probability, candles.previous_trade_probability)
            is not null
    qualify row_number() over (
        partition by contracts.event_id, contracts.market_ticker
        order by candles.period_end_at desc, candles.period_minutes asc
    ) = 1

)

select
    contracts.event_id,
    contracts.market_ticker,
    contracts.forecast_time,
    coalesce(last_trade.probability, last_candle.probability)          as market_probability,
    case
        when last_trade.probability is not null then 'trade'
        when last_candle.probability is not null then 'candlestick'
    end                                                               as price_source,
    coalesce(last_trade.price_observed_at, last_candle.price_observed_at)
                                                                      as price_observed_at,
    contracts.open_at <= contracts.forecast_time
        and contracts.close_at > contracts.forecast_time              as was_open_at_forecast_time
from contracts
left join last_trade
    on contracts.event_id = last_trade.event_id
    and contracts.market_ticker = last_trade.market_ticker
left join last_candle
    on contracts.event_id = last_candle.event_id
    and contracts.market_ticker = last_candle.market_ticker
