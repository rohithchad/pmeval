# Design decisions and assumptions

Each entry records a decision or an assumption that should be re-checked.

## D1. Local Python version
The project targets Python 3.11 (Docker images use 3.11). The development machine only had
3.13, so local runs use a 3.13 virtualenv. `requires-python` is `>=3.11`. CI uses 3.11.

## D2. Raw layer stores JSON payloads
Raw Parquet rows hold the original JSON of each API record as text (`payload`), plus
`request_params` and `ingested_at`. This keeps raw data untouched and schema-stable.
Typing and field extraction happen in dbt staging. Partitioning is
`source=/dataset=/ingest_date=`. The raw root is a plain configurable path, so swapping in an
S3 location later means replacing the writer's filesystem calls only.

## D3. Retries and rate limiting
`HttpClient` retries 429 and 5xx responses and network errors with exponential backoff and
jitter, and enforces a minimum gap between requests. Kalshi documents that 429 responses carry
no `Retry-After` header, so plain backoff is used.

## D4. Kalshi API
Source: docs.kalshi.com (llms.txt index, OpenAPI pages for markets, candlesticks, trades and the
historical endpoints), read 2026-10-06.
- Base URL `https://external-api.kalshi.com/trade-api/v2` (docs call it recommended; the older
  `api.elections.kalshi.com` host also works). Public market data needs no authentication.
- Prices are fixed-point dollar strings (`yes_price_dollars`, `last_price_dollars`, `*_fp` counts).
  Dollars on a $1 binary contract equal probability, so no cents conversion is needed.
- Live candlesticks use `period_interval` 1, 60 or 1440 and nested `*_dollars` fields. A candle
  with no trades has an empty `price` object (`{}`); staging must treat it as null.
- The docs state no cap on candles per request. As a precaution the client requests 30-day windows.
- Rate limits: token buckets per tier; 429 has no `Retry-After`. We stay near 5 requests/second.
- Series tickers in `SERIES_REGISTRY` were confirmed to exist on 2026-10-06:
  `KXCPIYOY`, `KXPAYROLLS`, `KXU3`, `KXGDP`, `KXFEDDECISION`. Kalshi has many other CPI/jobs
  series (for example `KXCPI`, `KXECONSTATCPI`, `KXECONSTATU3`, `KXFED`). Which of them
  best match each release is an open assumption to review after inspecting settled markets.

## D5. Historical tier and backfill
- `GET /historical/cutoff` returned `market_settled_ts`, `trades_created_ts`, `orders_updated_ts`
  and `market_positions_last_updated_ts` on 2026-10-06. We route by `market_settled_ts`: markets
  settled before it come from `/historical/markets`; settled markets at or after it come from the
  live `/markets?status=settled`.
- Historical candlesticks differ from live ones: fields are `open`/`close` (not `open_dollars`),
  `volume`/`open_interest` (not `*_fp`). Staging must handle both shapes.
- Settled markets in both tiers show `status = finalized` in the historical tier; live-tier
  statuses seen include `active`. Staging maps results from `result` (`yes`/`no`).
- Assumption: trades for historical markets come from `/historical/trades?ticker=`, and trades for
  live-tier markets from `/markets/trades`. The docs' cutoff text mentions `/historical/fills` for
  `trades_created_ts`, which is the member-fills endpoint, not public trades. Re-check if trade
  counts look short.
- Backfill is resumable through a JSON state file of completed tickers
  (`<raw_dir>/_state/kalshi_backfill.json`). A crash mid-market re-writes that market's rows on the
  next run; duplicates are removed in dbt staging.

## D6. FRED first-release data
Source: fred.stlouisfed.org/docs/api (series/observations, errors page), read 2026-10-06.
- `output_type=4` with realtime window 1776-07-04..9999-12-31 returns each observation's initial
  release only. `realtime_start` is the date that first value was published; this is the as-of
  availability timestamp used for point-in-time features. Rate limit is 120 requests/minute.
- Daily series exceed the 2000 vintage-date limit of that query (HTTP 400, observed for
  `DFEDTARU`). The Fed target rate is a policy decision and is not revised, so it is fetched with
  the default query and tagged `vintage_policy = unrevised`. Its availability time is the
  observation date.
- FRED publishes dates, not times. For as-of logic a first-release date is treated as available at
  the start of that UTC day plus the release time from the calendar (see release calendar).
  Conservative handling: a value is only used if its release timestamp is at or before
  `forecast_time`.
- Series mapping: CPI `CPIAUCSL` (index level; YoY/MoM derived in dbt), payrolls `PAYEMS` (level;
  monthly change derived in dbt), unemployment `UNRATE`, GDP `GDPC1` (real GDP level; growth derived
  in dbt), fed funds `DFEDTARU` (upper bound of target range). Whether derived growth rates match
  how each Kalshi contract defines its number is an open assumption to check against contract rules.

## D7. Release calendar and Fed statements
Checkpoint review (rule 9, before commit 9): no source needs registration or has restrictive terms.
- Release dates come from the FRED release-dates endpoint with the existing FRED key
  (`include_release_dates_with_no_data=true` lists scheduled future dates). Releases: CPI = 10,
  Employment Situation = 50, GDP = 53. FRED gives dates only; release times are an assumption:
  08:30 America/New_York for BLS and BEA releases, 14:00 for FOMC statements. Check these against
  Kalshi `close_time` (about one minute before the data release) in a dbt test later.
- GDP release 53 lists advance, second and third estimates. `dim_event` must pick the one that
  matches each Kalshi event; this is open.
- FRED's FOMC release (101) lists every calendar day, so it is not used. FOMC meeting dates and
  statements come from the Federal Reserve Board calendar page
  (federalreserve.gov/monetarypolicy/fomccalendars.htm), which covers 2021 to 2027. Board content is
  public domain with a request to cite the Board; there is no robots.txt. We fetch 1 request per
  second with an identifying User-Agent, and each statement once.
- Statement pages state their release time ("For release at 2:00 p.m. EDT"); it is stored as text
  and parsed in staging.
- Meeting dates are stored as shown ("27-28", "17-18*"); the statement is released on the last day.
  Meetings before 2021 are not ingested because Kalshi data does not reach back that far
  (to be confirmed with real run results).

## D8. Bronze layer is views over raw Parquet
`pmeval.warehouse.bronze` creates `bronze.<source>_<dataset>` views with
`CREATE OR REPLACE VIEW ... read_parquet(..., hive_partitioning, union_by_name)`. No data is copied,
reruns are safe, and new raw files appear immediately. Datasets with no files yet get empty
placeholder views (standard raw columns) so dbt models compile before every source has been
ingested. The expected dataset list lives in `EXPECTED_DATASETS`.

## D9. dbt setup
- Schemas are exactly `staging` and `marts` (custom `generate_schema_name`). Run dbt from
  `dbt_project/` with `--profiles-dir .`; the warehouse path comes from `PMEVAL_WAREHOUSE_PATH`.
- All timestamps in staging and marts are naive UTC `TIMESTAMP`.
- Staging reads fields from raw JSON with `json_extract_string`; live and historical candlesticks
  use different field names, so both are read with `coalesce`.
- Market-implied probability is the dollar price of a $1 YES contract; no conversion factor needed.
- Local setup quirk on macOS with a python.org Python: installing `dbt-core` 1.11+ downloads a
  wheel at build time and fails with `CERTIFICATE_VERIFY_FAILED`. Fix:
  `export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")` before `pip install`.
- dbt tests run against a synthetic fixture warehouse (`tests/fixture_warehouse.py`). Its numbers
  are made up for testing and are labelled synthetic; only field shapes copy real responses.

## D10. Events and forecast_time
- Event grain is the Kalshi event ticker (for example `KXU3-26JUN`): one release, many strike
  contracts. `event_id` is that ticker.
- `release_at` priority: FRED calendar (date + assumed 08:30 ET) > Fed statement time (14:00 ET
  fallback) > Kalshi market close time. Events are matched to the calendar by the New York date of
  the market close time. `release_time_source` records which rule applied so fallbacks are visible.
  On real data, jobs-report markets close at 12:29 UTC (8:29 ET), one minute before the assumed
  08:30 ET release, which supports the assumed time for BLS releases (BEA/GDP not yet checked).
- `forecast_time = release_at - forecast_hours_before` (default 24h), defined once in the dbt macro
  `forecast_time`. dbt reads `PMEVAL_FORECAST_HOURS_BEFORE`, the same variable Python uses.
- `resolution_definition` is the primary rule text of one representative contract in the event;
  per-contract strikes live on the contract rows.
- The dbt seed `series_registry.csv` mirrors `SERIES_REGISTRY` in Python; a test keeps them equal.

## D11. Market forecast and outcome
- Grain is event x contract (one strike of one release). The market's forecast is the YES price at
  `forecast_time`: last trade at or before it, else the last candle that ended at or before it.
  Contracts with no price by then have a NULL probability and are dropped from scoring.
- Contracts that opened after, or closed before, `forecast_time` carry
  `was_open_at_forecast_time = false`.
- `outcome` is 1 for result `yes`, 0 for `no`, NULL for anything else.
- Ran on real data on 2026-10-06 (3 settled KXU3 contracts) and produced plausible rows; the three
  outcomes there are all 0 with market prices of 0.02.

## D12. Point-in-time features
- `int_fred__headline_metrics` converts first-release levels into the figure each Kalshi contract
  asks about: unemployment rate (level), payroll change (persons, PAYEMS x 1000), CPI year-over-year
  percent change, annualized quarter-over-quarter real GDP growth, Fed target upper bound (level).
  Transform and look-back are columns of the `series_registry` seed. Prior values are found by date,
  so a missing release (the skipped October 2025 CPI) cannot misalign comparisons.
- Known approximation: official payroll and GDP growth figures use the prior period as revised in
  the same report; we use the prior period's first release. CPI uses the seasonally adjusted index
  `CPIAUCSL`, while the Kalshi CPI contract reads BLS's published one-decimal year-over-year
  figure, which is based on the unadjusted index. Features are therefore close to, not identical
  with, the published numbers. They are only used as model inputs; outcomes come from Kalshi.
- Availability of a first-release value is the calendar date plus the series' usual release time.
  A value is used only if `available_at <= forecast_time`. Never-revised Fed target values are
  treated as known from 00:00 UTC the day after the observation date.
- `fct_features_asof` carries every timestamp used, and the custom generic test
  `not_after_forecast_time` fails when any of them is later than `forecast_time`. A pytest case
  proves the test fails when a timestamp is deliberately made too late.
- Kalshi renamed its series: older events are `FEDDECISION-23DEC`, newer ones `KXFEDDECISION-26JUN`.
  Matching strips the `KX` prefix. Historical data depth seen on 2026-10-06: Fed decisions from May
  2023, CPI from December 2022, payrolls from April 2023, unemployment from August 2021, GDP from
  July 2021 (roughly 190 events in total, before any filtering), so sample sizes are small.
- Fed contracts encode the action in the ticker suffix (`H0` hold, `H25` hike 25 bps, `C25` cut 25
  bps, `H26`/`C26` more than 25 bps). `contract_bps` is the signed number.
- After changing seed columns, run `dbt build --full-refresh`.

## D13. Forecasters and predictions
- `forecast.predictions` is insert-only (`ON CONFLICT DO NOTHING`), keyed by event, contract,
  forecaster and version. A rerun keeps the first prediction, so a live forecast cannot be replaced
  after the outcome is known. Live rows must have `made_at < release_at`; backtest rows carry
  `mode = 'backtest'`.
- Base rate: smoothed share of YES among earlier settled contracts of the same series, using only
  contracts with `settled_at <= forecast_time`.
- Market forecaster: the price from `fct_market_forecast`; declines when there is none.
- Logistic regression: one model per series, trained walk-forward on contracts settled by the
  target's forecast_time, inputs standardised, L2 with C = 1. Inputs are the strike's distance from
  the last known figure and the latest change (threshold series) or hold/hike/cut indicators and the
  latest change (Fed). It declines with fewer than 30 earlier contracts or one outcome class. Model
  version, features, `n_train` and training window are stored in `metadata_json`. Contracts of one
  event are correlated, so effective sample size is smaller than the contract count.
- Forecasts are per contract, so each event contributes several rows. Evaluation resamples whole
  events (cluster bootstrap) for that reason.
- `check_no_lookahead` re-checks every timestamp feature at prediction time and raises
  `LeakageError`.

## D14. LLM forecaster
- Default model `claude-opus-5-5` (configurable via `PMEVAL_LLM_MODEL`; the cheaper
  `claude-sonnet-5-5` is an option for large backtests). Billed per token on the Anthropic API,
  separately from any Claude subscription. Opus 5.5 always thinks and rejects `temperature`, so
  the averaged samples differ only through the model's own variation. Effort defaults to `medium`.
- One call set per event covers all of its contracts (asks for one probability per contract),
  repeated `PMEVAL_LLM_SAMPLES` times (default 3) and averaged. Cost scales with events, not
  contracts. `--llm-max-events N` limits spend.
- The model is not shown the market price. If it were, it would mostly echo the market and the
  comparison would lose meaning.
- Structured output uses `output_config.format` with a JSON schema; numeric bounds and the exact
  contract set are re-validated with `jsonschema` afterwards. Invalid answers are logged and skipped.
- Server-side refusal fallbacks are deliberately NOT enabled: a silent switch to another model would
  make "which model produced this forecast" untrue. Refusals are logged as errors and the event gets
  no LLM forecast.
- Every call is logged to `forecast.llm_calls` (prompt version, model, inputs hash, prompt, raw
  response, parsed probabilities, tokens, timestamp). Reruns reuse a logged `ok` response for the same
  (event, prompt version, model, inputs hash, sample), so repeated runs cost nothing.
- Leakage: inputs carry explicit timestamps; `assert_inputs_as_of` raises `LeakageError` before a call
  if any is after `forecast_time`. The prompt also tells the model to ignore later knowledge, which is
  advisory only: a model trained after the event may still remember it.
- Contamination: LLM predictions made for already-resolved events (`mode = backtest`) are possibly
  contaminated by training data and must be labeled so wherever shown. Only predictions made live
  before a release are clean evidence.
- Dry run: stub client, forecaster name `llm_dry_run`, never mixed with `llm`.
- dbt views embed the database file name as their catalog. Do not rename `warehouse.duckdb` after a
  `dbt build` without rebuilding.

## D15. Statistical comparison: assumptions and limits
What is computed (`pmeval.eval.compare`, tables `eval.leaderboard` and `eval.comparisons`):
- Per forecaster: mean Brier and mean log loss with a 95% percentile interval from a **cluster
  bootstrap** (10,000 replicates, fixed seed, so results are reproducible).
- Per pair: mean loss difference `d = loss(A) - loss(B)` (negative means A is better) with a cluster
  bootstrap interval and p-value, and a **Diebold-Mariano test** on per-event mean differences.

Why clusters: contracts of one release resolve from one number and move together, so they are not
independent. Resampling single contracts would understate uncertainty (a test shows the cluster
interval is far wider when contracts are perfectly correlated). The unit of resampling is the event.

Assumptions:
1. Events are exchangeable enough to resample with replacement. Economic regimes shift, so a
   forecaster that wins in one period may not in another; the bootstrap cannot detect that.
2. Diebold-Mariano is applied at a one-step horizon with each event's mean differential as one
   observation, ignoring serial correlation between consecutive releases of the same series. If
   differentials are positively autocorrelated, p-values are too optimistic. The
   Harvey-Leybourne-Newbold small-sample correction and a t distribution with n-1 degrees of
   freedom are used.
3. Forecasters are compared on **common support**: only contracts every compared forecaster
   predicted. The logistic model declines early events and the market declines contracts without a
   price, so this can shrink the sample a lot. Counts are always reported.
4. Only the newest version of each forecaster is used; `llm_dry_run` (the stub) is excluded.
5. Log loss clips probabilities to [1e-6, 1 - 1e-6]. A forecaster that says 0 or 1 and is wrong gets
   a large, finite penalty.
6. The p-values are not adjusted for the many pairwise comparisons, so with several forecasters,
   two metrics and two modes, some small p-values will arise by chance.

Small-sample limits:
- Real history is on the order of 100 to 200 events across all series, fewer after common-support
  filtering, and far fewer for live predictions (there are only a few releases per month). Any row
  with fewer than 30 events is flagged `small_sample`; treat its intervals and p-values as rough.
- With few events, percentile bootstrap intervals are typically too narrow, and a non-significant
  result means "cannot tell", not "equal".
- Live results will take months to accumulate. Backtests are available immediately, but LLM backtests
  are possibly contaminated by training data and must be labeled so (`possibly_contaminated`).
- The comparison covers forecast quality only. It says nothing about whether any forecaster could
  make money after fees and spreads, and nothing here is financial advice.
