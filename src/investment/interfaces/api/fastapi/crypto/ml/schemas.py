from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from investment.interfaces.api.fastapi.crypto.backtest.schemas import CryptoApiModel


class WalkForwardRequest(CryptoApiModel):
    train_days: int = Field(default=365, ge=30)
    validation_days: int = Field(default=90, ge=7)
    test_days: int = Field(default=90, ge=7)
    step_days: int = Field(default=90, ge=1)
    purge_days: int = Field(default=30, ge=1)
    mode: Literal["ROLLING", "EXPANDING"] = "ROLLING"


class TrainModelRequest(CryptoApiModel):
    pairs: tuple[str, ...] = Field(min_length=2)
    start: datetime
    end: datetime
    universe_mode: Literal["POINT_IN_TIME", "STATIC_EXPLICIT"] = "POINT_IN_TIME"
    label_horizon_days: int = Field(default=7, ge=1, le=90)
    model_kinds: tuple[Literal["RIDGE", "HIST_GRADIENT_BOOSTING"], ...] = (
        "RIDGE",
        "HIST_GRADIENT_BOOSTING",
    )
    split: WalkForwardRequest = WalkForwardRequest()
    top_k: int = Field(default=3, ge=1, le=30)

    @field_validator("start", "end")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("ML timestamps must be timezone-aware")
        return value


class TrainModelResponse(CryptoApiModel):
    status: Literal["COMPLETED"] = "COMPLETED"
    model_id: str
    model_kind: str
    feature_version: str
    universe_mode: str
    label_horizon_days: int
    dataset_rows: int
    comparison: dict[str, object]
    limitations: tuple[str, ...]


class PredictReturnsRequest(CryptoApiModel):
    pairs: tuple[str, ...] = Field(min_length=2)
    as_of: datetime
    model_id: str | None = None

    @field_validator("as_of")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("asOf must be timezone-aware")
        return value


class AssetPredictionResponse(CryptoApiModel):
    rank: int
    asset: str
    expected_return: float


class PredictReturnsResponse(CryptoApiModel):
    model_id: str
    as_of: datetime
    horizon_days: int
    universe_mode: str
    predictions: tuple[AssetPredictionResponse, ...]
    limitations: tuple[str, ...]


class ModelMetadataResponse(CryptoApiModel):
    model_id: str
    model_kind: str
    feature_version: str
    features: tuple[str, ...]
    label_horizon_days: int
    universe_mode: str
    dataset_hash: str
    trained_start: datetime
    trained_end: datetime
    created_at: datetime
    comparison: dict[str, object]
    limitations: tuple[str, ...]


class ActivateModelRequest(CryptoApiModel):
    approved_by: str = Field(min_length=1, max_length=200)


class ActivateModelResponse(CryptoApiModel):
    model_id: str
    status: Literal["ACTIVE"] = "ACTIVE"
    approved_by: str
    activated_at: datetime
    previous_model_id: str | None
    policy_version: str
    validation_ic: float
    test_ic: float


class RunPaperMLJobRequest(PredictReturnsRequest):
    portfolio_id: str = Field(min_length=1)
    regime: Literal["RISK_ON", "NEUTRAL", "RISK_OFF"] = "NEUTRAL"
    maximum_positions: int = Field(default=3, ge=1, le=30)
    minimum_expected_return: float = 0.0
    dry_run: bool = True


class TargetWeightResponse(CryptoApiModel):
    asset: str
    weight: str


class RunPaperMLJobResponse(CryptoApiModel):
    model_id: str
    portfolio_id: str
    as_of: datetime
    dry_run: bool
    risk_approved: bool
    risk_violations: tuple[str, ...]
    targets: tuple[TargetWeightResponse, ...]
    cash_weight: str
    execution_status: str
