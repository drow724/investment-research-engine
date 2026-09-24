"""Lifecycle start and read-only inspection of frozen forward-observation evidence."""

import json
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from investment.crypto.application.dynamic_paper_rebalance import DynamicUniversePolicy
from investment.crypto.observation.domain import ObservationExperiment
from investment.crypto.observation.service import FrozenObservationService
from investment.interfaces.api.fastapi.observation.schemas import (
    ObservationExperimentResponse,
    StartObservationRequest,
)

router = APIRouter(prefix="/experiments", tags=["crypto-observation"])


@router.post("", response_model=ObservationExperimentResponse)
def start_observation(
    request: Request, command: StartObservationRequest
) -> ObservationExperimentResponse:
    suite_experiments = (
        *getattr(request.app.state, "v28_paper_experiments", []),
        *getattr(request.app.state, "v29_paper_experiments", []),
    )
    if command.portfolio_id in {f"{item}-paper" for item in suite_experiments}:
        raise HTTPException(status.HTTP_409_CONFLICT, "suite portfolio is owned by its experiment")
    if command.portfolio_id == getattr(
        request.app.state,
        "shadow_dynamic_paper_portfolio_id",
        None,
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "the configured shadow portfolio is owned by its frozen shadow experiment",
        )
    policy = getattr(request.app.state, "dynamic_policy", None)
    if not isinstance(policy, DynamicUniversePolicy):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "dynamic strategy policy is not initialized"
        )
    try:
        experiment = _service(request).start(
            command.experiment_id,
            command.portfolio_id,
            policy,
        )
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "paper portfolio not found") from error
    except ValueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return _experiment_response(experiment)


@router.get("")
def experiments(request: Request) -> tuple[dict[str, Any], ...]:
    return tuple(asdict(item) for item in _service(request).repository.experiments())


@router.get("/current")
def current(request: Request) -> dict[str, Any]:
    experiment_id = getattr(request.app.state, "observation_experiment_id", None)
    if not isinstance(experiment_id, str):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active observation configured")
    return asdict(_service(request).repository.experiment(experiment_id))


@router.get("/v28")
def v28_experiments(request: Request) -> tuple[dict[str, Any], ...]:
    """Cheap suite inventory; metrics/outcomes use the existing experiment endpoints."""
    return tuple(
        asdict(_service(request).repository.experiment(item))
        for item in getattr(request.app.state, "v28_paper_experiments", [])
    )


@router.get("/v29")
def v29_experiments(request: Request) -> tuple[dict[str, Any], ...]:
    """Cheap V2.9 suite inventory; detailed evidence uses the common endpoints."""
    return tuple(
        asdict(_service(request).repository.experiment(item))
        for item in getattr(request.app.state, "v29_paper_experiments", [])
    )


@router.get("/{experiment_id}", response_model=ObservationExperimentResponse)
def experiment(request: Request, experiment_id: str) -> ObservationExperimentResponse:
    try:
        return _experiment_response(_service(request).repository.experiment(experiment_id))
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "observation not found") from error


@router.get("/{experiment_id}/health")
def health(request: Request, experiment_id: str) -> dict[str, Any]:
    try:
        return _service(request).health(experiment_id)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "observation not found") from error


@router.get("/{experiment_id}/metrics")
def metrics(request: Request, experiment_id: str) -> dict[str, Any]:
    try:
        report = _service(request).report(experiment_id)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "observation not found") from error
    return {
        "experiment": report["experiment"],
        "performance": report["performance"],
        "trades": report["trades"],
    }


@router.get("/{experiment_id}/decisions")
def decisions(
    request: Request,
    experiment_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> tuple[dict[str, Any], ...]:
    try:
        values = _service(request).repository.snapshots(experiment_id)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "observation not found") from error
    return tuple(asdict(item) for item in values[offset : offset + limit])


@router.get("/{experiment_id}/report")
def report(request: Request, experiment_id: str) -> dict[str, Any]:
    try:
        return _service(request).report(experiment_id)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "observation not found") from error


@router.get("/{experiment_id}/market-contexts")
def market_contexts(
    request: Request,
    experiment_id: str,
    limit: int = Query(default=100, ge=1, le=1000),
) -> tuple[dict[str, Any], ...]:
    try:
        values = _service(request).repository.market_contexts(experiment_id)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "observation not found") from error
    return tuple(
        {
            "experimentId": item.experiment_id,
            "decisionId": item.decision_id,
            "strategyVersion": item.strategy_version,
            "configHash": item.config_hash,
            "decisionTime": item.decision_time,
            "context": json.loads(item.context_json),
        }
        for item in values[-limit:]
    )


@router.get("/{experiment_id}/diagnostics")
def diagnostics(request: Request, experiment_id: str) -> dict[str, Any]:
    try:
        return _service(request).diagnostics(experiment_id)
    except KeyError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "observation not found") from error


def _service(request: Request) -> FrozenObservationService:
    value = getattr(request.app.state, "observation_service", None)
    if not isinstance(value, FrozenObservationService):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "observation service is not initialized"
        )
    return value


def _experiment_response(value: ObservationExperiment) -> ObservationExperimentResponse:
    return ObservationExperimentResponse(
        experiment_id=value.experiment_id,
        portfolio_id=value.portfolio_id,
        strategy_version=value.strategy_version,
        config_hash=value.config_hash,
        started_at=value.started_at,
        planned_end_at=value.planned_end_at,
        status=value.status.value,
        starting_equity=value.starting_equity,
        completed_at=value.completed_at,
        interruption_reason=value.interruption_reason,
    )
