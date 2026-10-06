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

## Tables written by Python

### `forecast.predictions`

One row per (event, contract, forecaster, version). Insert-only: a rerun never overwrites an
earlier prediction.

| Column | Description |
|--------|-------------|
| `event_id`, `market_ticker` | The release and the contract forecast. |
| `forecaster` | `base_rate`, `market`, and later the statistical model and the LLM. |
| `version` | Forecaster version; bump it when the logic changes. |
| `probability` | P(YES) in [0, 1] (enforced by a CHECK constraint and by validation). |
| `forecast_time` | The as-of time; every input is at or before it. |
| `release_at` | Scheduled release time. Live predictions must be made before it. |
| `made_at` | Wall-clock time the prediction was produced (UTC). |
| `mode` | `live` (made before the release; primary results) or `backtest` (made afterwards, as-of `forecast_time`). |
| `metadata_json` | Forecaster-specific details, such as training window or prompt hash. |

### `forecast.llm_calls`

One row per LLM call attempt (including failures), for audit and for reusing answers on reruns.

| Column | Description |
|--------|-------------|
| `call_id` | Unique id of the call. |
| `event_id`, `forecast_time` | The event asked about and its as-of time. |
| `prompt_version`, `model` | Prompt file version and model name used. |
| `dry_run` | True for stub calls that never reached the API. |
| `inputs_hash` | SHA-256 of the structured as-of inputs plus the prompt version. |
| `sample_index` | Which of the repeated samples this call is. |
| `requested_at` | Wall-clock time of the call (UTC). |
| `status` | `ok`, `invalid` (output failed validation), or `error` (call failed). |
| `prompt_text`, `raw_response` | The exact prompt and the model's raw answer. |
| `parsed_probabilities` | JSON map of contract to probability for `ok` calls. |
| `input_tokens`, `output_tokens` | Token usage reported by the API. |
| `error_message` | Reason for `invalid` or `error`. |

### `eval.scores`, `eval.score_summary`, `eval.calibration_bins`

Rebuilt from `forecast.predictions` and `marts.fct_outcome` by `python -m pmeval.eval.scoring`.

| Table | Grain and columns |
|-------|-------------------|
| `eval.scores` | One row per scored prediction: the prediction columns, `outcome` (0/1), `brier`, `log_loss`, `possibly_contaminated` (true for LLM backtests), `scored_at`. |
| `eval.score_summary` | Per forecaster, version, mode and series (plus `all`): `n_contracts`, `n_events`, `mean_brier`, `mean_log_loss`, `mean_probability`, `yes_rate`. |
| `eval.calibration_bins` | Per forecaster, version and mode: `bin_lower`, `bin_upper`, `count`, `mean_predicted`, `observed_frequency`. |

Scores: Brier is `(p - outcome)^2`; log loss is `-log` of the probability given to what happened
(probabilities clipped to [1e-6, 1 - 1e-6]). Lower is better for both.

### `ops.run_log`

Written by the Airflow DAGs through `pmeval.ops`; read by the dashboard's data-health panel.

| Column | Description |
|--------|-------------|
| `run_id` | Unique id. |
| `pipeline`, `step` | DAG name and the step (`dag_run`, `dbt_test`, `source_freshness`, or a failed task id). |
| `status` | `ok`, `warn` or `failed`. |
| `recorded_at` | When it was recorded (UTC). |
| `detail` | Error text, or JSON counts of dbt results (`{"pass": 70}`). |

### `eval.leaderboard`, `eval.comparisons`

Written by `python -m pmeval.eval.compare`. Leaderboard rows: forecaster, version, mode, metric,
`n_events`, `n_contracts`, `mean_score`, 95% cluster-bootstrap `ci_lower`/`ci_upper`,
`possibly_contaminated`, `small_sample`. Comparison rows: forecaster pair, metric, mean difference
(negative favours A) with interval, bootstrap and Diebold-Mariano p-values, `small_sample`.
See `docs/DECISIONS.md` D15 for assumptions.

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
