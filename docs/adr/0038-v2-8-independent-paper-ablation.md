# V2.8: Independent Paper gate experiments

Date: 2026-09-09

## Evidence and scope

V2.7 accuracy's frozen week returned -0.7599%, with 16 round trips and 25% wins.
Descriptive selected-row outcomes worsened with the raw derivatives gate. Those rows
include repeated HOLD observations and overlapping return windows: they are not independent
trades, and the comparison does not establish causality. Seven days do not establish that
candidate ranking generalizes either. V2.8 measures independently executed portfolios.

The retained score is CALM_PULLBACK_RANK: it favors lower recent returns and volatility.
“Momentum” is the historical lane name, not a trend-following interpretation.

## Frozen arms

| Strategy suffix | New-entry derivatives treatment | Crowding |
| --- | --- | --- |
| production-control | Exact V2.7 accuracy funding/basis/global ratio gates | Existing gate |
| momentum | Observed, no derivatives gate | Shadow |
| long-short | Global account long/short ratio >= 1.0 | Shadow |
| soft-penalty | Each funding/basis/ratio breach adds 0.0005 to the entry cost hurdle | Shadow |

The soft surcharge is a fixed experimental hyperparameter (maximum 0.0015), not an
estimated loss or empirically calibrated return. It reduces the recorded fee-adjusted
expected return; raw score, ranking and gross expected returns stay unchanged. Missing or
stale derivatives block entries in long-short/soft arms. Momentum can proceed without them.
Squeeze remains recorded context. This introduces no short selling or real exchange orders.

Spot guards, confirmations, position sizing, cooldown, turnover/loss limits, hold/exit logic,
and SCORE_LINEAR coefficients are held constant. In particular the retained linear expected
return is a heuristic, not a fitted prediction. Round-trip fee plus slippage is 0.20% and is
deducted before entry. A positive modeled edge does not prove an actual positive edge.
Falling-knife and return-calibration ablations require a separate experiment after this one;
changing them concurrently would confound the derivatives comparison.

## Runtime and identity

Set INVESTMENT_RUNTIME_V28_PAPER_EXPERIMENT_PREFIX to a unique run identifier, e.g.
paper-v2.8-ablation-20260909. Paper fill model must be paper-fill-v2.
Four immutable TOML profiles have separate experiment IDs, portfolios, balances, positions,
fees, cooldowns, and outcomes. They start together with the configured initial cash
(default 1,000,000 KRW each) and use the same decision timestamp on each scheduler tick.
The suite runs serially under one scheduler lease; one arm's error is reported after the
remaining arms have been attempted. Universe and candles use existing point-in-time readers;
as with the existing engine, execution prices are simulated completed-candle references.
This is not an order-book fill model.

Restart reuses frozen identities and balances. It never silently restarts a completed run.
The optional older primary/shadow remain separate. Completed experiments cannot execute new
scheduled decisions: the runtime checks status, identity/hash and deadline before evaluation,
and checks the deadline again before the execution batch. Outcome draining continues.
At expiry any remaining holdings are frozen, not sold without a recorded strategy decision;
evaluate terminal mark-to-market equity and account for exit costs in comparison reports.

The suite is intentionally a Paper-only opt-in, not controlled by the old primary execute
flag. Disable the prefix to disable the suite. Do not change a prefix casually: a new prefix
creates a new set of funded simulated portfolios. Existing data is retained.

## Inspection

- /dashboard includes links to the four diagnostic views.
- GET /api/v1/experiments/v28 lists the frozen arms and identities without scanning outcomes.
- Existing /api/v1/experiments/{id}/metrics, /health and diagnostic endpoints work per arm.
- Existing Paper portfolio endpoints expose each arm's execution ledger.
- Daily review jobs include all four arms. No automatic promotion is introduced.

## Evaluation protocol

Observe seven days and drain the final 24-hour outcomes. Compare net portfolio return,
drawdown, trade expectancy, profit factor, fees, turnover and trade count against the
production-control arm. Also compare per-day/per-asset performance and paired decision-time
outcomes; report missingness separately in every arm. Cluster uncertainty by day/cohort,
not by candidate row. Do not annualize one week as a reliable CAGR/Sharpe estimate.
All arms are long-only; long/short performance comparison is not applicable.

Reject promotion if quality is insufficient or improvement disappears after costs. If all
arms lose, do not choose the least-negative arm as proof of a profitable strategy. Keep
Crowding/Squeeze observational until enough non-neutral episodes exist. Run subsequent
guard/calibration experiments on a new period rather than tuning repeatedly on this week.
