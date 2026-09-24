# ADR 0030: Calm-pullback V2.2 decision-only challenger

## Status

Accepted for forward observation on 2026-08-19. Paper execution remains disabled.

## Context

The post-coverage-fix V2.1 cohort contained 129 decision cycles and mature selected outcomes at
15, 30, 60, 240, 720, and 1,440 minutes. V2.1 selected candidates underperformed eligible but
unselected candidates from 15 minutes through 24 hours. Its cross-sectional score IC was negative
from 15 minutes through 12 hours, and the one-hour momentum component was the most consistently
negative component. V2.1 also concentrated 93 of 129 selections in PRLKRW and selected candidates
with roughly twice the volatility of the eligible population.

The evidence supports rejecting short-term pump chasing. It does not yet prove that a replacement
is profitable after Upbit fees and modeled slippage, so the replacement must start as a separate
challenger rather than mutate or execute the frozen V2.1 experiment.

## Decision

Publish `dynamic-intraday-v2.2` as a separately fingerprinted policy. Within the 20 most liquid
eligible KRW markets, calculate percentile ranks and score calm pullbacks as:

```text
0.45 * (1 - rank(momentum_1h))
+ 0.20 * (1 - rank(momentum_4h))
+ 0.10 * (1 - rank(momentum_24h))
+ 0.25 * (1 - rank(volatility))
```

The score is unitless on `[0, 1]`; it must never be compared directly with a fee rate. Entry uses a
separate `0.60` score hurdle and requires three consecutive decision confirmations.

Entry guards are pre-registered as follows:

- one-hour momentum between -1.5% and +0.5%;
- four-hour momentum between -3.0% and +1.5%;
- 24-hour momentum between -8.0% and +8.0%;
- volatility no greater than the smaller of 1.2% and the candidate-pool 80th percentile;
- stable-value base assets excluded from this directional strategy.

The challenger holds at most two assets, at most 25% each, with total target exposure capped at
50%. It uses a two-hour re-entry cooldown, a four-hour maximum holding period, a 0.15 replacement
advantage, a two-times-equity daily turnover cap, a 0.3% daily cost cap, and a 1% daily realized-loss
limit.

V2.1 is interrupted with an auditable supersession reason. Its remaining outcomes continue to be
evaluated through the configured drain list. V2.2 uses a new Paper portfolio and experiment ID so
old scores cannot satisfy new confirmation rules and evidence cannot mix across versions.

## Activation gate

Keep `INVESTMENT_RUNTIME_DYNAMIC_PAPER_EXECUTE=false`. Execution may be reconsidered only after a
future, untouched V2.2 cohort spans at least three calendar days and contains at least 192 mature
one-hour decision cohorts and 96 mature four-hour decision cohorts. At both horizons it must show:

- selected mean gross return above 0.25%;
- selected-minus-unselected return above 0.20%;
- score IC above 0.03;
- no single asset in more than 25% of selected cohorts.

## Consequences

The same historical sample used to form this hypothesis shows improvement, especially near four
hours, but does not reliably clear the approximately 0.20% modeled round-trip cost. V2.2 is
therefore an evidence-generating challenger, not an approved trading strategy. The separate
experiment identity makes failure cheap and preserves V2.1 as immutable evidence.
