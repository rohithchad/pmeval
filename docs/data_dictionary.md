# Data dictionary

Layers: raw Parquet, bronze (views over raw), silver (dbt `staging` schema), gold (dbt `marts`
schema). All timestamps are naive UTC. Probabilities are in [0, 1].

## Raw and bronze

Every raw Parquet row has:

| Column | Description |
|--------|-------------|
| `payload` | The original API record as JSON text. |
| `request_params` | JSON of the non-secret request parameters. |
| `ingested_at` | When the pipeline wrote the row (UTC). |
| `source`, `dataset`, `ingest_date` | Hive partition columns from the file path. |

Bronze views are named `bronze.<source>_<dataset>`, for example `bronze.kalshi_markets`.

## Silver and gold (generated from dbt YAML)

Regenerate with `python -m pmeval.warehouse.data_dictionary`. A test fails if this section is stale.

<!-- dbt:start -->

### `dim_event` (model)

One row per scheduled release (Kalshi event). Holds the release time and the single forecast_time at which every forecaster must make its prediction.

| Column | Description | Tests |
|--------|-------------|-------|
| `event_id` | Kalshi event ticker, for example KXU3-26JUN. | unique, not_null |
| `series_key` | Internal series name from the registry. | not_null, relationships |
| `release_at` | Scheduled release time (UTC). | not_null |
| `release_time_source` | Where release_at came from; kalshi_close_time is a flagged fallback. | accepted_values |
| `forecast_time` | release_at minus forecast_hours_before. No input may postdate it. | not_null |
| `resolution_definition` | Rule text of one representative contract of the event. |  |
| `is_resolved` | True when every contract of the event has a result. |  |

### `fct_features_asof` (model)

Point-in-time features per event contract. Every timestamp column must be at or before forecast_time; the custom test not_after_forecast_time enforces it.

Model tests: unique_combination

| Column | Description | Tests |
|--------|-------------|-------|
| `event_id` |  | not_null, relationships |
| `forecast_time` | The moment the forecast is made (UTC). | not_null |
| `strike` | Contract threshold, for example 4.2 for "unemployment above 4.2%". |  |
| `contract_bps` | Fed contracts only; signed basis points (H25 = +25, C25 = -25, H0 = 0). |  |
| `last_value` | Latest headline figure known at forecast_time. |  |
| `prev_value` | The figure before last_value. |  |
| `change_1` | last_value minus prev_value. |  |
| `mean_prior_12` | Mean of up to 12 figures before last_value. |  |
| `strike_minus_last_value` | Distance between the threshold and the latest known figure. |  |
| `last_value_available_at` | When last_value became known (UTC). | not_after_forecast_time |
| `prev_value_available_at` |  | not_after_forecast_time |
| `latest_statement_published_at` | Publication time of the latest FOMC statement known at forecast_time. | not_after_forecast_time |
| `latest_feature_available_at` | Latest availability time over all figures used. | not_after_forecast_time |

### `fct_market_forecast` (model)

The market's forecast per contract: the YES price at forecast_time (last trade, else last candle ending at or before it).

Model tests: unique_combination

| Column | Description | Tests |
|--------|-------------|-------|
| `event_id` |  | not_null, relationships |
| `market_probability` | Market-implied probability of YES at forecast_time; null if no price yet. | accepted_range |
| `price_source` |  | accepted_values |
| `price_observed_at` | Timestamp of the price used. Never later than forecast_time. | not_after_forecast_time |
| `was_open_at_forecast_time` | Whether the contract was trading at forecast_time. |  |

### `fct_outcome` (model)

Resolved result per contract. outcome is 1 for YES, 0 for NO, null otherwise.

Model tests: unique_combination

| Column | Description | Tests |
|--------|-------------|-------|
| `event_id` |  | not_null, relationships |
| `outcome` |  | accepted_values |

### `int_fred__headline_metrics` (model)

The headline figure each contract asks about (for example payroll change or CPI year over year), computed from first-release values, with the time it became knowable.

Model tests: unique_combination

| Column | Description | Tests |
|--------|-------------|-------|
| `available_at` | When the figure could first be known (UTC). | not_null |

### `series_registry` (seed)

Maps each economic series to its FRED id, Kalshi series ticker, usual release time and the transform that turns FRED levels into the headline figure. Mirrors SERIES_REGISTRY in Python.

### `stg_fed__meetings` (model)

One row per FOMC meeting, scheduled or past.

| Column | Description | Tests |
|--------|-------------|-------|
| `meeting_end_date` | Last day of the meeting, which is the statement date. | unique, not_null |

### `stg_fed__statements` (model)

One row per FOMC statement with its publication time (UTC).

| Column | Description | Tests |
|--------|-------------|-------|
| `statement_path` |  | unique, not_null |
| `published_at` |  | not_null |
| `release_time_is_assumed` | True when the page gave no release time and 14:00 New York time was assumed. |  |

### `stg_fred__observations` (model)

One row per FRED series and observation period at its FIRST release. Later revisions are never stored here.

Model tests: unique_combination

| Column | Description | Tests |
|--------|-------------|-------|
| `fred_series_id` |  | not_null |
| `observation_date` | First day of the period the value describes. | not_null |
| `first_released_on` | Date the value first became available. Never earlier than it was known. | not_null |
| `vintage_policy` | first_release, or unrevised for series that are never revised. | accepted_values |

### `stg_fred__release_calendar` (model)

Scheduled releases with a UTC timestamp built from the date and usual local time.

Model tests: unique_combination

| Column | Description | Tests |
|--------|-------------|-------|
| `release_at` | Scheduled release time (UTC). The local clock time is an assumption. | not_null |

### `stg_kalshi__candlesticks` (model)

One row per market and candle period. Prices are probabilities. period_end_at is when the candle closed; nothing in a candle was known before then.

Model tests: unique_combination

| Column | Description | Tests |
|--------|-------------|-------|
| `market_ticker` |  | not_null |
| `period_end_at` | End of the candle period (UTC). | not_null |
| `trade_close_probability` | Closing trade price in the period; null when nothing traded. | accepted_range |
| `previous_trade_probability` | Price of the last trade before the period. | accepted_range |
| `yes_bid_close_probability` |  | accepted_range |
| `yes_ask_close_probability` |  | accepted_range |

### `stg_kalshi__markets` (model)

One row per Kalshi market (a single strike of a release). The latest ingested record wins, so status and result are final once the market settles. Timestamps are naive UTC.

| Column | Description | Tests |
|--------|-------------|-------|
| `market_ticker` | Unique market id, for example KXU3-26JUN-T4.2. | unique, not_null |
| `event_ticker` | The release this market belongs to. | not_null |
| `series_ticker` | Series part of the event ticker as written (may lack the KX prefix). |  |
| `series_root` | Series ticker without the KX prefix, used to match legacy and current names. | not_null |
| `result` | Resolution, yes or no (null before settlement). | accepted_values |
| `last_price_probability` | Last traded YES price in dollars, which equals implied probability. | accepted_range |
| `open_at` | When trading opened (UTC). |  |
| `close_at` | When trading closed (UTC). About one minute before the data release. |  |
| `settled_at` | When the market settled (UTC). |  |

### `stg_kalshi__trades` (model)

One row per executed trade (live and historical tiers, de-duplicated).

| Column | Description | Tests |
|--------|-------------|-------|
| `trade_id` |  | unique, not_null |
| `market_ticker` |  | not_null |
| `traded_at` | Execution time (UTC). | not_null |
| `yes_probability` | YES contract price in dollars, equal to implied probability. | accepted_range |

<!-- dbt:end -->
