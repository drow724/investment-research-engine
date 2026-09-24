# ADR 0035: V2.5 validates the V2.4 four-hour loss and Basis-data hypotheses

## Status

Accepted for a new decision-only Shadow experiment. It is not approved for Paper execution.

## Context

V2.4 exposed two separate problems that must not be silently conflated:

1. V2.4-selected assets had a negative four-hour forward result. A calm-pullback rank can still
   select an asset whose four-hour move is an unfinished downtrend rather than a reversible pullback.
2. The optional Binance official Basis series can be absent or rate-limited. Treating a missing
   official value as zero, carrying it forward, or mixing a proxy into the same feature version
   would falsify both data-quality and signal evidence.

The V2.4 experiment, policy hash, decisions, and outcomes are frozen evidence. A correction must
therefore be a separate version, portfolio, and observation experiment.

## Decision

- Publish `dynamic-intraday-v2.5` as a separate schema-2 policy snapshot.
- Preserve every V2.4 spot parameter except one new-entry condition:
  `momentum_4h >= 0`. It rejects a candidate that is still negative over four hours, while leaving
  the short-term pullback cap, fee model, concentration control, and all existing V2.4 guards
  unchanged. Existing holdings are not force-sold by this new entry-only diagnostic rule.
- Keep the BTC overlay in `SHADOW` mode. V2.5 records it but does not change rank, selection,
  weights, or orders. This avoids claiming predictive value before it is measured.
- Pin V2.5 to `btc-squeeze-v3-mark-index-basis-proxy`. V3 always uses
  `mark_price / index_price - 1`, derived from required Premium Index fields. The optional official
  Basis remains independently stored as `basis_rate` and may remain `MISSING_DATA`.
- Run V2.5 only in a new, isolated, non-executing Shadow portfolio and experiment. Before it is
  enabled, interrupt V2.4 only after its pending forward outcomes are drained; never reuse either
  frozen identity.

## Direct validation criteria

V2.5 must collect the normal 1h/4h selected-versus-nonselected evidence. The review report makes
the four-hour loss rate, paired underperformance rate, selected return, paired spread, and temporal
halves explicit. This lets us falsify the narrow hypothesis: preventing a negative four-hour entry
trend improves the *future* four-hour relative outcome.

For every V2.5 decision cohort, the runtime also persists a non-executing
`v2.4-rule-control` selection variant. It uses the same V2.5 candidate inputs and portfolio state,
but restores only the V2.4 four-hour entry floor (`momentum_4h >= -0.03`). The review may therefore
measure actual V2.5 versus control-only selections, overlap, changed cohorts, and their paired
future returns. This is a same-cohort rule control, not a historical V2.4 portfolio replay or a
causal attribution of every V2.5 change.

The report also records, separately, the official Basis present/missing rate and the actual
Basis-input source coverage. V2.5's proxy data-quality hypothesis passes only when the V3 context
is point-in-time available at the configured rate and the input source is consistently
`MARK_INDEX_PROXY`; it does **not** claim that the proxy predicts returns merely because it is
available.

V2.5 cannot be considered Shadow-ready from ordinary score metrics alone.  Its same-input control
must cover every mature scored candidate, provide at least 96 paired four-hour decision cohorts
including 30 cohorts whose actual/control selections differ, and retain at least 90% completed
outcomes on each side with no more than a five-point coverage gap.  The actual V2.5 selection must
also have a non-negative mean four-hour return relative to the restored-rule control in both
chronological halves.  These are pre-registered decision rules for the isolated challenger, not a
claim that the control reconstructs the historical V2.4 portfolio.

The normal conservative Shadow-to-Paper gates continue to apply: at least three Seoul calendar
days, 192 mature 1h cohorts, 96 mature 4h cohorts, acceptable outcome completeness, positive
selected relative performance and score IC at both horizons, stable halves, and concentration
limits. A failed four-hour gate rejects V2.5; a pass only permits a later, isolated Paper-stage
proposal.

## Consequences

V2.5 is a tightly scoped, falsifiable research experiment, not a performance patch. It does not
alter V2.3 Paper execution or rewrite V2.4. Its preserved V2.4 records remain a temporal baseline;
the additive same-cohort control narrows the inference specifically to the restored four-hour-entry
rule. Both views still require sufficient calendar and cohort coverage before interpretation.
