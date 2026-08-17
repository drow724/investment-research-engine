"""Point-in-time observation models kept outside the trading decision path."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from investment.core.domain.observation import require_utc


class ObservationStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    INVALIDATED = "INVALIDATED"


class DecisionAction(StrEnum):
    ENTRY = "ENTRY"
    HOLD = "HOLD"
    EXIT = "EXIT"
    REPLACE = "REPLACE"
    REJECTED_ENTRY = "REJECTED_ENTRY"
    REJECTED_REPLACEMENT = "REJECTED_REPLACEMENT"
    NO_ACTION = "NO_ACTION"


class OutcomeStatus(StrEnum):
    COMPLETED = "COMPLETED"
    MISSING_DATA = "MISSING_DATA"


@dataclass(frozen=True, slots=True)
class ObservationExperiment:
    experiment_id: str
    portfolio_id: str
    strategy_version: str
    config_hash: str
    started_at: datetime
    planned_end_at: datetime
    status: ObservationStatus
    starting_equity: float
    completed_at: datetime | None = None
    interruption_reason: str | None = None

    def __post_init__(self) -> None:
        require_utc(self.started_at, "started_at")
        require_utc(self.planned_end_at, "planned_end_at")
        if self.completed_at:
            require_utc(self.completed_at, "completed_at")
        if self.planned_end_at <= self.started_at:
            raise ValueError("planned end must follow experiment start")


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    snapshot_id: str
    experiment_id: str
    decision_id: str
    strategy_version: str
    config_hash: str
    decision_time: datetime
    asset: str
    market: str
    action: DecisionAction
    reason: str
    score: float | None
    rank: int | None
    eligible: bool
    selected: bool
    current_position: float
    target_position: float
    portfolio_cash: float
    portfolio_equity: float
    current_exposure: float
    target_exposure: float
    reference_price: float | None
    liquidity: float | None
    hour_of_day: int
    day_of_week: int
    momentum_1h: float | None = None
    momentum_4h: float | None = None
    momentum_24h: float | None = None
    volatility: float | None = None
    reference_at: datetime | None = None
    selected_rank: int | None = None

    def __post_init__(self) -> None:
        require_utc(self.decision_time, "decision_time")
        if self.reference_at is not None:
            require_utc(self.reference_at, "reference_at")


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    snapshot_id: str
    horizon_minutes: int
    target_at: datetime
    evaluated_at: datetime
    status: OutcomeStatus
    forward_return: float | None
    mfe: float | None
    mae: float | None

    def __post_init__(self) -> None:
        require_utc(self.target_at, "target_at")
        require_utc(self.evaluated_at, "evaluated_at")

    @property
    def horizon_hours(self) -> float:
        """Compatibility view for callers that still display horizons in hours."""
        return self.horizon_minutes / 60
