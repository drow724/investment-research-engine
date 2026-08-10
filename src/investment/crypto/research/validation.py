"""Time-ordered validation abstractions."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

from investment.crypto.domain.research import ResearchPeriod


class ValidationStrategy(Protocol):
    name: str

    def validate_periods(
        self, train: ResearchPeriod, validation: ResearchPeriod
    ) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class HoldoutValidation:
    name: str = "HOLDOUT"

    def validate_periods(
        self, train: ResearchPeriod, validation: ResearchPeriod
    ) -> tuple[str, ...]:
        return () if train.end <= validation.start else ("TRAIN_OVERLAPS_VALIDATION",)


@dataclass(frozen=True, slots=True)
class WalkForwardValidation:
    purge_days: int = 1
    name: str = "WALK_FORWARD"

    def __post_init__(self) -> None:
        if self.purge_days <= 0:
            raise ValueError("purge_days must be positive")

    def validate_periods(
        self, train: ResearchPeriod, validation: ResearchPeriod
    ) -> tuple[str, ...]:
        minimum_start = train.end + timedelta(days=self.purge_days)
        return () if validation.start >= minimum_start else ("PURGE_GAP_VIOLATION",)
