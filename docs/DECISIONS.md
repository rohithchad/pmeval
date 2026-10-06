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
