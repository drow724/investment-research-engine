from fastapi import APIRouter, Depends, HTTPException, status
from httpx import HTTPError

from investment.crypto.application.market_data_service import CryptoMarketDataService
from investment.crypto.application.universe_service import CryptoUniverseService
from investment.crypto.domain.universe import UniverseSnapshot
from investment.interfaces.api.fastapi.crypto.market.schemas import (
    CryptoMarketSyncRequest,
    CryptoMarketSyncResponse,
    PairSyncResponse,
    UniverseMemberResponse,
    UniverseSnapshotResponse,
)
from investment.interfaces.api.fastapi.dependencies import (
    get_crypto_market_data_service,
    get_crypto_universe_service,
)

router = APIRouter(prefix="/crypto/market", tags=["crypto-market"])


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
