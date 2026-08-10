from datetime import timedelta
from decimal import Decimal

from investment.crypto.domain.market import MarketRegime, MarketRegimeResult
from investment.crypto.ports.strategy import StrategyContext
from investment.crypto.strategy.momentum import CrossSectionalMomentumStrategy
from tests.crypto_fixtures import crypto_bundle


def test_momentum_ranks_only_data_available_at_as_of() -> None:
    bundle = crypto_bundle(60)
    as_of = bundle.candles["BTCKRW"][40].open_time
    regime = MarketRegimeResult(MarketRegime.RISK_ON, as_of, "fixture", {})
    result = CrossSectionalMomentumStrategy(lookback_days=20, maximum_assets=2).generate(
        StrategyContext(as_of, bundle, regime)
    )
    assert [signal.asset.symbol for signal in result.signals] == ["SOL", "ETH"]
    assert all(signal.as_of == as_of for signal in result.signals)
    assert bundle.candles["BTCKRW"][40].available_at > as_of


def test_risk_off_produces_no_long_signals() -> None:
    bundle = crypto_bundle(60)
    as_of = bundle.candles["BTCKRW"][40].open_time + timedelta(days=1)
    regime = MarketRegimeResult(MarketRegime.RISK_OFF, as_of, "fixture", {})
    result = CrossSectionalMomentumStrategy().generate(StrategyContext(as_of, bundle, regime))
    assert result.signals == ()


def test_minimum_liquidity_filter_is_explicit() -> None:
    bundle = crypto_bundle(60)
    as_of = bundle.candles["BTCKRW"][-1].available_at
    regime = MarketRegimeResult(MarketRegime.RISK_ON, as_of, "fixture", {})
    result = CrossSectionalMomentumStrategy(
        minimum_average_quote_volume=Decimal("999999999999999999")
    ).generate(StrategyContext(as_of, bundle, regime))
    assert result.signals == ()


def test_strategy_respects_separate_universe_eligibility_result() -> None:
    bundle = crypto_bundle(60)
    as_of = bundle.candles["BTCKRW"][-1].available_at
    regime = MarketRegimeResult(MarketRegime.RISK_ON, as_of, "fixture", {})
    btc_only = (bundle.universe.pairs[0].base,)
    result = CrossSectionalMomentumStrategy(lookback_days=20).generate(
        StrategyContext(as_of, bundle, regime, btc_only)
    )
    assert [signal.asset.symbol for signal in result.signals] == ["BTC"]
