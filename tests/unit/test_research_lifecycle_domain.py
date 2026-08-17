from datetime import UTC, datetime, timedelta

import pytest

from investment.crypto.domain.research import (
    Experiment,
    ExperimentStatus,
    ExperimentType,
    ResearchPeriod,
    ValidationMethod,
    canonical_parameters,
)
from investment.crypto.research.validation import WalkForwardValidation


def _experiment() -> Experiment:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    return Experiment(
        "exp-1",
        "hypothesis",
        ExperimentType.STRATEGY,
        "strategy:v1",
        None,
        "strategy:v2",
        None,
        canonical_parameters({"window": 7}),
        (),
        ResearchPeriod(now, now + timedelta(days=30)),
        ResearchPeriod(now + timedelta(days=31), now + timedelta(days=60)),
        ValidationMethod.WALK_FORWARD,
        ("sharpe_ratio",),
        ExperimentStatus.DRAFT,
        now,
    )


def test_experiment_state_machine_rejects_invalid_transitions() -> None:
    experiment = _experiment()
    with pytest.raises(ValueError, match="invalid experiment transition"):
        experiment.transition(ExperimentStatus.PROMOTED)
    running = experiment.transition(ExperimentStatus.READY).transition(ExperimentStatus.RUNNING)
    assert running.started_at is not None
    assert running.transition(ExperimentStatus.FAILED).status is ExperimentStatus.FAILED


def test_walk_forward_validation_never_allows_future_data_into_train() -> None:
    now = datetime(2025, 1, 1, tzinfo=UTC)
    train = ResearchPeriod(now, now + timedelta(days=30))
    safe = ResearchPeriod(now + timedelta(days=37), now + timedelta(days=60))
    overlapping = ResearchPeriod(now + timedelta(days=30), now + timedelta(days=60))

    assert WalkForwardValidation(7).validate_periods(train, safe) == ()
    assert WalkForwardValidation(7).validate_periods(train, overlapping) == ("PURGE_GAP_VIOLATION",)
