# Live vs mock verification tracker

Updated as components are built. "Live" means run in a development session against the real
service and the output was observed. "Mocks only" means unit tests with recorded fixtures.

| Component | Status |
|-----------|--------|
| HTTP client (retries, rate limit) | Mocks only |
| Raw Parquet writer | Mocks only (local filesystem; no S3) |
| Kalshi live client: series, events, markets, candlesticks, trades | Live read on 2026-10-06 (small requests, public data); fixtures recorded from it |
| `kalshi_live` ingestion CLI end to end | Not yet run (components used by it were run live) |
| Bronze loader over real raw files | Live on a scratch copy of real raw data (views created); unit-tested |
| Kalshi historical client: cutoff, historical markets, candlesticks, trades | Live read on 2026-10-06 (2 markets of KXU3) |
| Backfill routing across both tiers + resume state | Live on a small sample (2 historical + 1 live-settled market). Full-series backfill not yet run |
| FRED client: first-release observations (CPIAUCSL, PAYEMS, UNRATE, GDPC1) and unrevised DFEDTARU | Live on 2026-10-06 with the user's FRED key; fixtures recorded from it |
| Release calendar (FRED release dates for CPI, jobs, GDP) | Live on 2026-10-06 to a scratch directory |
| Fed statement + meeting ingestion | Live on 2026-10-06 (47 statements, 57 meetings) to a scratch directory |
| dbt staging models on real raw data (Kalshi sample, FRED, calendar, Fed) | Run on 2026-10-06 against a scratch warehouse built from real ingested data; unit-tested on synthetic fixtures |
| dbt marts (dim_event, fct_*), schema tests and source freshness | `dbt build` (70 tests) passed on 2026-10-06 against a scratch warehouse built from real data (3 settled KXU3 contracts, real FRED, calendar, Fed). Source freshness ran: FRED sources PASS; `kalshi_markets` ERROR STALE because the daily live ingest was never run into that scratch warehouse. Full-history Kalshi data is not yet loaded |
