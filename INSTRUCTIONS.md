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

Commands are filled in as components land; see the Makefile and `docs/architecture.md`.

- Ingestion: `python -m pmeval.ingest.<module>` (see each module's docstring)
- Bronze load: `python -m pmeval.warehouse.bronze`
- dbt: `cd dbt_project && dbt build --profiles-dir .`
- Forecasters, evaluation, dashboard, Airflow: documented as they are added.

## Tests

```bash
make lint
make test
```

Unit tests use recorded fixtures in `tests/fixtures/` and mocked HTTP. They never touch the network.

## Adding a new economic series

1. Add the series definition (FRED id, Kalshi series ticker, release name, resolution rule) to the series registry in `pmeval/config.py`.
2. Record a small fixture for it under `tests/fixtures/`.
3. Add it to the dbt seed/mapping used by `dim_event`.
4. Re-run ingestion, `dbt build`, and the tests.
