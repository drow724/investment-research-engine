from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from httpx import HTTPError

from investment.crypto.application.intraday_service import CryptoIntradayMarketDataService
from investment.crypto.application.market_data_service import CryptoMarketDataService
from investment.crypto.application.market_overview_service import CryptoMarketOverviewService
from investment.crypto.application.universe_service import CryptoUniverseService
from investment.crypto.derivatives.domain import (
    CrowdingSignal,
    DerivativesSnapshot,
    LiquidationEvent,
    MarketStreamStatus,
    SqueezeSignal,
)
from investment.crypto.derivatives.service import DerivativesObservationService
from investment.crypto.domain.timeframe import CandleTimeframe
from investment.crypto.domain.universe import UniverseSnapshot
from investment.interfaces.api.fastapi.crypto.market.schemas import (
    CrowdingSignalResponse,
    CryptoMarketSyncRequest,
    CryptoMarketSyncResponse,
    DerivativesSnapshotResponse,
    IntradayMarketSyncRequest,
    IntradayMarketSyncResponse,
    IntradayPairSyncResponse,
    LatestMarketPriceResponse,
    LiquidationEventResponse,
    MarketOverviewResponse,
    MarketStreamStatusResponse,
    PairSyncResponse,
    SqueezeObservationResponse,
    SqueezeSignalResponse,
    UniverseMemberResponse,
    UniverseSnapshotResponse,
)
from investment.interfaces.api.fastapi.dependencies import (
    get_crypto_intraday_market_data_service,
    get_crypto_market_data_service,
    get_crypto_market_overview_service,
    get_crypto_universe_service,
)

router = APIRouter(prefix="/crypto/market", tags=["crypto-market"])


@router.get("/derivatives/squeeze", response_model=SqueezeObservationResponse)
def latest_squeeze_observation(
    request: Request, feature_version: str | None = Query(default=None, min_length=1)
) -> SqueezeObservationResponse:
    try:
        snapshot, signal = _derivatives_service(request).latest(feature_version=feature_version)
    except KeyError as error:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "derivatives observations are not available yet"
        ) from error
    return SqueezeObservationResponse(
        snapshot=_derivatives_snapshot_response(snapshot),
        signal=_squeeze_signal_response(signal),
        streams=tuple(
            _stream_status_response(value)
            for value in _derivatives_service(request).stream_statuses()
        ),
    )


@router.get("/derivatives/squeeze/history", response_model=tuple[SqueezeSignalResponse, ...])
def squeeze_observation_history(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    feature_version: str | None = Query(default=None, min_length=1),
) -> tuple[SqueezeSignalResponse, ...]:
    return tuple(
        _squeeze_signal_response(value)
        for value in _derivatives_service(request).history(
            limit=limit, feature_version=feature_version
        )
    )


@router.get("/derivatives/crowding", response_model=CrowdingSignalResponse)
def latest_crowding_observation(
    request: Request,
    feature_version: str = Query(default="btc-crowding-v1", min_length=1),
) -> CrowdingSignalResponse:
    try:
        value = _derivatives_service(request).latest_crowding(
            feature_version=feature_version
        )
    except KeyError as error:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "crowding observations are not available yet"
        ) from error
    return _crowding_signal_response(value)


@router.get(
    "/derivatives/crowding/history", response_model=tuple[CrowdingSignalResponse, ...]
)
def crowding_observation_history(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    feature_version: str = Query(default="btc-crowding-v1", min_length=1),
) -> tuple[CrowdingSignalResponse, ...]:
    return tuple(
        _crowding_signal_response(value)
        for value in _derivatives_service(request).crowding_history(
            limit=limit, feature_version=feature_version
        )
    )


@router.get("/derivatives/liquidations", response_model=tuple[LiquidationEventResponse, ...])
def liquidation_history(
    request: Request, limit: int = Query(default=100, ge=1, le=1000)
) -> tuple[LiquidationEventResponse, ...]:
    return tuple(
        _liquidation_response(value)
        for value in _derivatives_service(request).liquidations(limit=limit)
    )


@router.get("/derivatives/streams", response_model=tuple[MarketStreamStatusResponse, ...])
def derivatives_stream_statuses(
    request: Request,
) -> tuple[MarketStreamStatusResponse, ...]:
    return tuple(
        _stream_status_response(value) for value in _derivatives_service(request).stream_statuses()
    )


@router.post("/data/intraday/sync", response_model=IntradayMarketSyncResponse)
def sync_intraday_market_data(
    request: IntradayMarketSyncRequest,
    service: CryptoIntradayMarketDataService = Depends(get_crypto_intraday_market_data_service),
) -> IntradayMarketSyncResponse:
    try:
        results = service.sync_pairs(
            request.pairs,
            request.start,
            request.end,
            CandleTimeframe(request.timeframe),
        )
    except (ValueError, HTTPError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    return IntradayMarketSyncResponse(
        status=("PARTIAL" if any(item.status == "FAILED" for item in results) else "COMPLETED"),
        pairs=tuple(
            IntradayPairSyncResponse(
                pair=item.pair,
                timeframe=item.timeframe.value,
                rows=item.rows,
                status=item.status,
                error=item.error,
            )
            for item in results
        ),
    )


@router.get("/latest", response_model=MarketOverviewResponse)
def latest_market_prices(
    pairs: str = Query(default="BTC/KRW,ETH/KRW,SOL/KRW"),
    service: CryptoMarketOverviewService = Depends(get_crypto_market_overview_service),
) -> MarketOverviewResponse:
    pair_symbols = tuple(value.strip() for value in pairs.split(",") if value.strip())
    try:
        prices = service.latest(pair_symbols, datetime.now(UTC))
    except FileNotFoundError as error:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "normalized market data is unavailable"
        ) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    return MarketOverviewResponse(
        prices=tuple(
            LatestMarketPriceResponse(
                pair=item.pair,
                as_of=item.as_of,
                close=str(item.close),
                daily_change=item.daily_change,
                volume=str(item.volume),
            )
            for item in prices
        )
    )


@router.post("/data/sync", response_model=CryptoMarketSyncResponse)
def sync_market_data(
    request: CryptoMarketSyncRequest,
    service: CryptoMarketDataService = Depends(get_crypto_market_data_service),
) -> CryptoMarketSyncResponse:
    try:
        results = service.sync_pairs(request.pairs, request.start, request.end)
    except (ValueError, HTTPError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return CryptoMarketSyncResponse(
        pairs=tuple(PairSyncResponse(pair=item.pair, rows=item.rows) for item in results)
    )


@router.post("/universe/snapshots", response_model=UniverseSnapshotResponse)
def capture_universe(
    service: CryptoUniverseService = Depends(get_crypto_universe_service),
) -> UniverseSnapshotResponse:
    try:
        return _snapshot_response(service.capture_current())
    except (ValueError, HTTPError) as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error


@router.get("/universe", response_model=UniverseSnapshotResponse)
def latest_universe(
    service: CryptoUniverseService = Depends(get_crypto_universe_service),
) -> UniverseSnapshotResponse:
    try:
        return _snapshot_response(service.latest())
    except FileNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


def _snapshot_response(snapshot: UniverseSnapshot) -> UniverseSnapshotResponse:
    return UniverseSnapshotResponse(
        source=snapshot.source,
        observed_at=snapshot.observed_at,
        members=tuple(
            UniverseMemberResponse(pair=member.pair.symbol, warning=member.warning)
            for member in snapshot.members
        ),
    )


def _derivatives_service(request: Request) -> DerivativesObservationService:
    value = getattr(request.app.state, "derivatives_observation_service", None)
    if not isinstance(value, DerivativesObservationService):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "derivatives observation service is not initialized",
        )
    return value


def _derivatives_snapshot_response(
    value: DerivativesSnapshot,
) -> DerivativesSnapshotResponse:
    return DerivativesSnapshotResponse(
        snapshot_id=value.snapshot_id,
        symbol=value.symbol,
        observed_at=value.observed_at,
        available_at=value.available_at,
        mark_price=str(value.mark_price),
        index_price=str(value.index_price),
        open_interest_usd=str(value.open_interest_usd),
        funding_rate=str(value.funding_rate),
        basis_rate=None if value.basis_rate is None else str(value.basis_rate),
        mark_index_basis_rate=str(value.mark_index_basis_rate),
        global_long_short_ratio=str(value.global_long_short_ratio),
        top_position_long_short_ratio=str(value.top_position_long_short_ratio),
        taker_buy_sell_ratio=str(value.taker_buy_sell_ratio),
        coinbase_price_usd=(
            None if value.coinbase_price_usd is None else str(value.coinbase_price_usd)
        ),
        coinbase_premium_rate=(
            None if value.coinbase_premium_rate is None else str(value.coinbase_premium_rate)
        ),
        coinbase_observed_at=value.coinbase_observed_at,
        missing_fields=tuple(sorted(value.missing_fields)),
        source=value.source,
    )


def _squeeze_signal_response(value: SqueezeSignal) -> SqueezeSignalResponse:
    return SqueezeSignalResponse(
        snapshot_id=value.snapshot_id,
        symbol=value.symbol,
        as_of=value.as_of,
        state=value.state.value,
        fuel_score=value.fuel_score,
        ignition_score=value.ignition_score,
        open_interest_change_15m=value.open_interest_change_15m,
        open_interest_change_1h=value.open_interest_change_1h,
        futures_price_change_15m=value.futures_price_change_15m,
        futures_price_change_1h=value.futures_price_change_1h,
        spot_return_15m=value.spot_return_15m,
        spot_volume_ratio=value.spot_volume_ratio,
        spot_breakout=value.spot_breakout,
        short_liquidation_usd_15m=value.short_liquidation_usd_15m,
        liquidation_confirmed=value.liquidation_confirmed,
        evidence=value.evidence,
        basis_input_rate=(
            None if value.basis_input_rate is None else str(value.basis_input_rate)
        ),
        basis_input_source=(
            None if value.basis_input_source is None else value.basis_input_source.value
        ),
        feature_version=value.feature_version,
    )


def _crowding_signal_response(value: CrowdingSignal) -> CrowdingSignalResponse:
    return CrowdingSignalResponse(
        snapshot_id=value.snapshot_id,
        symbol=value.symbol,
        as_of=value.as_of,
        state=value.state.value,
        dominant_side=value.dominant_side.value,
        long_crowding_score=value.long_crowding_score,
        short_crowding_score=value.short_crowding_score,
        crowding_intensity=value.crowding_intensity,
        bullish_unwind_score=value.bullish_unwind_score,
        bearish_unwind_score=value.bearish_unwind_score,
        confidence=value.confidence,
        long_liquidation_usd_15m=value.long_liquidation_usd_15m,
        short_liquidation_usd_15m=value.short_liquidation_usd_15m,
        liquidation_confirmed=value.liquidation_confirmed,
        evidence=value.evidence,
        feature_version=value.feature_version,
    )


def _liquidation_response(value: LiquidationEvent) -> LiquidationEventResponse:
    return LiquidationEventResponse(
        event_id=value.event_id,
        symbol=value.symbol,
        event_time=value.event_time,
        trade_time=value.trade_time,
        position=value.position.value,
        price=str(value.price),
        quantity=str(value.quantity),
        notional_usd=str(value.notional_usd),
        source=value.source,
    )


def _stream_status_response(value: MarketStreamStatus) -> MarketStreamStatusResponse:
    return MarketStreamStatusResponse(
        stream_name=value.stream_name,
        state=value.state.value,
        updated_at=value.updated_at,
        connected_since=value.connected_since,
        last_message_at=value.last_message_at,
        last_error=value.last_error,
    )
