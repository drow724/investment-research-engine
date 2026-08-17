"""Application orchestration for the first crypto trading vertical slice."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from investment.crypto.backtest.engine import BacktestEngine
from investment.crypto.backtest.models import BacktestConfig, BacktestResult
from investment.crypto.domain.market import TradingPair, TradingUniverse, parse_trading_pair
from investment.crypto.domain.portfolio import PortfolioPurpose
from investment.crypto.portfolio.equal_weight import EqualWeightPortfolioConstructor
from investment.crypto.ports.market_data import CryptoMarketDataProvider
from investment.crypto.ports.universe import UniverseEligibilityModel
from investment.crypto.regime.btc_trend import BtcTrendRegimeModel
from investment.crypto.risk.engine import DeterministicRiskEngine, RiskPolicy
from investment.crypto.strategy.momentum import CrossSectionalMomentumStrategy


@dataclass(frozen=True, slots=True)
class MomentumBacktestCommand:
    pair_symbols: tuple[str, ...]
    start: datetime
    end: datetime
    initial_capital: Decimal
    purpose: PortfolioPurpose = PortfolioPurpose.PAPER_TRADING
    lookback_days: int = 30
    maximum_positions: int = 3
    rebalance_days: int = 7
    minimum_average_quote_volume: Decimal = Decimal("0")
    fee_rate: Decimal = Decimal("0.0005")
    slippage_rate: Decimal = Decimal("0.001")


class CryptoBacktestService:
    def __init__(
        self,
        market_data_provider: CryptoMarketDataProvider,
        universe_eligibility: UniverseEligibilityModel | None = None,
    ) -> None:
        self._market_data_provider = market_data_provider
        self._universe_eligibility = universe_eligibility

    def run_momentum(self, command: MomentumBacktestCommand) -> BacktestResult:
        universe = build_universe(command.pair_symbols)
        regime = BtcTrendRegimeModel()
        warmup_days = max(command.lookback_days + 1, regime.long_window + 1)
        market_data = self._market_data_provider.fetch(
            universe, command.start - timedelta(days=warmup_days * 2), command.end
        )
        strategy = CrossSectionalMomentumStrategy(
            lookback_days=command.lookback_days,
            maximum_assets=command.maximum_positions,
            minimum_average_quote_volume=command.minimum_average_quote_volume,
        )
        constructor = EqualWeightPortfolioConstructor(
            maximum_positions=command.maximum_positions,
            maximum_asset_weight=Decimal("0.5"),
        )
        risk = DeterministicRiskEngine(
            RiskPolicy(
                maximum_positions=command.maximum_positions,
                maximum_asset_fraction=Decimal("0.5"),
            )
        )
        engine = BacktestEngine(regime, strategy, constructor, risk, self._universe_eligibility)
        return engine.run(
            market_data,
            BacktestConfig(
                start=command.start,
                end=command.end,
                initial_capital=command.initial_capital,
                rebalance_days=command.rebalance_days,
                fee_rate=command.fee_rate,
                slippage_rate=command.slippage_rate,
                purpose=command.purpose,
            ),
        )


def build_universe(pair_symbols: tuple[str, ...]) -> TradingUniverse:
    pairs: list[TradingPair] = []
    for symbol in pair_symbols:
        pairs.append(parse_trading_pair(symbol))
    return TradingUniverse(tuple(pairs))
