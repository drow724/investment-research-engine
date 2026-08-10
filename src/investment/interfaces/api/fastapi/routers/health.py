from fastapi import APIRouter, Depends

from investment.application.services.health_service import HealthService
from investment.interfaces.api.fastapi.dependencies import get_health_service
from investment.interfaces.api.fastapi.schemas.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(service: HealthService = Depends(get_health_service)) -> HealthResponse:
    return HealthResponse.model_validate(service.check(), from_attributes=True)
