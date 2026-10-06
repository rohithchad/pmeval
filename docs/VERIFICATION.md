# Live vs mock verification tracker

Updated as components are built. "Live" means run in a development session against the real
service and the output was observed. "Mocks only" means unit tests with recorded fixtures.

| Component | Status |
|-----------|--------|
| HTTP client (retries, rate limit) | Mocks only |
| Raw Parquet writer | Mocks only (local filesystem; no S3) |
| Kalshi live client: series, events, markets, candlesticks, trades | Live read on 2026-10-06 (small requests, public data); fixtures recorded from it |
| `kalshi_live` ingestion CLI end to end | Not yet run |
| Kalshi historical client: cutoff, historical markets, candlesticks, trades | Live read on 2026-10-06 (2 markets of KXU3) |
| Backfill routing across both tiers + resume state | Live on a small sample (2 historical + 1 live-settled market). Full-series backfill not yet run |
| FRED client: first-release observations (CPIAUCSL, PAYEMS, UNRATE, GDPC1) and unrevised DFEDTARU | Live on 2026-10-06 with the user's FRED key; fixtures recorded from it |
| Release calendar (FRED release dates for CPI, jobs, GDP) | Live on 2026-10-06 to a scratch directory |
| Fed statement + meeting ingestion | Live on 2026-10-06 (47 statements, 57 meetings) to a scratch directory |
