from datetime import datetime

from pydantic import Field, field_validator

from investment.interfaces.api.fastapi.crypto.backtest.schemas import CryptoApiModel


class CryptoMarketSyncRequest(CryptoApiModel):
    pairs: tuple[str, ...] = Field(min_length=1, max_length=30)
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("market data timestamps must be timezone-aware")
        return value


class PairSyncResponse(CryptoApiModel):
    pair: str
    rows: int


class CryptoMarketSyncResponse(CryptoApiModel):
    status: str = "COMPLETED"
    source: str = "upbit"
    pairs: tuple[PairSyncResponse, ...]


class IntradayMarketSyncRequest(CryptoMarketSyncRequest):
    timeframe: str = Field(default="15m", pattern=r"^(15m|60m|240m)$")


class IntradayPairSyncResponse(PairSyncResponse):
    timeframe: str
    status: str
    error: str | None = None


class IntradayMarketSyncResponse(CryptoApiModel):
    status: str = "COMPLETED"
    source: str = "upbit"
    pairs: tuple[IntradayPairSyncResponse, ...]


class UniverseMemberResponse(CryptoApiModel):
    pair: str
    warning: bool


class UniverseSnapshotResponse(CryptoApiModel):
    source: str
    observed_at: datetime
    members: tuple[UniverseMemberResponse, ...]


class LatestMarketPriceResponse(CryptoApiModel):
    pair: str
    as_of: datetime
    close: str
    daily_change: float | None
    volume: str


class MarketOverviewResponse(CryptoApiModel):
    prices: tuple[LatestMarketPriceResponse, ...]
