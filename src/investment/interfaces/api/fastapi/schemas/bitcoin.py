from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=lambda value: _to_camel(value), populate_by_name=True)


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)


class MarketDataSyncRequest(CamelModel):
    symbol: str = Field(default="BTCUSDT", pattern=r"^[A-Z0-9]{5,20}$")
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class MarketDataSyncResponse(CamelModel):
    engine: Literal["bitcoin"] = "bitcoin"
    operation: Literal["market_data_sync"] = "market_data_sync"
    status: Literal["COMPLETED"] = "COMPLETED"
    symbol: str
    rows: int


class FeatureEvaluationRequest(CamelModel):
    symbol: str = Field(default="BTCUSDT", pattern=r"^[A-Z0-9]{5,20}$")
    as_of: datetime
    feature: str = "return_30d"
    label: str = "forward_return_30d"
    quantiles: int = Field(default=5, ge=2, le=20)

    @field_validator("as_of")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("asOf must be timezone-aware")
        return value


class FeatureEvaluationResponse(CamelModel):
    engine: Literal["bitcoin"] = "bitcoin"
    operation: Literal["feature_evaluation"] = "feature_evaluation"
    as_of: datetime
    feature: str
    label: str
    status: Literal["COMPLETED"] = "COMPLETED"
    result: dict[str, Any]


class ExperimentRequest(CamelModel):
    hypothesis: str
    features: tuple[str, ...]
    labels: tuple[str, ...]
    start: datetime
    end: datetime
    symbol: str = Field(default="BTCUSDT", pattern=r"^[A-Z0-9]{5,20}$")
    quantiles: int = Field(default=5, ge=2, le=20)

    @field_validator("start", "end")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("experiment timestamps must be timezone-aware")
        return value


class ExperimentResponse(CamelModel):
    experiment_id: str
    status: str
    hypothesis: str
    features: tuple[str, ...]
    results: dict[str, Any]
    stability: dict[str, Any]
    dataset_snapshot_id: str
