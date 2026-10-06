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
