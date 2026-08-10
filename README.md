# Investment Research Engine

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

Interactive docs are at `http://127.0.0.1:8000/api/v1/docs` and the stable schema is at
`/api/v1/openapi.json`.

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
should call data sync, universe snapshot collection, prediction, and this dry-run endpoint in order;
retraining is better scheduled weekly or monthly than on every prediction.

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
champion until that manual call succeeds. Spring can schedule `ready/run` and query experiment
endpoints, while approval remains a separate dashboard action. Records are stored under
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
