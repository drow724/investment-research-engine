# ADR 0037: V2.7 Algorithmic Crowding Paper lane

## Status

Accepted for isolated Paper observation and execution.

## Context

V2.6 produced positive score IC but did not clear estimated round-trip costs. Its selected
set was concentrated and repeatedly treated a weak one-hour continuation (notably SOL) as
a calm pullback. Separately, Squeeze V3 achieved reliable Mark--Index Basis coverage but
mostly emitted `NONE`; a continuous positioning-crowding feature is needed between rare
squeeze events.

## Decision

- Preserve Squeeze V3 as `btc-squeeze-v3-mark-index-basis-proxy`.
- Add an independent `btc-crowding-v1` feature derived point-in-time from the same immutable
  BTC snapshot and Squeeze V3 change metrics.
- Record long/short crowding, bullish/bearish unwind, dominant side, confidence, liquidation
  coverage, and full source identity in a separate `crowding_signal` table.
- Publish `dynamic-intraday-v2.7` under strategy schema 4 while pinning the V2.6 schema-3
  projection and hash.
- Tighten the one-hour entry floor from -1.5% to -0.7% and increase post-sale re-entry
  cooldown from two to four hours.
- Keep the Squeeze V3 entry gate. Add a new-entry-only Crowding gate that blocks a long
  crowding unwind or the conjunction of long crowding >= 0.70 and bearish unwind >= 0.55.
  Missing/stale/incomplete Crowding data fails closed for buys but never forces a sale.
- Do not add BTC-wide crowding values to per-asset rank scores.
- Record `v2.6-rule-control` on the same frozen decision inputs.
- Execute V2.7 only in a new isolated local Paper portfolio. No live exchange order path is
  introduced.

## Consequences

The Paper lane can test fees, cooldown, realized-loss protection, holding and replacement
semantics that a cash-only Shadow lane cannot. Crowding V1 is not proof of alpha: its
incremental contribution must be evaluated against the persisted V2.6 control, with data
coverage and temporal stability reported separately.

## Accuracy follow-up

The additive `dynamic-intraday-v2.7-accuracy-v1` experiment preserves this strategy's ranking and
production entry behavior while tightening only new-entry price freshness to 20 minutes. Existing
V2.7 experiments and their schema-4 hash remain frozen. Candidate confirmation continues to mean
consecutive score-above-entry-hurdle observations inside the liquidity-trimmed candidate set; it
does not mean that all entry gates passed. A separate shadow-only entry-eligible confirmation count
records consecutive passage through momentum, volatility, cost, Squeeze, and Crowding gates.

Squeeze V3 is a BTC-wide entry-risk context. `FUEL`, `IGNITION`, and `ACTIVE` describe the structure
of a potential squeeze, while missing/stale context and explicit risk states can block new buys.
The strategy does not require an active squeeze to rank an altcoin and does not add a BTC-wide score
to every asset. Crowding V1 remains a market state rather than a rank bonus. The accuracy experiment
records both the frozen state-based gate and a numeric policy-only gate; disagreements are evidence
for later review and do not alter production decisions.

Four same-frozen-input variants are persisted for incremental-alpha analysis: A momentum only, B
raw derivatives, C Crowding only, and D production gates. Reports compare accepted signal quality
and opportunity utility after an explicit 0.20% round-trip estimate. These variants are selection
counterfactuals, not portfolio replays or causal backtests.
