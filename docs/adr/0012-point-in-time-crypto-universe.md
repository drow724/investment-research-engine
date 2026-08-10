# ADR 0012: Point-in-time crypto universe

## Context

Using today's listed coins in an old backtest creates survivorship and look-ahead bias. Upbit's
current market-list response does not provide complete historical listing intervals.

## Decision

Capture append-only market-list snapshots with `observed_at`. At time `t`, use only the latest
snapshot observed by `t`; before the first snapshot, no membership is inferred. Then apply warning,
minimum-history, trailing quote-volume, and ranking rules. BTC can be retained as a regime input
without forcing it into the final portfolio.

## Consequences

Forward-collected tests become point-in-time correct. Historical tests before snapshot collection
must use a separately qualified listing-history dataset or remain in explicit static-universe mode.
The engine will not manufacture unavailable listing history.
