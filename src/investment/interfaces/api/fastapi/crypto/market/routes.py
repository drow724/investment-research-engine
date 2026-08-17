from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from httpx import HTTPError

from investment.crypto.application.intraday_service import CryptoIntradayMarketDataService
from investment.crypto.application.market_data_service import CryptoMarketDataService
from investment.crypto.application.market_overview_service import CryptoMarketOverviewService
from investment.crypto.application.universe_service import CryptoUniverseService
from investment.crypto.domain.timeframe import CandleTimeframe
from investment.crypto.domain.universe import UniverseSnapshot
from investment.interfaces.api.fastapi.crypto.market.schemas import (
    CryptoMarketSyncRequest,
    CryptoMarketSyncResponse,
    IntradayMarketSyncRequest,
    IntradayMarketSyncResponse,
    IntradayPairSyncResponse,
    LatestMarketPriceResponse,
    MarketOverviewResponse,
    PairSyncResponse,
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
