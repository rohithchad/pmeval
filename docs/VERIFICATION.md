# Live vs mock verification tracker

Updated as components are built. "Live" means run in a development session against the real
service and the output was observed. "Mocks only" means unit tests with recorded fixtures.

| Component | Status |
|-----------|--------|
| HTTP client (retries, rate limit) | Mocks only |
| Raw Parquet writer | Mocks only (local filesystem; no S3) |
| Kalshi live client: series, events, markets, candlesticks, trades | Live read on 2026-10-06 (small requests, public data); fixtures recorded from it |
| `kalshi_live` ingestion CLI end to end | Live on 2026-10-06 for KXU3 with `--status open` (28 markets, 24k candles, scratch dir). Real HTTP 429s occurred and were retried successfully |
| Bronze loader over real raw files | Live on a scratch copy of real raw data (views created); unit-tested |
| Kalshi historical client: cutoff, historical markets, candlesticks, trades | Live read on 2026-10-06 (2 markets of KXU3) |
| Backfill routing across both tiers + resume state | Live on a small sample (2 historical + 1 live-settled market). Full-series backfill not yet run |
| FRED client: first-release observations (CPIAUCSL, PAYEMS, UNRATE, GDPC1) and unrevised DFEDTARU | Live on 2026-10-06 with the user's FRED key; fixtures recorded from it |
| Release calendar (FRED release dates for CPI, jobs, GDP) | Live on 2026-10-06 to a scratch directory |
| Fed statement + meeting ingestion | Live on 2026-10-06 (47 statements, 57 meetings) to a scratch directory |
| dbt staging models on real raw data (Kalshi sample, FRED, calendar, Fed) | Run on 2026-10-06 against a scratch warehouse built from real ingested data; unit-tested on synthetic fixtures |
| dbt marts (dim_event, fct_*), schema tests and source freshness | `dbt build` (70 tests) passed on 2026-10-06 against a scratch warehouse built from real data (3 settled KXU3 contracts, real FRED, calendar, Fed). Source freshness ran: FRED sources PASS; `kalshi_markets` ERROR STALE because the daily live ingest was never run into that scratch warehouse. Full-history Kalshi data is not yet loaded |
| Anthropic LLM forecaster (`AnthropicClient`) | **NOT verified live: no `ANTHROPIC_API_KEY` in `.env`.** Tested against a fake client and the dry-run stub only. The request shape (`output_config` with `effort` and a JSON-schema `format`, no sampling parameters) follows the current SDK documentation but has never been sent |
| Logistic regression, base rate, market forecasters, predictions table | Run on real scratch data (3 contracts) in backtest mode; far too little history for the logistic model to fit |
| Airflow DAGs (daily_ingest, pre_release, post_release, weekly_quality) | In the built Airflow 2.10.5 Docker image on 2026-10-06: all 4 DAGs import with no errors and match the expected structure; `airflow tasks test` ran `check_events_due`, `check_outcomes_pending`, `load_bronze`, `dbt_build` (70 passed), `record_success`, `dbt_test` (57 passed) and `source_freshness` (failure recorded, exit code 1) against a SYNTHETIC fixture warehouse. **Not yet run:** the full scheduler/webserver stack with real data, the live ingestion tasks inside Airflow, the failure webhook against a real endpoint |
| Streamlit dashboard | Rendered in a browser on 2026-10-06 against a SYNTHETIC fixture warehouse, for layout only (both Live and Backtest views); headless `AppTest` tests pass. Not yet viewed with real results because none exist yet |
| Docker images | `docker compose build app` and the Airflow image built; the app container started and answered its Streamlit health check (2026-10-06). Full `make up` stack not run end to end |
| GitHub Actions workflow | **Not run** (needs a GitHub repository). Its dbt parse/build steps were run locally with the same commands; the `airflow-dags` job was not run (the DAG-import checks were run inside the Airflow Docker image instead) |
