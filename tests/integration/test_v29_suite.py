from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

from fastapi.testclient import TestClient

from investment.crypto.application.dynamic_paper_rebalance import DynamicPaperRebalanceService
from investment.crypto.observation.domain import ObservationStatus
from investment.crypto.observation.service import FrozenObservationService
from investment.interfaces.api.fastapi.main import create_app


def test_v29_suite_runs_four_isolated_paper_lanes_and_stops_at_deadline(monkeypatch, tmp_path):
    monkeypatch.setenv("INVESTMENT_RUNTIME_V28_PAPER_EXPERIMENT_PREFIX", "")
    monkeypatch.setenv(
        "INVESTMENT_RUNTIME_V29_PAPER_EXPERIMENT_PREFIX", "paper-v2.9-alpha-20260919"
    )
    monkeypatch.setenv("INVESTMENT_RUNTIME_PAPER_EXECUTION_MODEL_VERSION", "paper-fill-v2")
    monkeypatch.setenv("INVESTMENT_CRYPTO_PAPER_DATABASE", str(tmp_path / "paper.sqlite3"))
    monkeypatch.setenv(
        "INVESTMENT_CRYPTO_OBSERVATION_DATABASE", str(tmp_path / "observation.sqlite3")
    )
    monkeypatch.setenv(
        "INVESTMENT_CRYPTO_DERIVATIVES_DATABASE", str(tmp_path / "derivatives.sqlite3")
    )
    monkeypatch.setenv("INVESTMENT_RUNTIME_STATE_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("INVESTMENT_CRYPTO_STRATEGY_REVIEW_ROOT", str(tmp_path / "reviews"))
    calls = []

    def run(self, command):
        calls.append((command, self.policy))
        return Mock()

    monkeypatch.setattr(DynamicPaperRebalanceService, "run", run)
    monkeypatch.setattr(FrozenObservationService, "capture", lambda *args: 0)
    with TestClient(create_app()) as client:
        inventory = client.get("/api/v1/experiments/v29").json()
        assert len(inventory) == 4
        assert len({row["portfolio_id"] for row in inventory}) == 4
        assert len({row["started_at"] for row in inventory}) == 1
        assert {row["starting_equity"] for row in inventory} == {1_000_000}

        response = client.post("/api/v1/jobs/crypto_dynamic_paper_rebalance/run")
        assert response.status_code == 200
        assert len(calls) == 4
        assert all(command.execute for command, _ in calls)
        assert len({command.portfolio_id for command, _ in calls}) == 4
        assert len({command.as_of for command, _ in calls}) == 1
        repository = client.app.state.observation_service.repository

        class AfterDeadline(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime.now(UTC) + timedelta(days=8)

        calls.clear()
        with monkeypatch.context() as clock_patch:
            clock_patch.setattr("investment.interfaces.api.fastapi.main.datetime", AfterDeadline)
            client.app.state.autonomous_runtime.scheduler.registry.resolve(
                "crypto_dynamic_paper_rebalance"
            )()
        assert calls == []
        assert all(
            repository.experiment(row["experiment_id"]).status is ObservationStatus.COMPLETED
            for row in inventory
        )

        response = client.post(
            "/api/v1/experiments",
            json={
                "experimentId": "hijack",
                "portfolioId": inventory[0]["portfolio_id"],
            },
        )
        assert response.status_code == 409
