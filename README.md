# Investment Research Engine

한국어 사용자용 현재 개발 현황은
[`docs/user-guide-current-status-ko.md`](docs/user-guide-current-status-ko.md)를 참고한다.

A Python 3.12 modular monolith for reproducible, point-in-time-safe investment research. Phase 1
implements daily Bitcoin OHLCV ingestion, a focused feature and forward-label pipeline,
predictive evaluation, walk-forward validation, and a small FastAPI contract. It does not place
orders or implement the future Spring control plane.

Phase 2 adds a Bitcoin market-intelligence layer for testing whether observable demand absorbs
holder and exchange supply pressure. It remains a research system: composite scores are hypotheses,
not trading signals or claims about market participants.

Version 0.3 adds a separate `investment.crypto` foundation for cross-sectional research and
backtesting across a trading universe. It does not extend the Bitcoin investment engine with
tactical trading responsibilities and does not submit live orders.

Version 0.4 adds public Upbit KRW daily-market ingestion, forward-collected point-in-time universe
snapshots, liquidity eligibility, and persistent idempotent SQLite paper accounting. Live exchange
orders remain disabled.

Version 0.5 adds leakage-safe cross-sectional ML research: point-in-time features, executable-open
labels, purged walk-forward comparison of Ridge and histogram gradient boosting, a versioned local
model registry, ranked inference, and a risk-checked dry-run paper allocation job. It does not claim
a profitable edge and still cannot submit live orders.

Version 0.6 adds the crypto research lifecycle. Strategy/model versions, dataset snapshots,
training, backtest and validation runs are durable domain records. A challenger can become a
candidate after policy evaluation, but only explicit human approval can replace the active champion.

Version 0.7 makes Python an autonomous long-running execution plane. An internal APScheduler runs
configuration-driven application jobs, while runtime state, job history, heartbeat, and best-effort
status events make the engine observable by a future Spring control plane. Spring observes Python;
it is not required to trigger normal execution.

Version 0.8 starts the personal intraday research slice: Upbit 15/60/240-minute candle ingestion,
timeframe-isolated storage, 15-minute next-open backtesting, a UTC-aligned four-hour BTC regime,
hourly rebalance evaluation, and automatic 15-minute data synchronization. It remains Paper
research and does not submit orders.

Version 0.9 adds a dynamic KRW-market Paper allocation loop. It ranks the liquid markets for which
at least seven days of completed 15-minute data exists, selects up to three positive-momentum
assets, and plans both exits and new entries. Execution is confined to the local Paper ledger,
disabled by default, and never sends an Upbit private order.

Version 0.10 closes the model-approval boundary. Training still writes only `LATEST`; a model can
become `ACTIVE` only through an explicit approval API after its selected model has positive
walk-forward validation and test IC. Every activation records the approver, time, prior model,
policy version, and accepted scores in an append-only local audit record.

Version 0.10.1 makes Upbit collection rate-limit aware. All public clients in the process share an
eight-request-per-second limiter, honor `Remaining-Req`, retry HTTP 429 with bounded exponential
backoff and jitter, and do not retry HTTP 418 temporary blocks. Intraday multi-pair collection
preserves successful pairs and reports failed pairs instead of discarding the entire batch.

Version 0.11 adds opt-in bootstrap for autonomous Paper execution. When a Paper portfolio ID is
configured, startup idempotently creates that local portfolio with configurable virtual KRW and
registers the dynamic rebalance. The repository's local `.env` enables `paper-main` with
KRW 1,000,000 and Paper execution; `.env` is ignored by Git. No live exchange adapter exists.

Version 0.11.1 makes the configured Paper portfolio a first-class dashboard view. It loads
`paper-main` automatically, refreshes its virtual cash and positions every 30 seconds, shows
estimated equity and cash weight, exposes the automatic schedule, and provides a confirmed manual
trigger for the same Paper-only rebalance job.

Version 0.12 adds persistent Paper execution history to the API and dashboard and changes the
dynamic Paper evaluation cadence to every 15 minutes after candle sync. Selection combines one-,
four-, and 24-hour momentum but trades only above a 0.20% round-trip hurdle: two 0.05% KRW fees plus
two 0.05% estimated-slippage allowances. This is an experimental cost-aware Paper rule, not a
profit claim, and live trading remains unavailable.

Version 0.13 introduces `dynamic-intraday-v2`: two-observation entry confirmation, separate
entry/hold/exit hysteresis, daily turnover/fee/realized-loss budgets that block new buys but permit
risk-reducing sells, exact full-position liquidation, dust cleanup, and durable decision audits.
Each audit stores strategy version, point-in-time scores, selections, orders, risk violations,
and explicit decision reasons. The developer dashboard translates those reasons so a zero-order
decision shows whether it was caused by entry confirmation, hold hysteresis, a small target-weight
difference, an exhausted daily risk budget, or the absence of an eligible asset. The audit is
visible in the development dashboard.

Version 0.14 introduces `dynamic-intraday-v2.1`. The 15-minute collector now tracks the top 50
KRW markets, bootstraps ten days of data, and repairs existing files that have fewer than seven
days of bars. A sold asset cannot be re-entered for one hour, and a new asset can replace a retained
position only when its score is at least 1 percentage point higher. These controls target coverage
and rotation quality; they do not claim improved returns.

Version 0.15 hardens bounded-context and scheduler safety. Binance ingestion is now authoritative
under reusable crypto market data, Bitcoin publishes an explicit research contract, and AST tests
guard dependency direction. State-changing jobs use centralized single-instance/coalescing policy,
scoped SQLite leases, stable execution-window identities, and explicit locked/duplicate outcomes.

Version 0.16 adds a 168-hour frozen `dynamic-intraday-v2.1` Paper observation. It fingerprints the
complete strategy policy, records point-in-time candidate and rejection snapshots, evaluates
idempotent 15m/30m/1h/4h/12h/24h returns plus MFE/MAE, compares marked net performance with BTC, and exposes
health and evidence reports without feeding outcomes back into trading or enabling live execution.

Forward Observation never changes a trading signal. It freezes every eligible, selected,
eligible-but-not-selected, and ineligible candidate as it was known at decision time, including
the raw 1h/4h/24h momentum and volatility components. A separate evaluator fills each minute-based
horizon only after future completed-candle data becomes available; these outcomes are not inputs to
selection, sizing, risk controls, execution, or scheduling.

## Docker operation

The autonomous FastAPI and scheduler process can run as one Docker Compose service while keeping
SQLite, Parquet, runtime state, experiments, and models on host bind mounts.

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f investment-engine
```

Never run the host uvicorn process and the Compose service at the same time. Do not scale the
service beyond one instance. See [`docs/docker-operations-ko.md`](docs/docker-operations-ko.md).

## Architecture

```text
FastAPI adapter → application services → bitcoin research or crypto trading bounded context
```

The engine distinguishes `open_time` (the candle event), `available_at` (when the completed candle
was knowable), and `ingested_at` (when this system collected it). `PointInTimeDataset` rejects rows
whose `available_at` is later than the requested as-of timestamp. Feature code receives only that
filtered dataset. Forward-looking labels live in a separate pipeline and are evaluation targets,
never feature inputs.

## Bitcoin versus crypto trading

`investment.bitcoin` answers long-horizon questions about the user's core BTC investment.
`investment.crypto` treats BTC, ETH, SOL, and future eligible assets as members of a tactical
trading universe. Crypto-domain portfolios accept only `PAPER_TRADING` and `SYSTEMATIC_TRADING`;
`CORE_INVESTMENT` is rejected by domain constructors, risk approval, and API validation. A trading
BTC sell can therefore never target the core-investment BTC account.

The first architecture-validation slice is:

```text
Point-in-time daily OHLCV
  → deterministic BTC trend regime
  → cross-sectional momentum ranking
  → equal-weight portfolio with explicit cash
  → deterministic allocation risk checks
  → next-open backtest
  → fee/slippage-adjusted metrics and benchmarks
```

The universe may contain many liquid assets while `maximumPositions` keeps the actual portfolio
narrow. Negative momentum, insufficient history/liquidity, or `RISK_OFF` can produce an all-cash
portfolio. The reference strategy is replaceable through the `Strategy` Protocol and is not a
claim that momentum will remain profitable.

Strategies only receive market/regime context and return signals. They cannot access
`ExchangeGateway`. Portfolio construction creates target weights, the independent risk engine may
reject them, and only an `ApprovedOrder` can reach the paper or future live exchange gateway.
See [`docs/crypto-trading-engine.md`](docs/crypto-trading-engine.md) for boundaries and live-trading
prerequisites.

Research data is written as deterministic raw JSON and upserted normalized Parquet under `data/`;
these generated files are ignored by Git. The small CSV under `tests/fixtures` is tracked.

## Bitcoin intelligence data

External metric records use a vendor-neutral long form:

```text
dataset, entity, metric, event_time, available_at, ingested_at,
valid_from, value, unit, source, revision
```

ETF vendors can be connected through `HttpBitcoinEtfFlowProvider`, which accepts either a JSON list
or `{ "records": [...] }`; the vendor must provide an explicit publication timestamp. On-chain has
a provider Protocol plus a normalizer for LTH supply/spending, realized profit/loss, and exchange
flows. Binance USD-M futures supplies the initial concrete derivatives adapter for funding and open
interest. Liquidation and basis adapters are not included yet.

Raw batches use `RawMetricJsonStorage`; revision-preserving normalized metrics use
`MetricParquetStorage`. Ingest validation rejects schema mismatches, duplicates, required nulls,
infinite values, naive timestamps, and negative values for metrics where negatives are impossible.
Missing data defaults to `NO_FILL`; zero, forward-fill, and drop behavior must be requested
explicitly.

Feature families include:

- ETF net-flow sums, z-scores, acceleration, and positive-day count
- neutral LTH supply/spending and realized profit/loss transformations
- exchange inflow/outflow/netflow transformations when the source metric exists
- funding and open-interest transformations
- equal-weight demand and supply pressure baselines
- `btc_absorption_score = demand_pressure - supply_pressure`
- demand/supply versus price divergence

Composite feature metadata records version, inputs, weights, parameters, creation time, and history
scope. ETF features are explicitly `POST_ETF`: US spot Bitcoin ETF history starts in 2024 and is a
short institutional overlay, not a long-history feature with equal statistical confidence.

“Large address” does not mean “whale investor.” Addresses may belong to exchanges, custodians, ETF
custody, institutions, or individuals, so Phase 2 exposes only a conservatively named provider
abstraction and does not include large-address data in the baseline score without a qualified source.

## Install and verify

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
mypy src/investment
```

## Run the API

```bash
uvicorn investment.interfaces.api.fastapi.main:app --reload
curl http://127.0.0.1:8000/api/v1/health
```

For the normal autonomous process, use:

```bash
python -m investment
```

FastAPI startup starts the internal scheduler and graceful shutdown stops it. The default jobs are
runtime heartbeat, daily Upbit universe capture, and incremental daily crypto market sync. Their
cron expressions, pairs, timeouts, instance identity, local state path, and Spring event endpoint
are environment-backed settings prefixed with `INVESTMENT_`.

```text
INVESTMENT_RUNTIME_INSTANCE_ID
INVESTMENT_RUNTIME_EVENT_ENDPOINT
INVESTMENT_RUNTIME_EVENT_RETRY_DELAYS_JSON
INVESTMENT_RUNTIME_HEARTBEAT_CRON
INVESTMENT_RUNTIME_UNIVERSE_SNAPSHOT_CRON
INVESTMENT_RUNTIME_MARKET_SYNC_CRON
INVESTMENT_RUNTIME_MARKET_SYNC_PAIRS_JSON
INVESTMENT_RUNTIME_INTRADAY_SYNC_CRON
INVESTMENT_RUNTIME_INTRADAY_SYNC_LOOKBACK_HOURS
INVESTMENT_RUNTIME_INTRADAY_MAXIMUM_ASSETS
INVESTMENT_RUNTIME_DYNAMIC_REBALANCE_CRON
INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID
INVESTMENT_RUNTIME_DYNAMIC_PAPER_EXECUTE
INVESTMENT_RUNTIME_DYNAMIC_PAPER_INITIAL_CASH
```

When the event endpoint is unset, autonomous jobs still run and status remains local. When set,
events are posted to Spring with an `Idempotency-Key` matching `eventId`. Delivery failure marks
reporting as degraded but cannot change a successful research job into a failed one.

```text
GET  /api/v1/runtime/status
GET  /api/v1/jobs
GET  /api/v1/jobs/{executionId}
POST /api/v1/jobs/{jobName}/run
```

The POST endpoint is for administration and development; the scheduler is the normal trigger.

The dynamic rebalance job is not registered unless
`INVESTMENT_RUNTIME_DYNAMIC_PAPER_PORTFOLIO_ID` names an existing Paper portfolio. When registered,
it runs at `2,17,32,47 * * * *` by default, just after every 15-minute sync. It remains a dry-run
unless `INVESTMENT_RUNTIME_DYNAMIC_PAPER_EXECUTE=true` is explicitly set. Even then, fills affect
only the local SQLite Paper ledger.

This checkout includes a local, Git-ignored `.env` that enables automatic execution for
`paper-main`. On startup, the portfolio is created if absent. Existing balances and positions are
never reset by changing the configured initial cash.

The default intraday schedule is `1,16,31,46 * * * *`: one minute after each 15-minute boundary.
Backfill 15-minute data explicitly before the first backtest:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/crypto/market/data/intraday/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "pairs":["BTC/KRW","ETH/KRW","SOL/KRW"],
    "start":"2026-07-01T00:00:00Z",
    "end":"2026-08-01T00:00:00Z",
    "timeframe":"15m"
  }'
```

Then run the 15-minute research backtest. Four bars equal an hourly rebalance evaluation:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/crypto/backtests/intraday \
  -H 'Content-Type: application/json' \
  -d '{
    "pairs":["BTC/KRW","ETH/KRW","SOL/KRW"],
    "start":"2026-07-10T00:00:00Z",
    "end":"2026-08-01T00:00:00Z",
    "signalLookbackBars":16,
    "rebalanceBars":4,
    "maximumPositions":3
  }'
```

To inspect automatic selection across the stored KRW universe without changing the portfolio:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/crypto/paper/portfolios/dynamic-rebalance \
  -H 'Content-Type: application/json' \
  -d '{
    "portfolioId":"paper-main",
    "asOf":"2026-08-14T00:00:00Z",
    "execute":false
  }'
```

This is not restricted to current holdings. Existing positions that fall out of selection receive
Paper sell plans, while newly selected eligible coins receive Paper buy plans.

Training does not activate a model. Review the returned comparison and explicitly approve it:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/crypto/ml/models/MODEL_ID/activate \
  -H 'Content-Type: application/json' \
  -d '{"approvedBy":"researcher@example.com"}'
```

`GET /api/v1/crypto/ml/models/active` returns the currently approved artifact. Activation is
rejected when the selected model's validation or held-out test IC is not positive.

Interactive docs are at `http://127.0.0.1:8000/api/v1/docs` and the stable schema is at
`/api/v1/openapi.json`.

The Korean development trading dashboard is available at:

```text
http://127.0.0.1:8000/dashboard
```

It shows autonomous engine health, current/recent jobs, stored daily market prices, universe health,
strategy experiments, latest trained-model metadata, Paper portfolio balances, readiness warnings,
and development-only manual job controls. Displayed prices are stored completed daily candles, not
live exchange quotes, and the dashboard cannot place orders.

Run the crypto momentum reference backtest after placing one normalized Parquet file per pair under
`data/normalized/crypto/price/` (for example `BTCKRW.parquet`). Each file requires UTC-aware
`open_time`, `available_at`, OHLC, and `volume` columns:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/crypto/backtests \
  -H 'Content-Type: application/json' \
  -d '{
    "pairs":["BTC/KRW","ETH/KRW","SOL/KRW"],
    "start":"2024-01-01T00:00:00Z",
    "end":"2026-01-01T00:00:00Z",
    "initialCapital":"100000",
    "portfolioPurpose":"PAPER_TRADING",
    "lookbackDays":30,
    "maximumPositions":3,
    "rebalanceDays":7,
    "feeRate":"0.0005",
    "slippageRate":"0.001"
  }'
```

Signals at a trading-day open see only candles published before that open. Rebalanced holdings earn
open-to-next-open returns, preventing use of the current day's completed candle at its own opening
price. Results report CAGR, total/gross return, Sharpe, Sortino, drawdown, volatility, hit rate,
turnover, fee/slippage impacts, BTC buy-and-hold, and cash benchmarks.

## Upbit data and universe collection

Synchronize selected daily KRW pairs through the public, unauthenticated Upbit adapter:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/crypto/market/data/sync \
  -H 'Content-Type: application/json' \
  -d '{
    "pairs":["BTC/KRW","ETH/KRW","SOL/KRW"],
    "start":"2024-01-01T00:00:00Z",
    "end":"2026-01-01T00:00:00Z"
  }'
```

Capture the currently observable KRW market list, then inspect the latest stored snapshot:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/crypto/market/universe/snapshots
curl http://127.0.0.1:8000/api/v1/crypto/market/universe
```

Universe history is forward-collected. Before the first stored snapshot, the dynamic eligibility
model returns no assets instead of assuming today's survivors existed historically. Eligibility
then removes warning markets and assets without sufficient trailing history or quote-volume, ranks
the remainder by liquidity, and limits the research universe independently of portfolio size.

Raw candle responses are content-addressed, so an unchanged response is stored once while a vendor
revision produces a distinct raw artifact. Normalized files remain under
`data/normalized/crypto/price/{PAIR}.parquet`.

## Persistent paper portfolios

Create and inspect an isolated paper account:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/crypto/paper/portfolios \
  -H 'Content-Type: application/json' \
  -d '{
    "portfolioId":"crypto-paper-001",
    "purpose":"PAPER_TRADING",
    "cashAsset":"KRW",
    "initialCash":"100000"
  }'

curl http://127.0.0.1:8000/api/v1/crypto/paper/portfolios/crypto-paper-001
```

SQLite stores decimal values as exact text and applies executions transactionally. Both
`intent_id` and paper `order_id` are unique, making retries idempotent across process restarts.
There is deliberately no HTTP order endpoint yet; execution remains an internal paper adapter until
precision rules, reservations, reconciliation, loss controls, and shadow-trading gates are added.

## Machine-learning automation

After synchronizing candle data and collecting universe snapshots, train and register a model:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/crypto/ml/train \
  -H 'Content-Type: application/json' \
  -d '{
    "pairs":["BTC/KRW","ETH/KRW","SOL/KRW"],
    "start":"2024-01-01T00:00:00Z",
    "end":"2026-01-01T00:00:00Z",
    "universeMode":"POINT_IN_TIME",
    "labelHorizonDays":7
  }'
```

Use `POST /api/v1/crypto/ml/predict` with `pairs` and `asOf` for ranked expected returns. Use
`POST /api/v1/crypto/ml/paper/jobs/run` with an existing `portfolioId`; it defaults to `dryRun:true`
and returns risk-checked target weights without placing orders. A daily cron/control-plane workflow
can run data sync and universe capture internally; prediction and this dry-run endpoint remain jobs
to register in the next runtime increment. Retraining is better scheduled weekly or monthly than on
every prediction.

For research without historical snapshots, `universeMode:STATIC_EXPLICIT` is available, but its
artifact is permanently marked with survivorship-bias risk and should not be mistaken for a
point-in-time result.

## Research lifecycle

Initialize the first immutable strategy champion once:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/crypto/research/strategies \
  -H 'Content-Type: application/json' \
  -d '{
    "name":"cross_sectional_momentum",
    "parameters":{
      "momentum_window_days":30,
      "maximum_positions":3,
      "maximum_asset_weight":"0.5",
      "rebalance_days":7
    }
  }'
```

Create a complete candidate configuration with `POST /api/v1/crypto/research/experiments`, then
call these endpoints in order:

```text
POST /api/v1/crypto/research/experiments/{id}/ready
POST /api/v1/crypto/research/experiments/{id}/run
POST /api/v1/crypto/research/experiments/{id}/promote
```

Promotion requires `{"approvedBy":"researcher identity"}`. A successful run only reaches
`CANDIDATE`; scheduled retraining never activates it. Execution continues to resolve the active
champion until that manual call succeeds. Python can schedule `ready/run` application use cases;
Spring queries experiment endpoints and presents approval as a separate dashboard action. Records are stored under
`experiments/crypto-lifecycle/` through a repository boundary.

Synchronize Binance daily candles (the interval is half-open, `[start, end)`):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bitcoin/market-data/sync \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","start":"2020-01-01T00:00:00Z","end":"2026-01-01T00:00:00Z"}'
```

Evaluate a feature using only data available by `asOf`:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bitcoin/research/features/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"symbol":"BTCUSDT","asOf":"2026-01-01T00:00:00Z","feature":"return_30d","label":"forward_return_30d","quantiles":5}'
```

Run a registered, synchronous research experiment (the application boundary can later return a job
identifier without moving research logic into FastAPI):

```bash
curl -X POST http://127.0.0.1:8000/api/v1/bitcoin/research/experiments \
  -H 'Content-Type: application/json' \
  -d '{
    "hypothesis":"btc_absorption_v1",
    "features":["btc_absorption_score"],
    "labels":["forward_return_30d","forward_return_90d","forward_return_180d"],
    "start":"2024-01-01T00:00:00Z",
    "end":"2026-01-01T00:00:00Z"
  }'
```

Experiments receive a deterministic ID derived from their canonical configuration and dataset
snapshot. Registry JSON under `experiments/output/` records the snapshot ID/hash, source list,
schema version, feature versions, parameters, evaluations, and year-by-year stability. Generated
experiment output is excluded from Git.

`FeatureStabilityAnalyzer` additionally supports rolling multi-year windows, deterministic
`BTC > MA200` / `BTC < MA200` partitions, and high/low realized-volatility partitions. These are
diagnostic groupings, not a Phase 3 regime model.

The Phase 1 feature set is returns over 1/7/30/90 days, price versus 20/50/200-day moving
averages, annualized realized volatility over 7/30/90 days, and three 20-day volume measures.
Labels are forward returns over 7/30/90/180 days and forward path maximum drawdown over 30/90 days.

The same application-independent research components can be used directly:

```python
from datetime import UTC, datetime

from investment.bitcoin.features.momentum import ReturnFeature
from investment.core.data.point_in_time import PointInTimeDataset
from investment.core.feature.pipeline import FeaturePipeline
from investment.core.labels.forward import ForwardLabelGenerator
from investment.core.research.evaluator import FeatureEvaluator

dataset = PointInTimeDataset.from_parquet(
    "data/normalized/bitcoin/price/BTCUSDT.parquet",
    as_of=datetime(2026, 1, 1, tzinfo=UTC),
)
features = FeaturePipeline([ReturnFeature(30)]).compute(dataset)
labels = ForwardLabelGenerator().compute(dataset)
research = features.join(labels, on="open_time")
result = FeatureEvaluator().evaluate(
    research, feature="return_30d", label="forward_return_30d", quantiles=5
)
```

Architecture decisions are recorded in [`docs/adr`](docs/adr).
The Spring event payload is documented in
[`docs/runtime-spring-event-contract.md`](docs/runtime-spring-event-contract.md).
