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


class DerivativesSnapshotResponse(CryptoApiModel):
    snapshot_id: str
    symbol: str
    observed_at: datetime
    available_at: datetime
    mark_price: str
    index_price: str
    open_interest_usd: str
    funding_rate: str
    basis_rate: str | None
    mark_index_basis_rate: str
    global_long_short_ratio: str
    top_position_long_short_ratio: str
    taker_buy_sell_ratio: str
    coinbase_price_usd: str | None
    coinbase_premium_rate: str | None
    coinbase_observed_at: datetime | None
    missing_fields: tuple[str, ...]
    source: str


class MarketStreamStatusResponse(CryptoApiModel):
    stream_name: str
    state: str
    updated_at: datetime
    connected_since: datetime | None
    last_message_at: datetime | None
    last_error: str | None


class LiquidationEventResponse(CryptoApiModel):
    event_id: str
    symbol: str
    event_time: datetime
    trade_time: datetime
    position: str
    price: str
    quantity: str
    notional_usd: str
    source: str


class SqueezeSignalResponse(CryptoApiModel):
    snapshot_id: str
    symbol: str
    as_of: datetime
    state: str
    fuel_score: float | None
    ignition_score: float | None
    open_interest_change_15m: float | None
    open_interest_change_1h: float | None
    futures_price_change_15m: float | None
    futures_price_change_1h: float | None
    spot_return_15m: float | None
    spot_volume_ratio: float | None
    spot_breakout: bool | None
    short_liquidation_usd_15m: float | None
    liquidation_confirmed: bool
    evidence: tuple[str, ...]
    basis_input_rate: str | None
    basis_input_source: str | None
    feature_version: str


class SqueezeObservationResponse(CryptoApiModel):
    snapshot: DerivativesSnapshotResponse
    signal: SqueezeSignalResponse
    streams: tuple[MarketStreamStatusResponse, ...]


class CrowdingSignalResponse(CryptoApiModel):
    snapshot_id: str
    symbol: str
    as_of: datetime
    state: str
    dominant_side: str
    long_crowding_score: float | None
    short_crowding_score: float | None
    crowding_intensity: float | None
    bullish_unwind_score: float | None
    bearish_unwind_score: float | None
    confidence: float
    long_liquidation_usd_15m: float | None
    short_liquidation_usd_15m: float | None
    liquidation_confirmed: bool
    evidence: tuple[str, ...]
    feature_version: str
