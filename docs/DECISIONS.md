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
