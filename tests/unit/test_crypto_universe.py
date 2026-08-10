from datetime import timedelta
from decimal import Decimal

from investment.crypto.domain.universe import UniverseHistory, UniverseMember, UniverseSnapshot
from investment.crypto.universe.liquidity import PointInTimeLiquidityUniverse
from tests.crypto_fixtures import crypto_bundle


def test_universe_uses_only_latest_snapshot_known_at_as_of() -> None:
    bundle = crypto_bundle(90)
    pairs = bundle.universe.pairs
    first_time = bundle.candles["BTCKRW"][30].available_at
    second_time = bundle.candles["BTCKRW"][60].available_at
    first = UniverseSnapshot(
        first_time,
        "fixture",
        tuple(UniverseMember(pair, False, "fixture", first_time) for pair in pairs[:2]),
    )
    second = UniverseSnapshot(
        second_time,
        "fixture",
        tuple(
            UniverseMember(pair, pair.base.symbol == "ETH", "fixture", second_time)
            for pair in pairs
        ),
    )
    selector = PointInTimeLiquidityUniverse(
        UniverseHistory((first, second)),
        lookback_days=20,
        minimum_average_quote_volume=Decimal("0"),
        maximum_assets=3,
    )
    before_second = selector.evaluate(bundle, second_time - timedelta(seconds=1))
    after_second = selector.evaluate(bundle, second_time + timedelta(days=1))
    assert {pair.base.symbol for pair in before_second.eligible_pairs} == {"BTC", "ETH"}
    assert {pair.base.symbol for pair in after_second.eligible_pairs} == {"BTC", "SOL"}
    assert after_second.exclusions["ETHKRW"] == "MARKET_WARNING"


def test_no_snapshot_means_no_historical_membership_is_invented() -> None:
    bundle = crypto_bundle(40)
    as_of = bundle.candles["BTCKRW"][30].available_at
    result = PointInTimeLiquidityUniverse(
        UniverseHistory(()), minimum_average_quote_volume=Decimal("0")
    ).evaluate(bundle, as_of)
    assert result.eligible_pairs == ()
    assert set(result.exclusions.values()) == {"NO_MEMBERSHIP_SNAPSHOT"}
