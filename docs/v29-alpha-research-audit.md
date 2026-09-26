# V2.9 Alpha Research Audit and Minimal Research Architecture

Date: 2026-09-26

Scope: read-only audit of the current code and PostgreSQL observation store. The
V2.9 score, gates, portfolios, scheduler, and paper execution path remain unchanged.

## Current architecture and data flow

The code's source-of-truth flow is:

1. `CryptoUniverseService` stores append-only point-in-time Upbit universe JSON.
2. `CryptoIntradayMarketDataService` stores raw Upbit responses as immutable JSON and
   normalized 15-minute OHLCV in one Parquet file per pair.
3. `DynamicPaperRebalanceService._assess` uses only candles whose `available_at` is
   not later than the decision time. It computes 1h, 4h, and 24h returns, 24h
   realized volatility, and 24h average quote volume.
4. Eligible markets are ordered by quote volume and only the top 20 receive V2.9
   component ranks and a score. The remaining eligible markets are observed but are
   not members of the scored cross-section.
5. V2.9 uses the `CALM_PULLBACK_RANK` formula. Component weights are 0.45 (1h),
   0.20 (4h), 0.10 (24h), and 0.25 (volatility). An extreme-score penalty is then
   applied above 0.70.
6. Expected 1h/4h relative returns are fixed linear transforms of the post-penalty
   score. They are not an empirically fitted calibration; the decision diagnostics
   explicitly record `expectedReturnCalibrationIsEmpirical=false`.
7. Entry guards, expected-return checks, confirmation, derivatives/crowding context,
   cooldown, concentration, and rolling loss restrictions determine the selected
   set. Daily turnover/fee/loss limits can subsequently suppress buys.
8. The planner converts selected target weights to order intents. A deterministic
   Paper exchange applies 5 bps fee and 5 bps one-way slippage for `paper-fill-v2`.
9. `FrozenObservationService` persists every candidate snapshot, one context row per
   decision, and outcomes at 15, 30, 60, 120, 240, 720, and 1440 minutes.

## Prompt discrepancies

- The active concurrent store is PostgreSQL, not SQLite. SQLite files are legacy or
  recovery artifacts and must not be opened on the host while Compose is running.
- Forward outcomes include 30m, 2h, and 24h in addition to the prompt's approximate
  15m/1h/4h/12h list.
- Approximately 289 markets are captured per cohort, but the V2.9 ranking universe
  is the 20 most liquid eligible markets, not all observed markets.
- V2.9's name suggests calm pullback ranking, but volatility has a positive rank
  weight in the score and is then constrained by a separate low-volatility entry
  guard. Alpha and gate semantics are therefore mixed.
- BTC 1h/4h/24h return and BTC volatility are not explicit fields in
  `decision_market_context`. They can be obtained from the contemporaneous BTCKRW
  candidate snapshot or recomputed from Parquet.
- Existing `FeatureEvaluator` and research lifecycle components target generic/daily
  research and do not handle candidate-cohort duplication, 15-minute overlap, or
  signal episodes. They should be reused conceptually, not forced onto V2.9 data.

## Data availability matrix

| Research input | Frozen snapshot | Context JSON | Raw/normalized history | Historical safety |
|---|---:|---:|---:|---|
| V2.9 score/raw score/penalty/rank | Yes | No | Recomputable | Exact control is stored |
| 1h/4h/24h asset momentum | Yes | No | Yes | Point-in-time stored |
| 24h realized volatility | Yes | No | Yes | Point-in-time stored |
| 24h quote liquidity | Yes | No | Yes | Point-in-time stored |
| OHLCV / volume shock / breakout | No | Spot subset only | Yes | Recompute using `available_at` |
| Universe membership/warning | Eligibility/reason | No | Universe JSON | Point-in-time history exists |
| BTC return/volatility | Via BTCKRW row | 15m spot return only | Yes | Reconstructable |
| Funding/OI/ratios/taker flow | No | Yes | Derivatives snapshots | Point-in-time stored |
| Basis | No | Official missing; proxy present | Yes | Use versioned proxy only |
| Liquidations | No | 15m aggregate | Events | Point-in-time stored |
| Squeeze V3/Crowding V1 | No | Yes | Signal tables | Point-in-time stored |
| Order/fill/fee/slippage | No | Execution metadata | Paper tables | Authoritative for Paper PnL |

Not safely reconstructable from `decision_snapshot` alone: rolling breakout state,
volume confirmation, arbitrary trend definitions, delisting history beyond stored
universe snapshots, and exact pre-score component percentile ranks. These require the
versioned Parquet/universe history and a point-in-time recomputation.

## Timestamp and leakage audit

No confirmed future-data leakage was found in the current PostgreSQL V2.9 sample:

- zero candidate rows used `reference_at > decision_time`;
- eligible rows had a median reference age of 5 minutes and maximum of 8.84 minutes;
- zero derivative snapshot or signal timestamps exceeded their decision timestamp;
- all outcome targets equal decision time plus the declared horizon;
- no outcomes were evaluated before their target time.

Risks that still require controls:

1. Parquet files are overwritten with the latest ingestion for a candle key. Historical
   recomputation must filter `available_at`, but it cannot recreate an older vendor
   revision if a candle was later corrected.
2. The live universe is dynamic. Research must use stored universe snapshots and must
   not substitute today's listed markets for past cohorts.
3. Four V2.9 variants store identical candidate cohorts. Combining them without a
   uniqueness rule multiplies observations without adding evidence.
4. Adjacent 15-minute decisions and 1h/4h/12h outcomes overlap heavily. Row-level
   p-values and confidence intervals would be misleading.
5. Missing outcomes are not random until missingness by market/time is checked. The
   current 15m missing rate is materially higher than other horizons.

## Gate classification

| Condition | Primary responsibility | Comment |
|---|---|---|
| Falling-knife / short-term / 4h / 24h spike | Predictive alpha | Forecasts continuation/reversal; not portfolio risk |
| Volatility entry/hold threshold | Mixed alpha and risk | Predictive filter plus tail-risk control |
| Consecutive confirmation | Predictive alpha | Requires signal persistence |
| Expected after-fee return | Alpha calibration plus execution economics | Current calibration is fixed, not empirical |
| Funding/basis/long-short/Squeeze/Crowding | Market context / predictive alpha | Currently implemented as a fail-closed entry gate |
| Cooldown | Mixed execution and behavioral risk | Path-dependent; may also remove valid alpha |
| Repeated-loss block | Portfolio risk with asset-performance signal | Mixed semantics |
| Selection concentration | Diversification risk | Operates on historical selections, not exposure alone |
| Maximum positions/asset weight/invested fraction | Portfolio risk | Genuine exposure constraints |
| Daily loss/fee/turnover limits | Portfolio risk | Buy suppression after alpha selection |
| Minimum order/rebalance size | Execution | Avoids uneconomic orders |
| Stale/missing data gates | Operational data quality | Must remain separate from alpha evaluation |

## Minimal research architecture

The implementation adds no runtime dependency from V2.9 to research code.

- `SignalRegistry`: immutable metadata plus deterministic materialization of signal
  values/ranks against one frozen production-control cohort.
- `CandidateSignalEvaluator`: IC and top-k analysis while disclosing timestamps,
  assets, non-overlapping blocks, repeated top-k episodes, and concentration.
- `HypothesisRegistry`: append-only JSON hypotheses with a one-way OOS-consumption
  marker. It complements rather than replaces the existing strategy lifecycle.
- `research/v29_signal_baseline.py`: read-only PostgreSQL report for M0-M3. It opens a
  read-only transaction and writes only a local JSON report.

Initial registered signals are deliberately independent:

- M0: exact stored V2.9 post-penalty score.
- M1a: 1h return relative to contemporaneous BTCKRW.
- M1b: 1h return relative to the same scored cohort median.
- M2: 1h return minus one quarter of 4h return.
- M3: 4h return divided by 4h-scaled 15m realized volatility.

These definitions are hypotheses, not a composite score and not promotion candidates.

## Evaluation and validation policy

1. Register the hypothesis before viewing validation/OOS results.
2. Use exactly one baseline experiment for shared candidate/outcome cohorts.
3. Report pooled IC only as descriptive; primary ranking evidence is the mean
   cross-sectional IC by decision cohort.
4. Report raw rows, unique times/assets, top-k episodes, concentration, and a greedy
   non-overlapping time-block count together.
5. Compare absolute, BTC-relative, and cohort-median-relative forward return.
6. Apply the frozen 20 bps round-trip fee/slippage estimate before promotion.
7. Split by contiguous time. Add a purge at least as long as the prediction horizon.
8. Expose a reserved OOS period once. It cannot be reused for tuning.
9. Record rejected variants. Do not silently alter a signal version.
10. Study derivative variables as context strata/interactions only after the base
    signal has enough episodes in each stratum.

## Research gaps and next safe steps

- The PostgreSQL-era V2.9 sample is only about 2.2 days and has too few independent
  4h/12h blocks for OOS inference. Current runs are suitable for pipeline verification
  and descriptive diagnostics, not validation.
- A future dataset builder should reconstruct M4-M7 from Parquet with an explicit
  candle revision policy and store the resulting dataset checksum.
- Gate counterfactuals need atomic gate flags. Current combined reason strings permit
  descriptive comparisons but make incremental attribution fragile.
- Context analysis should first use pre-declared bins or continuous interactions for
  funding, OI change, basis proxy, and BTC return. Squeeze/Crowding state counts are
  currently too concentrated to support reliable conditional claims.
- Mean reversion should be added only after M0-M3 output is stable and should use the
  same cohort, outcomes, costs, and dependence disclosures.

## Frozen-baseline guarantee

No new research module is imported by `DynamicPaperRebalanceService`, scheduler,
portfolio repository, execution gateway, or FastAPI runtime. V2.9 configuration hashes
and behavior therefore remain unchanged.
