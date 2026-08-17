# ADR 0029: Frozen Paper Decision Forward Observation

## Status

Accepted

## Context

Seven days of Paper operation must produce evidence about scores, ranks, and rejection filters
without leaking future observations into `dynamic-intraday-v2.1` or duplicating the Paper ledger.

## Decision

The active strategy and its `DynamicUniversePolicy` remain frozen. A canonical JSON serialization
of every policy field is SHA-256 hashed when a 168-hour experiment starts. A changed strategy
version or hash invalidates capture rather than silently mixing policies.

The existing `paper_rebalance_decision` and `paper_execution` tables remain authoritative for
trading. A separate SQLite research database stores experiment metadata, immutable asset-level
`DecisionSnapshot` rows, and logically separate `DecisionOutcome` rows. Snapshot fields contain
only values available at the decision `as_of`. Outcomes have a unique `(snapshot_id, horizon)` key.

For each eligible, selected, rejected, exited, and data-ineligible asset, capture normalizes the
action while preserving the original reason. Available candidate score, rank, liquidity, reference
price and candle timestamp, raw 1h/4h/24h momentum, raw volatility, selected rank,
current/target weight, portfolio context, UTC hour, and weekday are retained. No unavailable
feature components are invented. Eligible-but-not-selected rows are never discarded.

Forward return uses the decision's last completed 15-minute close as reference and the latest
completed close available at the 15m, 30m, 1h, 4h, 12h, or 24h target. MFE and MAE use the highest high and
lowest low that became available after the decision through the horizon. A final candle more than
30 minutes stale produces `MISSING_DATA`; values are never interpolated.

Horizons are represented canonically in minutes. The additive `decision_outcome_minute` table is
created without dropping the legacy hourly table, and existing hourly outcomes are copied with
`horizon_minutes = horizon_hours * 60`. Each horizon is evaluated and inserted independently with
a `(snapshot_id, horizon_minutes)` primary key, so retries are idempotent and missing future data
cannot block trading or another horizon.

Rejected candidates receive the same outcome evaluation as executed candidates. Outcome
evaluation runs in the existing scheduler at minutes 7/22/37/52 as a single-instance, leased,
idempotent state mutation. The evaluator cannot be imported or called by strategy selection.

BTC buy-and-hold uses the first and last completed BTC 15-minute closes inside the exact observed
interval. Strategy net performance uses marked decision equity and authoritative fees/executions.
Expectancy is mean net realized PnL on completed sells because realized PnL already includes the
sell fee; it is not charged twice.

## Consequences

The 72-hour checkpoint is operational only. At 168 hours the experiment becomes `COMPLETED`, but
24-hour outcomes may continue filling for decisions near the end. Reports never promote a model,
enable live trading, tune thresholds, or generate v3 automatically.

## Analysis queries

The minute table is canonical for new analysis. `status = 'COMPLETED'` excludes explicit missing
market-data outcomes.

```sql
-- score vs 12h return
SELECT s.score, o.forward_return
FROM decision_snapshot s JOIN decision_outcome_minute o USING (snapshot_id)
WHERE o.horizon_minutes = 720 AND o.status = 'COMPLETED';

-- raw 1h momentum vs 1h return
SELECT s.momentum_1h, o.forward_return
FROM decision_snapshot s JOIN decision_outcome_minute o USING (snapshot_id)
WHERE o.horizon_minutes = 60 AND o.status = 'COMPLETED'
  AND s.momentum_1h IS NOT NULL;

-- selected vs eligible-but-not-selected average 4h return
SELECT CASE WHEN s.selected = 1 THEN 'selected' ELSE 'eligible-not-selected' END AS cohort,
       COUNT(*) AS observations, AVG(o.forward_return) AS average_return_4h
FROM decision_snapshot s JOIN decision_outcome_minute o USING (snapshot_id)
WHERE s.eligible = 1 AND o.horizon_minutes = 240 AND o.status = 'COMPLETED'
GROUP BY cohort;

-- eligible score decile average 12h return (1 = highest score)
WITH ranked AS (
  SELECT snapshot_id,
         NTILE(10) OVER (PARTITION BY decision_id ORDER BY score DESC) AS score_decile
  FROM decision_snapshot WHERE eligible = 1 AND score IS NOT NULL
)
SELECT r.score_decile, COUNT(*) AS observations,
       AVG(o.forward_return) AS average_return_12h
FROM ranked r JOIN decision_outcome_minute o USING (snapshot_id)
WHERE o.horizon_minutes = 720 AND o.status = 'COMPLETED'
GROUP BY r.score_decile ORDER BY r.score_decile;
```
