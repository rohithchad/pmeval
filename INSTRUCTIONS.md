# Instructions

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,app,dbt]"
cp .env.example .env     # then fill in the keys you have
```

Python 3.11+ is required (the Docker image uses 3.11).

## Environment variables

| Variable | Purpose |
|----------|---------|
| `FRED_API_KEY` | Free key from fred.stlouisfed.org, needed for FRED/ALFRED ingestion. |
| `ANTHROPIC_API_KEY` | Needed only for live LLM forecasting. Billed separately from a Claude subscription. |
| `PMEVAL_RAW_DIR` | Raw Parquet root (default `./data/raw`). |
| `PMEVAL_WAREHOUSE_PATH` | DuckDB file (default `./data/warehouse.duckdb`). |
| `PMEVAL_FORECAST_HOURS_BEFORE` | Hours before release at which forecasts are made (default 24). |
| `PMEVAL_LOG_LEVEL` | Logging level (default `INFO`). |
| `PMEVAL_LLM_MODEL` | Claude model for the LLM forecaster (default `claude-opus-5-5`). |
| `PMEVAL_LLM_SAMPLES` | Calls per event whose answers are averaged (default 3). |
| `PMEVAL_LLM_EFFORT` | Thinking effort passed to the model (default `medium`). |

Kalshi public market data needs no key. Never commit `.env`.

## Running each component

All commands run from the repository root with the virtualenv active. Paths and keys come from `.env`.

| Step | Command |
|------|---------|
| FRED first-release data | `python -m pmeval.ingest.fred` (needs `FRED_API_KEY`) |
| Release calendar | `python -m pmeval.ingest.release_calendar` |
| Fed statements and meetings | `python -m pmeval.ingest.fed_statements` |
| Kalshi open markets and candles | `python -m pmeval.ingest.kalshi_live [SERIES ...]` (`--status all` for every status) |
| Kalshi settled markets, both tiers (resumable) | `python -m pmeval.ingest.kalshi_historical [SERIES ...] [--no-trades]` |
| Bronze views over raw Parquet | `python -m pmeval.warehouse.bronze` |
| dbt models and tests | `cd dbt_project && dbt build --profiles-dir .` (add `--full-refresh` after changing seed columns) |
| dbt freshness | `cd dbt_project && dbt source freshness --profiles-dir .` |
| Backtest forecasts | `python -m pmeval.forecast.run --mode backtest --forecasters base_rate,market,logreg` |
| Live forecasts | `python -m pmeval.forecast.run --mode live --forecasters auto` |
| LLM forecasts | add `llm` to `--forecasters` (billed; needs `ANTHROPIC_API_KEY`); use `llm_dry_run` for a free stub; `--llm-max-events N` limits spend |
| Scores, intervals, tests | `python -m pmeval.eval.scoring && python -m pmeval.eval.compare` |
| Dashboard | `streamlit run app/streamlit_app.py` |
| Regenerate data dictionary | `python -m pmeval.warehouse.data_dictionary` |

Order matters: ingest, bronze, dbt, forecast, score, dashboard. Every step is safe to rerun.

### Airflow (Docker)

```bash
make up          # builds the images and starts Postgres, Airflow and the dashboard
```

Open http://localhost:8080 (login `admin` / `admin` unless `AIRFLOW_ADMIN_PASSWORD` is set in `.env`;
local use only) and unpause the DAGs you want: `daily_ingest`, `pre_release`, `post_release`,
`weekly_quality`. DAGs start paused. `make down` stops the stack. The dashboard is on
http://localhost:8501.

### Notes

- macOS with a python.org Python: if `pip install` of dbt fails with `CERTIFICATE_VERIFY_FAILED`, run
  `export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")` first.
- Keep one DuckDB version everywhere (the Dockerfiles pin it); do not rename `warehouse.duckdb` after
  `dbt build` without rebuilding, because dbt views embed the file name.

## Tests

```bash
make lint
make test
```

Unit tests use recorded fixtures in `tests/fixtures/`, mocked HTTP and a synthetic warehouse
(`tests/fixture_warehouse.py`, numbers made up for testing). They never touch the network. dbt runs
inside pytest against that warehouse. The Airflow DAG import test is skipped unless Airflow is
installed (it runs in CI and in the Airflow image).

## Adding a new economic series

1. Add the series to `SERIES_REGISTRY` in `src/pmeval/config.py` (FRED id, Kalshi series ticker, release name, FRED release id, release time) and to `dbt_project/seeds/series_registry.csv` with its `headline_transform` and `transform_lag_months`; a test keeps the two in sync.
2. If the contract is not a numeric threshold, extend `describe_contract` in `forecast/llm.py` and the feature inputs in `forecast/model.py`; add `SERIES_DESCRIPTIONS` text for the LLM prompt.
3. Add a small recorded fixture and a test.
4. Re-run ingestion, `dbt build --full-refresh`, and the tests.
