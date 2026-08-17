"""15-minute data sync and personal-frequency backtest vertical slice."""

import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from httpx import HTTPError

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.backtest.engine import BacktestEngine
from investment.crypto.backtest.models import BacktestConfig, BacktestResult
from investment.crypto.domain.portfolio import PortfolioPurpose
from investment.crypto.domain.timeframe import CandleTimeframe
from investment.crypto.domain.universe import UniverseSnapshot
from investment.crypto.infrastructure.storage import (
    CryptoCandleParquetStorage,
    CryptoRawCandleStorage,
)
from investment.crypto.infrastructure.upbit import (
    UpbitMinuteCandleNormalizer,
    UpbitPublicClient,
)
from investment.crypto.portfolio.equal_weight import EqualWeightPortfolioConstructor
from investment.crypto.ports.market_data import CryptoMarketDataProvider
from investment.crypto.regime.intraday_btc import IntradayBtcRegimeModel
from investment.crypto.risk.engine import DeterministicRiskEngine, RiskPolicy
from investment.crypto.strategy.momentum import CrossSectionalMomentumStrategy


@dataclass(frozen=True, slots=True)
class IntradayPairSyncResult:
    pair: str
    timeframe: CandleTimeframe
    rows: int
    status: str = "COMPLETED"
    error: str | None = None


class CryptoIntradayMarketDataService:
    def __init__(
        self,
        client: UpbitPublicClient,
        raw_storage: CryptoRawCandleStorage,
        normalized_storage: CryptoCandleParquetStorage,
    ) -> None:
        self._client = client
        self._raw = raw_storage
        self._normalized = normalized_storage
        self._normalizer = UpbitMinuteCandleNormalizer()

    def sync_pairs(
        self,
        pair_symbols: tuple[str, ...],
        start: datetime,
        end: datetime,
        timeframe: CandleTimeframe = CandleTimeframe.MINUTE_15,
    ) -> tuple[IntradayPairSyncResult, ...]:
        universe = build_universe(pair_symbols)
        results: list[IntradayPairSyncResult] = []
        for pair in universe.pairs:
            try:
                batch = self._client.fetch_minute_candles(pair, start, end, timeframe)
                self._raw.save(batch)
                candles = self._normalizer.normalize(batch)
                self._normalized.save(
                    pair,
                    candles,
                    source=batch.source,
                    ingested_at=batch.ingested_at,
                    timeframe=timeframe,
                )
                results.append(IntradayPairSyncResult(pair.symbol, timeframe, len(candles)))
            except (HTTPError, ValueError) as error:
                results.append(
                    IntradayPairSyncResult(pair.symbol, timeframe, 0, "FAILED", str(error))
                )
        return tuple(results)

    def sync_liquid_universe(
        self,
        snapshot: UniverseSnapshot,
        start: datetime,
        end: datetime,
        *,
        maximum_assets: int = 50,
        timeframe: CandleTimeframe = CandleTimeframe.MINUTE_15,
        throttle_seconds: float = 0.12,
        bootstrap_lookback_hours: int = 10 * 24,
        minimum_history_bars: int = 7 * 24 * 4,
    ) -> tuple[IntradayPairSyncResult, ...]:
        eligible = {
            f"{member.pair.quote.symbol}-{member.pair.base.symbol}": member.pair
            for member in snapshot.members
            if member.pair.quote.symbol == "KRW" and not member.warning
        }
        ranked = self._client.rank_markets_by_quote_volume(tuple(eligible))
        pairs = tuple(
            f"{eligible[item.market].base.symbol}/KRW" for item in ranked[:maximum_assets]
        )
        results: list[IntradayPairSyncResult] = []
        for pair in pairs:
            trading_pair = build_universe((pair,)).pairs[0]
            effective_start = (
                start
                if self._normalized.row_count(trading_pair, timeframe) >= minimum_history_bars
                else min(start, end - timedelta(hours=bootstrap_lookback_hours))
            )
            results.extend(self.sync_pairs((pair,), effective_start, end, timeframe))
            if throttle_seconds:
                time.sleep(throttle_seconds)
        return tuple(results)


@dataclass(frozen=True, slots=True)
class IntradayBacktestCommand:
    pair_symbols: tuple[str, ...]
    start: datetime
    end: datetime
    initial_capital: Decimal = Decimal("1000000")
    signal_lookback_bars: int = 16
    rebalance_bars: int = 4
    maximum_positions: int = 3
    fee_rate: Decimal = Decimal("0.0005")
    slippage_rate: Decimal = Decimal("0.001")


class CryptoIntradayBacktestService:
    def __init__(self, provider: CryptoMarketDataProvider) -> None:
        self._provider = provider

    def run(self, command: IntradayBacktestCommand) -> BacktestResult:
        universe = build_universe(command.pair_symbols)
        warmup_bars = 16 * 42 + 1
        market_data = self._provider.fetch(
            universe,
            command.start - timedelta(minutes=15 * warmup_bars),
            command.end,
        )
        maximum_weight = Decimal("0.5")
        engine = BacktestEngine(
            IntradayBtcRegimeModel(),
            CrossSectionalMomentumStrategy(
                command.signal_lookback_bars,
                command.maximum_positions,
                lookback_unit="bars",
            ),
            EqualWeightPortfolioConstructor(command.maximum_positions, maximum_weight),
            DeterministicRiskEngine(
                RiskPolicy(
                    maximum_positions=command.maximum_positions,
                    maximum_asset_fraction=maximum_weight,
                )
            ),
        )
        return engine.run(
            market_data,
            BacktestConfig(
                command.start,
                command.end,
                command.initial_capital,
                command.rebalance_bars,
                command.fee_rate,
                command.slippage_rate,
                portfolio_id="crypto-intraday-paper",
                purpose=PortfolioPurpose.PAPER_TRADING,
                periods_per_year=96 * 365,
            ),
        )
