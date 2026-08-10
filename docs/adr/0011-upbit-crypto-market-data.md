# ADR 0011: Upbit KRW crypto market data

## Context

The first small-capital trading experiment is KRW-denominated and needs consistent daily data for
multiple assets. A live authenticated exchange integration would broaden risk prematurely.

## Decision

Use Upbit's unauthenticated public market-list and daily-candle REST endpoints as the first crypto
adapter. Keep raw vendor payloads content-addressed as JSON and upsert normalized pair Parquet files.
A UTC daily candle becomes available at the following UTC day boundary; backtests continue to trade
at the next open. Domain pairs use `BASE/QUOTE`, while the adapter alone knows Upbit's `QUOTE-BASE`
format.

## Consequences

No API key or order permission is required. Pagination and idempotent storage support multi-year
daily history, but retries/backoff, API quota coordination, and exchange maintenance calendars still
need production hardening.
