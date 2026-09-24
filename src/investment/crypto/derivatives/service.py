"""Capture and score BTC squeeze observations without affecting trading decisions."""

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.derivatives.binance import BinanceIntradayDerivativesClient
from investment.crypto.derivatives.crowding import BtcAlgorithmicCrowdingCalculator
from investment.crypto.derivatives.domain import (
    BasisSource,
    CrowdingSignal,
    DerivativesSnapshot,
    LiquidatedPosition,
    LiquidationEvent,
    MarketStreamStatus,
    SqueezeSignal,
    SqueezeState,
)
from investment.crypto.derivatives.repository import SqliteDerivativesObservationRepository
from investment.crypto.derivatives.streams import BINANCE_LIQUIDATION_STREAM
from investment.crypto.domain.market import MarketCandle
from investment.crypto.ports.market_data import CryptoMarketDataProvider

logger = logging.getLogger(__name__)


class BtcSqueezeSignalCalculator:
    feature_version = "btc-squeeze-v2-market-streams"
    use_mark_index_basis_proxy = False

    def calculate(
        self,
        snapshots: tuple[DerivativesSnapshot, ...],
        spot_candles: tuple[MarketCandle, ...] = (),
        *,
        short_liquidation_usd_15m: float | None = None,
        liquidation_confirmed: bool = False,
    ) -> SqueezeSignal:
        if not snapshots:
            raise ValueError("at least one derivatives snapshot is required")
        ordered = tuple(sorted(snapshots, key=lambda item: item.available_at))
        latest = ordered[-1]
        oi_15m = _change(ordered, "open_interest_usd", timedelta(minutes=15))
        oi_1h = _change(ordered, "open_interest_usd", timedelta(hours=1))
        price_15m = _change(ordered, "mark_price", timedelta(minutes=15))
        price_1h = _change(ordered, "mark_price", timedelta(hours=1))
        basis_input_rate, basis_input_source = self._basis_input(latest)
        fuel = self._fuel_score(latest, basis_input_rate, oi_1h, price_1h)
        spot_return, volume_ratio, breakout = self._spot_features(spot_candles, latest.available_at)
        ignition = self._ignition_score(latest, spot_return, volume_ratio, breakout)
        state, evidence = self._state(
            latest,
            fuel,
            ignition,
            oi_15m,
            price_15m,
            spot_return,
            breakout,
            short_liquidation_usd_15m,
            liquidation_confirmed,
        )
        evidence = self._with_basis_evidence(evidence, basis_input_source)
        return SqueezeSignal(
            snapshot_id=latest.snapshot_id,
            symbol=latest.symbol,
            as_of=latest.available_at,
            state=state,
            fuel_score=fuel,
            ignition_score=ignition,
            open_interest_change_15m=oi_15m,
            open_interest_change_1h=oi_1h,
            futures_price_change_15m=price_15m,
            futures_price_change_1h=price_1h,
            spot_return_15m=spot_return,
            spot_volume_ratio=volume_ratio,
            spot_breakout=breakout,
            short_liquidation_usd_15m=short_liquidation_usd_15m,
            liquidation_confirmed=liquidation_confirmed,
            evidence=evidence,
            basis_input_rate=(basis_input_rate if self.use_mark_index_basis_proxy else None),
            basis_input_source=(
                basis_input_source if self.use_mark_index_basis_proxy else None
            ),
            feature_version=self.feature_version,
        )

    def _basis_input(
        self, latest: DerivativesSnapshot
    ) -> tuple[Decimal | None, BasisSource | None]:
        if self.use_mark_index_basis_proxy:
            return latest.mark_index_basis_rate, BasisSource.MARK_INDEX_PROXY
        if latest.basis_rate is None:
            return None, None
        return latest.basis_rate, BasisSource.OFFICIAL

    def _with_basis_evidence(
        self,
        evidence: tuple[str, ...],
        basis_input_source: BasisSource | None,
    ) -> tuple[str, ...]:
        # V2 is frozen: keep its evidence payload unchanged.  New versions can
        # make provenance visible without mixing semantics under a V2 key.
        del basis_input_source
        return evidence

    def _fuel_score(
        self,
        latest: DerivativesSnapshot,
        basis_input_rate: Decimal | None,
        oi_change_1h: float | None,
        price_change_1h: float | None,
    ) -> float | None:
        if oi_change_1h is None or price_change_1h is None or basis_input_rate is None:
            return None
        oi_build = _clamp(oi_change_1h / 0.03)
        price_softness = _clamp((0.01 - price_change_1h) / 0.02)
        low_funding = _clamp((0.00005 - float(latest.funding_rate)) / 0.0002)
        backwardation = _clamp(-float(basis_input_rate) / 0.001)
        short_crowding = (
            _clamp((1 - float(latest.global_long_short_ratio)) / 0.25)
            + _clamp((1 - float(latest.top_position_long_short_ratio)) / 0.25)
        ) / 2
        score = (
            0.35 * oi_build
            + 0.20 * price_softness
            + 0.20 * low_funding
            + 0.15 * backwardation
            + 0.10 * short_crowding
        )
        return round(score if oi_change_1h > 0 else 0.0, 6)


    @staticmethod
    def _spot_features(
        candles: tuple[MarketCandle, ...], as_of: datetime
    ) -> tuple[float | None, float | None, bool | None]:
        available = tuple(
            sorted(
                (item for item in candles if item.available_at <= as_of),
                key=lambda item: item.open_time,
            )
        )
        if len(available) < 17:
            return None, None, None
        latest = available[-1]
        previous = available[-2]
        lookback = available[-17:-1]
        spot_return = float(latest.close / previous.close - 1)
        baseline_volume = median(float(item.volume) for item in lookback)
        volume_ratio = float(latest.volume) / baseline_volume if baseline_volume > 0 else None
        breakout = latest.close > max(item.high for item in lookback)
        return spot_return, volume_ratio, breakout

    @staticmethod
    def _ignition_score(
        latest: DerivativesSnapshot,
        spot_return: float | None,
        volume_ratio: float | None,
        breakout: bool | None,
    ) -> float | None:
        if spot_return is None or volume_ratio is None or breakout is None:
            return None
        score = (
            0.40 * float(breakout)
            + 0.25 * _clamp((volume_ratio - 1) / 1.5)
            + 0.20 * _clamp(spot_return / 0.01)
            + 0.15 * _clamp((float(latest.taker_buy_sell_ratio) - 1) / 0.30)
        )
        return round(score, 6)

    def _state(
        self,
        latest: DerivativesSnapshot,
        fuel: float | None,
        ignition: float | None,
        oi_change_15m: float | None,
        price_change_15m: float | None,
        spot_return: float | None,
        breakout: bool | None,
        short_liquidation_usd_15m: float | None,
        liquidation_confirmed: bool,
    ) -> tuple[SqueezeState, tuple[str, ...]]:
        stream_evidence = (
            "LIQUIDATION_STREAM_CONNECTED"
            if liquidation_confirmed
            else "LIQUIDATION_STREAM_NOT_CONTINUOUS_FOR_15M"
        )
        if fuel is None:
            insufficient_evidence = [stream_evidence]
            if "basis_rate" in latest.missing_fields and not self.use_mark_index_basis_proxy:
                insufficient_evidence.append("MISSING_DATA_BASIS_RATE")
            else:
                insufficient_evidence.append("WAITING_FOR_1H_DERIVATIVES_HISTORY")
            return SqueezeState.INSUFFICIENT_DATA, tuple(insufficient_evidence)
        evidence: list[str] = []
        if fuel >= 0.50:
            evidence.append("SHORT_FUEL_CONDITIONS_PRESENT")
        if ignition is not None and ignition >= 0.55:
            evidence.append("SPOT_IGNITION_CONDITIONS_PRESENT")
        active_proxy = (
            fuel >= 0.50
            and ignition is not None
            and ignition >= 0.55
            and oi_change_15m is not None
            and oi_change_15m <= -0.005
            and price_change_15m is not None
            and price_change_15m >= 0.004
            and spot_return is not None
            and spot_return > 0
            and breakout is True
        )
        if active_proxy:
            evidence.append("PRICE_UP_WITH_OI_UNWIND")
            if (
                liquidation_confirmed
                and short_liquidation_usd_15m is not None
                and short_liquidation_usd_15m > 0
            ):
                evidence.extend(("SHORT_LIQUIDATION_CONFIRMED", stream_evidence))
                return SqueezeState.ACTIVE, tuple(evidence)
            evidence.extend(("OI_UNWIND_PROXY_NOT_LIQUIDATION_CONFIRMED", stream_evidence))
            return SqueezeState.IGNITION, tuple(evidence)
        if fuel >= 0.50 and ignition is not None and ignition >= 0.55:
            evidence.append(stream_evidence)
            return SqueezeState.IGNITION, tuple(evidence)
        if fuel >= 0.50:
            evidence.extend(("WAITING_FOR_SPOT_IGNITION", stream_evidence))
            return SqueezeState.FUEL, tuple(evidence)
        return SqueezeState.NONE, ("NO_SQUEEZE_SETUP", stream_evidence)


class BtcSqueezeBasisProxySignalCalculator(BtcSqueezeSignalCalculator):
    """Parallel, source-aware signal using Mark–Index Basis as its sole input.

    It has a separate feature version so proxy-backed observations can never be
    mixed with the frozen official-Basis V2 series.
    """

    feature_version = "btc-squeeze-v3-mark-index-basis-proxy"
    use_mark_index_basis_proxy = True

    def _with_basis_evidence(
        self,
        evidence: tuple[str, ...],
        basis_input_source: BasisSource | None,
    ) -> tuple[str, ...]:
        assert basis_input_source is BasisSource.MARK_INDEX_PROXY
        return tuple(dict.fromkeys((*evidence, "BASIS_INPUT_MARK_INDEX_PROXY")))


class DerivativesObservationService:
    def __init__(
        self,
        client: BinanceIntradayDerivativesClient,
        repository: SqliteDerivativesObservationRepository,
        spot_market_data: CryptoMarketDataProvider,
        calculator: BtcSqueezeSignalCalculator | None = None,
        additional_calculators: tuple[BtcSqueezeSignalCalculator, ...] = (),
        crowding_calculator: BtcAlgorithmicCrowdingCalculator | None = None,
        crowding_source_feature_version: str = "btc-squeeze-v3-mark-index-basis-proxy",
    ) -> None:
        self.client = client
        self.repository = repository
        self.spot_market_data = spot_market_data
        self.calculator = calculator or BtcSqueezeSignalCalculator()
        feature_versions = tuple(
            item.feature_version for item in (self.calculator, *additional_calculators)
        )
        if len(set(feature_versions)) != len(feature_versions):
            raise ValueError("derivatives signal calculators must use unique feature versions")
        self._additional_calculators = additional_calculators
        self._crowding_calculator = crowding_calculator
        self._crowding_source_feature_version = crowding_source_feature_version

    def capture(self) -> SqueezeSignal:
        snapshot = self.client.fetch_snapshot()
        snapshot = self._with_coinbase_premium(snapshot)
        self.repository.save_snapshot(snapshot)
        history = self.repository.snapshots(snapshot.symbol, 1000)
        spot = self._spot_candles(snapshot)
        liquidation_window_start = snapshot.available_at - timedelta(minutes=15)
        liquidation_confirmed = self.repository.continuously_connected_since(
            BINANCE_LIQUIDATION_STREAM, liquidation_window_start
        )
        short_liquidation = None
        long_liquidation = None
        if liquidation_confirmed:
            short_liquidation = float(
                self.repository.liquidation_notional(
                    snapshot.symbol,
                    LiquidatedPosition.SHORT,
                    liquidation_window_start,
                    snapshot.available_at,
                )
            )
            long_liquidation = float(
                self.repository.liquidation_notional(
                    snapshot.symbol,
                    LiquidatedPosition.LONG,
                    liquidation_window_start,
                    snapshot.available_at,
                )
            )
        signal = self.calculator.calculate(
            history,
            spot,
            short_liquidation_usd_15m=short_liquidation,
            liquidation_confirmed=liquidation_confirmed,
        )
        self.repository.save_signal(signal)
        calculated_signals = {signal.feature_version: signal}
        for calculator in self._additional_calculators:
            try:
                additional_signal = calculator.calculate(
                    history,
                    spot,
                    short_liquidation_usd_15m=short_liquidation,
                    liquidation_confirmed=liquidation_confirmed,
                )
                self.repository.save_signal(additional_signal)
                calculated_signals[additional_signal.feature_version] = additional_signal
            except Exception:
                logger.exception(
                    "auxiliary derivatives signal failed; preserving primary signal "
                    "feature_version=%s",
                    calculator.feature_version,
                )
        if self._crowding_calculator is not None:
            source_signal = calculated_signals.get(self._crowding_source_feature_version)
            if source_signal is None:
                logger.error(
                    "crowding source signal unavailable feature_version=%s",
                    self._crowding_source_feature_version,
                )
            else:
                try:
                    crowding = self._crowding_calculator.calculate(
                        snapshot,
                        source_signal,
                        long_liquidation_usd_15m=long_liquidation,
                        short_liquidation_usd_15m=short_liquidation,
                        liquidation_confirmed=liquidation_confirmed,
                    )
                    self.repository.save_crowding_signal(crowding)
                except Exception:
                    logger.exception(
                        "auxiliary crowding signal failed; preserving squeeze signals "
                        "feature_version=%s",
                        self._crowding_calculator.feature_version,
                    )
        return signal

    def latest(
        self, symbol: str = "BTCUSDT", feature_version: str | None = None
    ) -> tuple[DerivativesSnapshot, SqueezeSignal]:
        observation = self.repository.observation_known_at(
            symbol,
            datetime.now(UTC),
            feature_version or self.calculator.feature_version,
        )
        if observation is None:
            raise KeyError(symbol)
        return observation

    def history(
        self, symbol: str = "BTCUSDT", limit: int = 100, feature_version: str | None = None
    ) -> tuple[SqueezeSignal, ...]:
        selected_version = feature_version or self.calculator.feature_version
        return self.repository.signals(symbol, limit, selected_version)

    def liquidations(
        self, symbol: str = "BTCUSDT", limit: int = 100
    ) -> tuple[LiquidationEvent, ...]:
        return self.repository.liquidations(symbol, limit)

    def latest_crowding(
        self, symbol: str = "BTCUSDT", feature_version: str = "btc-crowding-v1"
    ) -> CrowdingSignal:
        values = self.repository.crowding_signals(symbol, 1, feature_version)
        if not values:
            raise KeyError(symbol)
        return values[0]

    def crowding_history(
        self,
        symbol: str = "BTCUSDT",
        limit: int = 100,
        feature_version: str = "btc-crowding-v1",
    ) -> tuple[CrowdingSignal, ...]:
        return self.repository.crowding_signals(symbol, limit, feature_version)

    def stream_statuses(self) -> tuple[MarketStreamStatus, ...]:
        return self.repository.stream_statuses()

    def _with_coinbase_premium(self, snapshot: DerivativesSnapshot) -> DerivativesSnapshot:
        try:
            coinbase = self.repository.latest_coinbase_price("BTC-USD", snapshot.available_at)
        except KeyError:
            return snapshot
        if snapshot.available_at - coinbase.observed_at > timedelta(minutes=2):
            return snapshot
        premium = coinbase.price_usd / snapshot.index_price - 1
        return replace(
            snapshot,
            coinbase_price_usd=coinbase.price_usd,
            coinbase_premium_rate=premium,
            coinbase_observed_at=coinbase.observed_at,
            missing_fields=snapshot.missing_fields - {"coinbase_premium_rate"},
        )

    def _spot_candles(self, snapshot: DerivativesSnapshot) -> tuple[MarketCandle, ...]:
        try:
            bundle = self.spot_market_data.fetch(
                build_universe(("BTC/KRW",)),
                snapshot.available_at - timedelta(hours=8),
                snapshot.available_at,
            )
        except (FileNotFoundError, ValueError):
            return ()
        return bundle.candles["BTCKRW"]


def _change(values: tuple[DerivativesSnapshot, ...], field: str, delay: timedelta) -> float | None:
    latest = values[-1]
    target = latest.available_at - delay
    candidates = tuple(item for item in values[:-1] if item.available_at <= target)
    if not candidates:
        return None
    previous = candidates[-1]
    if target - previous.available_at > timedelta(minutes=20):
        return None
    current_value = float(getattr(latest, field))
    previous_value = float(getattr(previous, field))
    return current_value / previous_value - 1 if previous_value else None


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
