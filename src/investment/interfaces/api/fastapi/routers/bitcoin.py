from fastapi import APIRouter, Depends, HTTPException, status

from investment.application.services.experiment_service import ExperimentService
from investment.application.services.market_data_service import MarketDataService
from investment.application.services.research_service import ResearchService
from investment.interfaces.api.fastapi.dependencies import (
    get_experiment_service,
    get_market_data_service,
    get_research_service,
)
from investment.interfaces.api.fastapi.schemas.bitcoin import (
    ExperimentRequest,
    ExperimentResponse,
    FeatureEvaluationRequest,
    FeatureEvaluationResponse,
    MarketDataSyncRequest,
    MarketDataSyncResponse,
)

router = APIRouter(prefix="/bitcoin", tags=["bitcoin"])


@router.post("/research/experiments", response_model=ExperimentResponse)
def run_experiment(
    request: ExperimentRequest,
    service: ExperimentService = Depends(get_experiment_service),
) -> ExperimentResponse:
    try:
        run = service.run(
            hypothesis=request.hypothesis,
            features=request.features,
            labels=request.labels,
            start=request.start,
            end=request.end,
            symbol=request.symbol,
            quantiles=request.quantiles,
        )
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="required normalized market data is unavailable",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return ExperimentResponse(
        experiment_id=run.experiment_id,
        status=run.status,
        hypothesis=run.config.hypothesis,
        features=run.config.features,
        results=run.results,
        stability=run.stability,
        dataset_snapshot_id=run.dataset_snapshot.snapshot_id,
    )


@router.post("/market-data/sync", response_model=MarketDataSyncResponse)
def sync_market_data(
    request: MarketDataSyncRequest,
    service: MarketDataService = Depends(get_market_data_service),
) -> MarketDataSyncResponse:
    try:
        result = service.sync(request.symbol, request.start, request.end)
    except (ValueError, OSError) as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
    return MarketDataSyncResponse(symbol=result.symbol, rows=result.rows)


@router.post("/research/features/evaluate", response_model=FeatureEvaluationResponse)
def evaluate_feature(
    request: FeatureEvaluationRequest,
    service: ResearchService = Depends(get_research_service),
) -> FeatureEvaluationResponse:
    try:
        result = service.evaluate_feature(
            symbol=request.symbol,
            as_of=request.as_of,
            feature=request.feature,
            label=request.label,
            quantiles=request.quantiles,
        )
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="normalized market data is not available; synchronize it first",
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)
        ) from error
    return FeatureEvaluationResponse(
        as_of=request.as_of,
        feature=request.feature,
        label=request.label,
        result=result.to_dict(),
    )
