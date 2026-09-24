from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from investment.interfaces.api.fastapi.crypto.portfolio.routes import dynamic_rebalance
from investment.interfaces.api.fastapi.crypto.portfolio.schemas import DynamicRebalanceRequest
from investment.interfaces.api.fastapi.settings import Settings


def test_generic_manual_rebalance_rejects_shadow_portfolio() -> None:
    settings = Settings(
        _env_file=None,
        runtime_dynamic_strategy_version="dynamic-intraday-v2.3",
        runtime_dynamic_paper_portfolio_id="paper-v2.3-main",
        runtime_observation_experiment_id="observation-v2.3",
        runtime_shadow_dynamic_strategy_version="dynamic-intraday-v2.4",
        runtime_shadow_dynamic_paper_portfolio_id="paper-v2.4-shadow",
        runtime_shadow_observation_experiment_id="observation-v2.4-shadow",
    )
    service = Mock()

    with pytest.raises(HTTPException, match="V2.4 shadow portfolio") as captured:
        dynamic_rebalance(
            DynamicRebalanceRequest(
                portfolio_id="paper-v2.4-shadow",
                as_of=datetime(2026, 8, 24, tzinfo=UTC),
                execute=False,
            ),
            service,
            settings,
        )

    assert captured.value.status_code == 409
    service.run.assert_not_called()
