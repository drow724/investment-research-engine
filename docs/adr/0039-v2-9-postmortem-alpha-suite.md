# ADR 0039: V2.9 postmortem alpha suite

## Status

Accepted for isolated Paper observation. It is not enabled by default.

## Context

The completed V2.8 postmortem did not confirm a positive after-cost edge. Its production-control
lane lost 1.102%, while the selected four-hour cohort averaged -0.466% after the frozen 20 bp cost
hurdle. Lower turnover reduced the loss, but did not establish that the underlying signal was
profitable.

Three findings were worth testing without promoting them directly into production rules:

- the candidates admitted by the existing very-low-volatility cap underperformed, while a middle
  volatility slice was positive in-sample;
- a negative short-term/four-hour pullback slice was positive in-sample;
- candidates rejected by the 25% selection-concentration cap were positive in-sample.

Each finding is exploratory and may be a multiple-testing artifact. V2.9 therefore uses parallel
Paper portfolios and a contemporaneous control rather than rewriting V2.8.

## Decision

Run four independently funded, seven-day lanes with the same market timestamps and `paper-fill-v2`:

1. `production-control`: exact V2.8 production-control policy.
2. `volatility-relaxed`: remove the relative volatility quantile and raise only the absolute entry
   cap from 0.003 to 0.018. The incremental observations above the control cap test the volatility
   pocket; no lower bound is fitted from the postmortem.
3. `pullback-window`: admit only 1h momentum from -3.0% to -0.7% and cap 4h momentum at -0.8%.
4. `concentration-relaxed`: raise only the historical selection concentration cap from 25% to 50%.

Ranking, confirmation, BTC derivatives/crowding gates, exits, position sizing, fee/slippage,
turnover limits, and all other controls remain unchanged. The suite is configured independently by
`INVESTMENT_RUNTIME_V29_PAPER_EXPERIMENT_PREFIX`; leaving it blank creates no V2.9 portfolios.

The forward observation horizons now include 120 minutes so the requested entry/exit path analysis
does not have a structural two-hour gap.

## Consequences

- A challenger must beat the contemporaneous control after costs and across time slices; a positive
  point estimate alone is insufficient.
- The relaxed-volatility lane deliberately tests a broad incremental range instead of encoding the
  best in-sample lower boundary.
- The concentration result is tested as a relaxed cap, not as an immediate removal or a new sizing
  algorithm.
- V2.8 data and behavior stay immutable, and the active environment is not changed by this ADR.
