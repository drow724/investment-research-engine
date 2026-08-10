from fastapi import APIRouter, Depends, HTTPException, status

from investment.crypto.application.ml_paper_job import (
    MLPaperJobService,
    RunMLPaperJobCommand,
)
from investment.crypto.application.ml_service import (
    CryptoMLService,
    PredictReturnsCommand,
    TrainModelCommand,
)
from investment.crypto.domain.market import MarketRegime
from investment.crypto.ml.dataset import UniverseMode
from investment.crypto.ml.model import ModelKind
from investment.crypto.ml.split import PurgedWalkForwardConfig, WalkForwardMode
from investment.interfaces.api.fastapi.crypto.ml.schemas import (
    AssetPredictionResponse,
    ModelMetadataResponse,
    PredictReturnsRequest,
    PredictReturnsResponse,
    RunPaperMLJobRequest,
    RunPaperMLJobResponse,
    TargetWeightResponse,
    TrainModelRequest,
    TrainModelResponse,
)
from investment.interfaces.api.fastapi.dependencies import (
    get_crypto_ml_paper_job_service,
    get_crypto_ml_service,
)

router = APIRouter(prefix="/crypto/ml", tags=["crypto-ml"])


@router.post("/train", response_model=TrainModelResponse)
def train_model(
    request: TrainModelRequest,
    service: CryptoMLService = Depends(get_crypto_ml_service),
) -> TrainModelResponse:
    try:
        split = request.split
        result = service.train(
            TrainModelCommand(
                request.pairs,
                request.start,
                request.end,
                UniverseMode(request.universe_mode),
                request.label_horizon_days,
                tuple(ModelKind(kind) for kind in request.model_kinds),
                PurgedWalkForwardConfig(
                    split.train_days,
                    split.validation_days,
                    split.test_days,
                    split.step_days,
                    split.purge_days,
                    WalkForwardMode(split.mode),
                ),
                request.top_k,
            )
        )
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    metadata = result.metadata
    return TrainModelResponse(
        model_id=metadata.model_id,
        model_kind=metadata.model_kind.value,
        feature_version=metadata.feature_version,
        universe_mode=metadata.universe_mode,
        label_horizon_days=metadata.label_horizon_days,
        dataset_rows=result.rows,
        comparison=metadata.comparison,
        limitations=metadata.limitations,
    )


@router.post("/predict", response_model=PredictReturnsResponse)
def predict_returns(
    request: PredictReturnsRequest,
    service: CryptoMLService = Depends(get_crypto_ml_service),
) -> PredictReturnsResponse:
    try:
        result = service.predict(
            PredictReturnsCommand(request.pairs, request.as_of, request.model_id)
        )
    except FileNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    return PredictReturnsResponse(
        model_id=result.model_id,
        as_of=result.as_of,
        horizon_days=result.horizon_days,
        universe_mode=result.universe_mode.value,
        predictions=tuple(
            AssetPredictionResponse(
                rank=index, asset=item.asset, expected_return=item.expected_return
            )
            for index, item in enumerate(result.predictions, start=1)
        ),
        limitations=result.limitations,
    )


@router.get("/models/latest", response_model=ModelMetadataResponse)
def latest_model(
    service: CryptoMLService = Depends(get_crypto_ml_service),
) -> ModelMetadataResponse:
    try:
        metadata = service.latest_model()
    except FileNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    return ModelMetadataResponse(
        **{
            "model_id": metadata.model_id,
            "model_kind": metadata.model_kind.value,
            "feature_version": metadata.feature_version,
            "features": metadata.features,
            "label_horizon_days": metadata.label_horizon_days,
            "universe_mode": metadata.universe_mode,
            "dataset_hash": metadata.dataset_hash,
            "trained_start": metadata.trained_start,
            "trained_end": metadata.trained_end,
            "created_at": metadata.created_at,
            "comparison": metadata.comparison,
            "limitations": metadata.limitations,
        }
    )


@router.post("/paper/jobs/run", response_model=RunPaperMLJobResponse)
def run_paper_job(
    request: RunPaperMLJobRequest,
    service: MLPaperJobService = Depends(get_crypto_ml_paper_job_service),
) -> RunPaperMLJobResponse:
    try:
        result = service.run(
            RunMLPaperJobCommand(
                request.portfolio_id,
                request.pairs,
                request.as_of,
                request.model_id,
                MarketRegime(request.regime),
                request.maximum_positions,
                request.minimum_expected_return,
                request.dry_run,
            )
        )
    except FileNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    return RunPaperMLJobResponse(
        model_id=result.model_id,
        portfolio_id=result.portfolio_id,
        as_of=result.as_of,
        dry_run=result.dry_run,
        risk_approved=result.risk_approved,
        risk_violations=result.risk_violations,
        targets=tuple(
            TargetWeightResponse(asset=item.asset, weight=str(item.weight))
            for item in result.targets
        ),
        cash_weight=str(result.cash_weight),
        execution_status=result.execution_status,
    )
