"""Read-only inspection of frozen forward-observation evidence."""

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status

from investment.crypto.observation.service import FrozenObservationService

router = APIRouter(prefix="/experiments", tags=["crypto-observation"])


@router.get("")
def experiments(request: Request) -> tuple[dict[str, Any], ...]:
    return tuple(asdict(item) for item in _service(request).repository.experiments())


@router.get("/current")
def current(request: Request) -> dict[str, Any]:
    experiment_id = getattr(request.app.state, "observation_experiment_id", None)
    if not isinstance(experiment_id, str):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no active observation configured")
    return asdict(_service(request).repository.experiment(experiment_id))


@router.get("/{experiment_id}")
def experiment(request: Request, experiment_id: str) -> dict[str, Any]:
    try:
        return asdict(_service(request).repository.experiment(experiment_id))
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
