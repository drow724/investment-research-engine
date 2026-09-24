from datetime import datetime

from pydantic import Field

from investment.interfaces.api.fastapi.crypto.backtest.schemas import CryptoApiModel


class StartObservationRequest(CryptoApiModel):
    experiment_id: str = Field(min_length=1, max_length=100)
    portfolio_id: str = Field(min_length=1, max_length=100)


class ObservationExperimentResponse(CryptoApiModel):
    experiment_id: str
    portfolio_id: str
    strategy_version: str
    config_hash: str
    started_at: datetime
    planned_end_at: datetime
    status: str
    starting_equity: float
    completed_at: datetime | None
    interruption_reason: str | None
