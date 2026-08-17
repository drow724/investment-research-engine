"""Small stdlib CLI adapter for observation operations."""

import json

from investment.crypto.application.dynamic_paper_rebalance import DynamicUniversePolicy
from investment.crypto.domain.timeframe import CandleTimeframe
from investment.crypto.infrastructure.market_data import ParquetCryptoMarketDataProvider
from investment.crypto.infrastructure.sqlite_accounting import SqlitePaperPortfolioRepository
from investment.crypto.observation.repository import SqliteObservationRepository
from investment.crypto.observation.service import FrozenObservationService
from investment.interfaces.api.fastapi.settings import Settings


def run_observation_command(action: str) -> None:
    settings = Settings()
    experiment_id = settings.runtime_observation_experiment_id
    portfolio_id = settings.runtime_dynamic_paper_portfolio_id
    if experiment_id is None or portfolio_id is None:
        raise SystemExit("configure observation experiment ID and Paper portfolio ID first")
    service = FrozenObservationService(
        SqliteObservationRepository(settings.crypto_observation_database),
        SqlitePaperPortfolioRepository(settings.crypto_paper_database),
        ParquetCryptoMarketDataProvider(settings.crypto_price_root, CandleTimeframe.MINUTE_15),
        settings.runtime_state_root,
    )
    payload: dict[str, object]
    if action == "start":
        value = service.start(experiment_id, portfolio_id, DynamicUniversePolicy())
        payload = {"experimentId": value.experiment_id, "status": value.status.value}
    elif action == "status":
        payload = service.health(experiment_id)
    elif action == "evaluate":
        payload = {"insertedOutcomes": service.evaluate_pending(experiment_id)}
    elif action == "report":
        payload = service.report(experiment_id)
    elif action == "stop":
        value = service.interrupt(
            experiment_id,
            "early safety failure: excessive drawdown and persistently negative "
            "selected-candidate forward returns",
        )
        payload = {
            "experimentId": value.experiment_id,
            "status": value.status.value,
            "completedAt": value.completed_at,
            "reason": value.interruption_reason,
        }
    else:
        raise SystemExit(f"unknown observation action: {action}")
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
