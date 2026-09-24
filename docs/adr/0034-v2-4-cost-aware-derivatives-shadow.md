# ADR 0034: V2.4 is a cost-aware decision-only challenger with a point-in-time BTC overlay

## Status

Accepted for Shadow observation. Paper execution is not yet approved.

## Context

V2.3 accumulated evidence that the highest score bucket was not reliably the best forward-return
bucket, selection was concentrated in a small number of assets, and gross candidate differences
were often too small to clear Upbit fees and estimated slippage. The separately collected BTC
perpetual-futures observations may explain a market-wide risk-on or squeeze context, but their
incremental value has not yet been established.

Turning these observations directly into Paper orders would mix hypothesis creation with
validation. Reusing the V2.3 portfolio or experiment would also contaminate its frozen policy hash,
confirmation history, and outcomes.

## Decision

- Publish the complete immutable `dynamic-intraday-v2.4` policy as registry schema 2. Preserve the
  exact V2.2 and V2.3 schema-1 hashes.
- Exclude a non-held candidate when its latest completed 15-minute candle is more than 30 minutes
  old. Do not create a forward outcome from that stale reference. A held asset with stale pricing
  fails the entire rebalance safely rather than creating an unpriced sell or hold decision.
- Run V2.4 in a separate portfolio and observation experiment at minutes 6, 21, 36, and 51. The
  lane is always `execute=false`; V2.3 Paper execution remains independent.
- Above raw score 0.80, subtract a quadratic penalty that reaches 0.25 at raw score 1.0. Persist
  raw score, penalty, and adjusted score separately.
- Convert the adjusted rank score to pre-registered 1-hour and 4-hour expected relative-return
  hypotheses. Entries and replacements fail closed when this evidence is missing. A cash entry
  must clear the estimated round-trip cost; a replacement must clear the incumbent plus the
  incremental round-trip cost and an additional 10 basis-point advantage.
- Do not force-sell an existing holding merely because return calibration data is missing. A valid
  negative expected relative return may still fail the hold rule.
- After at least 20 selected cohorts, block a new entry when its projected 24-hour selection
  concentration would exceed 25%. Concentration alone never forces a sale.
- Block a new entry or top-up in an asset after at least two sells whose net realized loss over 72
  hours reaches 0.5% of current equity. This rule also never forces a sale.
- Read BTC derivatives context only from the immutable local observation repository. Both signal
  time and snapshot availability must be no later than the rebalance decision, the feature version
  must match, and data older than ten minutes is explicit `STALE` rather than carried forward.
- Store one decision-level BTC context. In V2.4 Shadow it is explanatory only: it must not change
  per-asset scores, ranks, weights, or orders.
- Keep the generic primary manual-rebalance endpoint away from the Shadow portfolio. Manual V2.4
  collection must use `crypto_dynamic_paper_shadow_rebalance`.

## Paper activation gate

The Shadow experiment remains decision-only until it spans at least three Seoul calendar days and
has at least 192 mature 1-hour cohorts and 96 mature 4-hour cohorts. At both horizons it must pass
the pre-registered coverage and missing-data limits, selected gross return above 0.25%, paired
selected-minus-nonselected spread above 0.20%, score IC above 0.03, and a positive paired spread in
both chronological halves. No asset may appear in more than 25% of selected cohorts.
At least 98% of decision cohorts must have a stored BTC context, every context must use
`btc-squeeze-v2-market-streams`, and at least 90% must be fully `AVAILABLE`; missing, stale, and
incomplete rows cannot silently pass the activation review.

Paper return, profit factor, and drawdown are intentionally not gates for the decision-only stage
because an all-cash Shadow portfolio cannot produce execution evidence. Passing Shadow produces
only `SHADOW_READY_FOR_PAPER`; a new isolated Paper portfolio and experiment are still required.

## Consequences

V2.4 can test the proposed ranking while collecting a correctly aligned BTC context without
changing the working V2.3 Paper account. The automated gate verifies context coverage, freshness,
and schema compatibility; it does not yet claim that a squeeze recommendation predicts returns.
Recommendation/state-conditioned forward-return analysis and optional Coinbase/liquidation
coverage remain a required follow-up before that context can become an entry gate. Dry-run evidence
also cannot validate actual hold/replacement behavior, rolling realized-loss blocking, fees, profit
factor, or drawdown. Those require a later isolated V2.4 Paper stage. The current linear
expected-return coefficients are a falsifiable calibration hypothesis, not a profit claim.
