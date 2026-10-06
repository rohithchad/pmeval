# Architecture

```mermaid
flowchart LR
    K[Kalshi public API<br/>live + historical tiers] --> R
    F[FRED / ALFRED<br/>first-release vintages] --> R
    C[Release calendar +<br/>Fed statements] --> R
    R[(Raw Parquet<br/>data/raw/source/ingest_date)] --> B
    B[(DuckDB bronze tables)] --> S
    S[dbt staging<br/>typed, UTC, deduped] --> M
    M[dbt marts<br/>dim_event, fct_market_forecast,<br/>fct_outcome, fct_features_asof] --> FC
    FC[Forecasters<br/>base rate, market, logistic model, LLM] --> P[(predictions table)]
    P --> E[Evaluation<br/>Brier, log loss, calibration,<br/>cluster bootstrap, DM test]
    M --> E
    E --> D[Streamlit dashboard]
    A[Airflow DAGs] -. orchestrates .-> R
    A -. orchestrates .-> FC
    A -. orchestrates .-> E
```

## Layers

| Layer | Where | Contents |
|-------|-------|----------|
| Raw | `data/raw/` (Parquet) | Untouched API responses plus `ingested_at`. Path is configurable so S3 can replace it. |
| Bronze | DuckDB | Views/tables registered over the raw Parquet. No transformation. |
| Silver | dbt `staging` | Type casting, naming, UTC timestamps, price-to-probability, dedup. |
| Gold | dbt `marts` | One row per event, market probability at forecast time, outcomes, as-of features. |
| Predictions / scores | DuckDB | Written by Python forecasters and the evaluation code. |

## No look-ahead

Every event has one `forecast_time` (default: 24 hours before the scheduled release).
Every forecaster input must be timestamped at or before it. This is enforced three ways:
a dbt generic test on the feature mart, a runtime guard in the LLM forecaster, and
pytest tests that fail when any input is newer than `forecast_time`.
