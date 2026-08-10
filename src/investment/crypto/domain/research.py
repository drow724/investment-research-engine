"""Immutable domain records for the crypto research lifecycle."""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum

from investment.crypto.backtest.models import PerformanceMetrics


class ExperimentStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANDIDATE = "CANDIDATE"
    PROMOTED = "PROMOTED"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class VersionStatus(StrEnum):
    CHALLENGER = "CHALLENGER"
    CANDIDATE = "CANDIDATE"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


class ExperimentType(StrEnum):
    STRATEGY = "STRATEGY"
    MODEL = "MODEL"
    STRATEGY_AND_MODEL = "STRATEGY_AND_MODEL"


class ValidationMethod(StrEnum):
    HOLDOUT = "HOLDOUT"
    WALK_FORWARD = "WALK_FORWARD"


@dataclass(frozen=True, slots=True)
class ResearchPeriod:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("research period requires aware, increasing timestamps")


@dataclass(frozen=True, slots=True)
class StrategyVersion:
    version_id: str
    strategy_name: str
    version: int
    parameters_json: str
    status: VersionStatus
    created_at: datetime
    source_experiment_id: str | None = None

    @property
    def parameters(self) -> dict[str, object]:
        value = json.loads(self.parameters_json)
        if not isinstance(value, dict):
            raise ValueError("strategy parameters must be a JSON object")
        return value

    def with_status(self, status: VersionStatus) -> "StrategyVersion":
        return replace(self, status=status)


@dataclass(frozen=True, slots=True)
class ModelVersion:
    version_id: str
    model_name: str
    version: int
    artifact_id: str
    status: VersionStatus
    created_at: datetime
    source_experiment_id: str


@dataclass(frozen=True, slots=True)
class DatasetSnapshot:
    snapshot_id: str
    data_start: datetime
    data_end: datetime
    universe_version: str
    feature_version: str
    source: str
    checksum: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class TrainingRun:
    run_id: str
    experiment_id: str
    dataset_snapshot_id: str
    model_version_id: str
    status: RunStatus
    started_at: datetime
    completed_at: datetime | None
    metrics_json: str
    error: str | None = None


@dataclass(frozen=True, slots=True)
class BacktestRun:
    run_id: str
    experiment_id: str
    strategy_version_id: str
    dataset_snapshot_id: str
    fee_rate: str
    slippage_rate: str
    status: RunStatus
    started_at: datetime
    completed_at: datetime | None
    metrics: PerformanceMetrics | None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ValidationRun:
    run_id: str
    experiment_id: str
    dataset_snapshot_id: str
    method: ValidationMethod
    status: RunStatus
    successful: bool
    checks: tuple[str, ...]
    started_at: datetime
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    passed: bool
    policy_version: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Experiment:
    experiment_id: str
    hypothesis: str
    experiment_type: ExperimentType
    base_strategy_version: str | None
    base_model_version: str | None
    candidate_strategy_version: str | None
    candidate_model_version: str | None
    parameters_json: str
    feature_changes: tuple[str, ...]
    train_period: ResearchPeriod
    validation_period: ResearchPeriod
    validation_method: ValidationMethod
    requested_metrics: tuple[str, ...]
    status: ExperimentStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    dataset_snapshot_id: str | None = None
    run_ids: tuple[str, ...] = ()
    evaluation: CandidateEvaluation | None = None
    approved_by: str | None = None

    def transition(self, target: ExperimentStatus, *, now: datetime | None = None) -> "Experiment":
        allowed = {
            ExperimentStatus.DRAFT: {ExperimentStatus.READY, ExperimentStatus.REJECTED},
            ExperimentStatus.READY: {ExperimentStatus.RUNNING, ExperimentStatus.REJECTED},
            ExperimentStatus.RUNNING: {ExperimentStatus.COMPLETED, ExperimentStatus.FAILED},
            ExperimentStatus.COMPLETED: {ExperimentStatus.CANDIDATE, ExperimentStatus.REJECTED},
            ExperimentStatus.CANDIDATE: {ExperimentStatus.PROMOTED, ExperimentStatus.REJECTED},
        }
        if target not in allowed.get(self.status, set()):
            raise ValueError(f"invalid experiment transition: {self.status} -> {target}")
        timestamp = now or datetime.now(UTC)
        return replace(
            self,
            status=target,
            started_at=timestamp if target is ExperimentStatus.RUNNING else self.started_at,
            completed_at=(
                timestamp
                if target in {ExperimentStatus.COMPLETED, ExperimentStatus.FAILED}
                else self.completed_at
            ),
        )


def canonical_parameters(parameters: dict[str, object]) -> str:
    return json.dumps(parameters, sort_keys=True, separators=(",", ":"))


def deterministic_id(prefix: str, payload: str) -> str:
    return f"{prefix}-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"
