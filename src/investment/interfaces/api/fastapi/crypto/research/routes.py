import json
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, status

from investment.crypto.application.research_lifecycle_service import (
    CreateStrategyExperimentCommand,
    CryptoResearchLifecycleService,
)
from investment.crypto.domain.research import (
    Experiment,
    ResearchPeriod,
    StrategyVersion,
    ValidationMethod,
)
from investment.interfaces.api.fastapi.crypto.research.schemas import (
    CandidateEvaluationResponse,
    CreateExperimentRequest,
    ExperimentResponse,
    InitializeStrategyRequest,
    ManualApprovalRequest,
    PeriodRequest,
    RunExperimentResponse,
    StrategyVersionResponse,
)
from investment.interfaces.api.fastapi.dependencies import get_crypto_research_lifecycle_service

router = APIRouter(prefix="/crypto/research", tags=["crypto-research-lifecycle"])


@router.post("/strategies", response_model=StrategyVersionResponse)
def initialize_strategy(
    request: InitializeStrategyRequest,
    service: CryptoResearchLifecycleService = Depends(get_crypto_research_lifecycle_service),
) -> StrategyVersionResponse:
    try:
        return _strategy_response(service.initialize_strategy(request.name, request.parameters))
    except (FileExistsError, ValueError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error


@router.get("/strategies", response_model=tuple[StrategyVersionResponse, ...])
def list_strategies(
    service: CryptoResearchLifecycleService = Depends(get_crypto_research_lifecycle_service),
) -> tuple[StrategyVersionResponse, ...]:
    return tuple(_strategy_response(item) for item in service.list_strategies())


@router.post("/experiments", response_model=ExperimentResponse)
def create_experiment(
    request: CreateExperimentRequest,
    service: CryptoResearchLifecycleService = Depends(get_crypto_research_lifecycle_service),
) -> ExperimentResponse:
    try:
        experiment = service.create_experiment(
            CreateStrategyExperimentCommand(
                request.hypothesis,
                request.strategy_name,
                request.candidate_parameters,
                request.pairs,
                ResearchPeriod(request.train_period.start, request.train_period.end),
                ResearchPeriod(request.validation_period.start, request.validation_period.end),
                ValidationMethod(request.validation_method),
                request.feature_changes,
                request.requested_metrics,
            )
        )
    except FileNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (FileExistsError, ValueError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return _experiment_response(experiment)


@router.get("/experiments", response_model=tuple[ExperimentResponse, ...])
def list_experiments(
    service: CryptoResearchLifecycleService = Depends(get_crypto_research_lifecycle_service),
) -> tuple[ExperimentResponse, ...]:
    return tuple(_experiment_response(item) for item in service.list_experiments())


@router.get("/experiments/{experiment_id}", response_model=ExperimentResponse)
def get_experiment(
    experiment_id: str,
    service: CryptoResearchLifecycleService = Depends(get_crypto_research_lifecycle_service),
) -> ExperimentResponse:
    return _call(lambda: _experiment_response(service.get(experiment_id)))


@router.post("/experiments/{experiment_id}/ready", response_model=ExperimentResponse)
def mark_ready(
    experiment_id: str,
    service: CryptoResearchLifecycleService = Depends(get_crypto_research_lifecycle_service),
) -> ExperimentResponse:
    return _call(lambda: _experiment_response(service.mark_ready(experiment_id)))


@router.post("/experiments/{experiment_id}/run", response_model=RunExperimentResponse)
def run_experiment(
    experiment_id: str,
    service: CryptoResearchLifecycleService = Depends(get_crypto_research_lifecycle_service),
) -> RunExperimentResponse:
    try:
        result = service.run(experiment_id)
    except FileNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return RunExperimentResponse(
        experiment=_experiment_response(result.experiment),
        champion_run_id=result.champion_run.run_id,
        challenger_run_id=result.challenger_run.run_id,
        validation_run_id=result.validation_run.run_id,
    )


@router.post("/experiments/{experiment_id}/promote", response_model=ExperimentResponse)
def promote_candidate(
    experiment_id: str,
    request: ManualApprovalRequest,
    service: CryptoResearchLifecycleService = Depends(get_crypto_research_lifecycle_service),
) -> ExperimentResponse:
    return _call(lambda: _experiment_response(service.promote(experiment_id, request.approved_by)))


@router.post("/experiments/{experiment_id}/reject", response_model=ExperimentResponse)
def reject_candidate(
    experiment_id: str,
    service: CryptoResearchLifecycleService = Depends(get_crypto_research_lifecycle_service),
) -> ExperimentResponse:
    return _call(lambda: _experiment_response(service.reject(experiment_id)))


def _call(operation: Callable[[], ExperimentResponse]) -> ExperimentResponse:
    try:
        result = operation()
    except FileNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return result


def _strategy_response(version: StrategyVersion) -> StrategyVersionResponse:
    return StrategyVersionResponse(
        version_id=version.version_id,
        strategy_name=version.strategy_name,
        version=version.version,
        parameters=version.parameters,
        status=version.status.value,
        created_at=version.created_at,
        source_experiment_id=version.source_experiment_id,
    )


def _experiment_response(value: Experiment) -> ExperimentResponse:
    parameters = json.loads(value.parameters_json)
    if not isinstance(parameters, dict):
        raise ValueError("invalid experiment parameters")
    evaluation = (
        CandidateEvaluationResponse(
            passed=value.evaluation.passed,
            policy_version=value.evaluation.policy_version,
            reasons=value.evaluation.reasons,
        )
        if value.evaluation is not None
        else None
    )
    return ExperimentResponse(
        experiment_id=value.experiment_id,
        hypothesis=value.hypothesis,
        experiment_type=value.experiment_type.value,
        base_strategy_version=value.base_strategy_version,
        base_model_version=value.base_model_version,
        candidate_strategy_version=value.candidate_strategy_version,
        candidate_model_version=value.candidate_model_version,
        parameters=parameters,
        feature_changes=value.feature_changes,
        train_period=PeriodRequest(start=value.train_period.start, end=value.train_period.end),
        validation_period=PeriodRequest(
            start=value.validation_period.start, end=value.validation_period.end
        ),
        validation_method=value.validation_method.value,
        requested_metrics=value.requested_metrics,
        status=value.status.value,
        created_at=value.created_at,
        started_at=value.started_at,
        completed_at=value.completed_at,
        dataset_snapshot_id=value.dataset_snapshot_id,
        run_ids=value.run_ids,
        evaluation=evaluation,
        approved_by=value.approved_by,
    )
