from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from investment.crypto.derivatives.crowding import BtcAlgorithmicCrowdingCalculator
from investment.crypto.derivatives.domain import DerivativesSnapshot
from investment.crypto.derivatives.service import (
    BtcSqueezeBasisProxySignalCalculator,
    BtcSqueezeSignalCalculator,
    DerivativesObservationService,
)
from investment.interfaces.api.fastapi.main import create_app


def test_derivatives_squeeze_api_reads_observation_without_affecting_trading(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("INVESTMENT_RUNTIME_STATE_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv(
        "INVESTMENT_CRYPTO_DERIVATIVES_DATABASE", str(tmp_path / "derivatives.sqlite3")
    )
    as_of = datetime(2026, 1, 1, 1, tzinfo=UTC)

    with TestClient(create_app()) as client:
        service = client.app.state.derivatives_observation_service
        assert isinstance(service, DerivativesObservationService)
        snapshots = tuple(
            _snapshot(
                as_of - timedelta(minutes=15 * (4 - index)),
                Decimal(100 + index),
            )
            for index in range(5)
        )
        for snapshot in snapshots:
            service.repository.save_snapshot(snapshot)
        proxy_signal = BtcSqueezeBasisProxySignalCalculator().calculate(snapshots)
        service.repository.save_signal(proxy_signal)
        service.repository.save_signal(BtcSqueezeSignalCalculator().calculate(snapshots))
        service.repository.save_crowding_signal(
            BtcAlgorithmicCrowdingCalculator().calculate(
                snapshots[-1],
                proxy_signal,
                long_liquidation_usd_15m=None,
                short_liquidation_usd_15m=None,
                liquidation_confirmed=False,
            )
        )

        latest = client.get("/api/v1/crypto/market/derivatives/squeeze")
        history = client.get("/api/v1/crypto/market/derivatives/squeeze/history")
        proxy_latest = client.get(
            "/api/v1/crypto/market/derivatives/squeeze"
            "?feature_version=btc-squeeze-v3-mark-index-basis-proxy"
        )
        proxy_history = client.get(
            "/api/v1/crypto/market/derivatives/squeeze/history"
            "?feature_version=btc-squeeze-v3-mark-index-basis-proxy"
        )
        crowding = client.get("/api/v1/crypto/market/derivatives/crowding")
        crowding_history = client.get("/api/v1/crypto/market/derivatives/crowding/history")
        configured = {item.name for item in client.app.state.autonomous_runtime.scheduler.configs}

    assert latest.status_code == 200
    assert latest.json()["snapshot"]["symbol"] == "BTCUSDT"
    assert latest.json()["signal"]["featureVersion"] == "btc-squeeze-v3-mark-index-basis-proxy"
    assert latest.json()["signal"]["liquidationConfirmed"] is False
    assert latest.json()["snapshot"]["coinbasePremiumRate"] is None
    assert latest.json()["snapshot"]["markIndexBasisRate"] == "0"
    assert latest.json()["signal"]["basisInputSource"] == "MARK_INDEX_PROXY"
    assert latest.json()["streams"] == []
    assert history.status_code == 200
    assert len(history.json()) == 1
    assert proxy_latest.status_code == 200
    assert (
        proxy_latest.json()["signal"]["featureVersion"]
        == "btc-squeeze-v3-mark-index-basis-proxy"
    )
    assert proxy_latest.json()["signal"]["basisInputSource"] == "MARK_INDEX_PROXY"
    assert proxy_history.status_code == 200
    assert len(proxy_history.json()) == 1
    assert proxy_history.json()[0]["featureVersion"] == "btc-squeeze-v3-mark-index-basis-proxy"
    assert crowding.status_code == 200
    assert crowding.json()["featureVersion"] == "btc-crowding-v1"
    assert crowding_history.status_code == 200
    assert len(crowding_history.json()) == 1
    assert "crypto_derivatives_snapshot" in configured


def _snapshot(as_of: datetime, open_interest: Decimal) -> DerivativesSnapshot:
    return DerivativesSnapshot(
        symbol="BTCUSDT",
        observed_at=as_of,
        available_at=as_of,
        mark_price=Decimal("100"),
        index_price=Decimal("100"),
        open_interest_usd=open_interest,
        funding_rate=Decimal("-0.0001"),
        basis_rate=Decimal("-0.001"),
        global_long_short_ratio=Decimal("0.8"),
        top_position_long_short_ratio=Decimal("0.85"),
        taker_buy_sell_ratio=Decimal("1.2"),
    )
