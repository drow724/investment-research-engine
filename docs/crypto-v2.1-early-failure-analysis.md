# Crypto v2.1 Early Safety Failure

## Decision

`paper-v2.1-forward-observation-20260814` was interrupted on 2026-08-16 after the Paper
portfolio failed the safety gate. Its database, decisions, outcomes, and executions remain intact.
It must not be interpreted as a completed seven-day validation.

At interruption review, marked equity had fallen from KRW 937,501.92 to approximately KRW
862,421.27 (about -8.0%). The observation-period ledger contained 36 executions, KRW 49,869.63
of realized loss, KRW 4,798.26 of fees, and 5 profitable versus 14 losing sells.

Repeated decision snapshots are autocorrelated and therefore are not independent trades.
Nevertheless, selected-candidate mean forward returns were negative at every available horizon and
worse than eligible non-selected candidates: approximately -0.17% at 30m, -0.27% at 1h, -1.17%
at 4h, -2.65% at 12h, and -3.51% at 24h. This is sufficient to reject continued automatic Paper
execution, but not sufficient by itself to identify a replacement strategy.

## Analysis-only continuation

The runtime now uses `paper-analysis-main` with execution disabled. The separate
`paper-v2.1-decision-only-analysis-20260816` experiment records frozen candidate decisions and
forward outcomes without submitting Paper orders. This keeps the failed portfolio unchanged and
prevents its holdings from contaminating the new empty-portfolio signal cohort.

The analysis phase should answer:

1. Whether score and each raw momentum component have positive cross-sectional IC.
2. Whether the selected cohort underperforms because of late momentum entry.
3. Which horizon contains the signal peak before reversal.
4. Whether volatility, liquidity, rank, cooldown, or confirmation cohorts explain losses.
5. Whether any proposed v3 rule survives a separate backtest before a fresh Paper experiment.

The old experiment's 15m outcomes were recorded as `MISSING_DATA` because the evaluator excluded
the next candle by open time. The evaluator now reads one candle interval before the decision and
then admits only candles whose `available_at` is strictly after the decision and no later than the
target. Existing rows are preserved; corrected 15m evidence begins with the analysis-only
experiment.
