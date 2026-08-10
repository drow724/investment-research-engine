from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from investment.interfaces.api.fastapi.crypto.backtest.schemas import CryptoApiModel


class PeriodRequest(CryptoApiModel):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("period timestamps must be timezone-aware")
        return value


class InitializeStrategyRequest(CryptoApiModel):
    name: str = Field(min_length=1)
    parameters: dict[str, object]


class CreateExperimentRequest(CryptoApiModel):
    hypothesis: str = Field(min_length=1)
    strategy_name: str = Field(min_length=1)
    candidate_parameters: dict[str, object]
    pairs: tuple[str, ...] = Field(min_length=2)
    train_period: PeriodRequest
    validation_period: PeriodRequest
    validation_method: Literal["HOLDOUT", "WALK_FORWARD"] = "WALK_FORWARD"
    feature_changes: tuple[str, ...] = ()
    requested_metrics: tuple[str, ...] = (
        "sharpe_ratio",
        "maximum_drawdown",
        "fee_adjusted_return",
        "turnover",
    )


class ManualApprovalRequest(CryptoApiModel):
    approved_by: str = Field(min_length=1)


class StrategyVersionResponse(CryptoApiModel):
    version_id: str
    strategy_name: str
    version: int
    parameters: dict[str, object]
    status: str
    created_at: datetime
    source_experiment_id: str | None


class CandidateEvaluationResponse(CryptoApiModel):
    passed: bool
    policy_version: str
    reasons: tuple[str, ...]


class ExperimentResponse(CryptoApiModel):
    experiment_id: str
    hypothesis: str
    experiment_type: str
    base_strategy_version: str | None
    base_model_version: str | None
    candidate_strategy_version: str | None
    candidate_model_version: str | None
    parameters: dict[str, object]
    feature_changes: tuple[str, ...]
    train_period: PeriodRequest
    validation_period: PeriodRequest
    validation_method: str
    requested_metrics: tuple[str, ...]
    status: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    dataset_snapshot_id: str | None
    run_ids: tuple[str, ...]
    evaluation: CandidateEvaluationResponse | None
    approved_by: str | None


class RunExperimentResponse(CryptoApiModel):
    experiment: ExperimentResponse
    champion_run_id: str
    challenger_run_id: str
    validation_run_id: str
