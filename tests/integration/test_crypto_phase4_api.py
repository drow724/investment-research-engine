from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.application.market_data_service import CryptoPairSyncResult
from investment.crypto.application.paper_trading_service import PaperTradingService
from investment.crypto.domain.universe import UniverseMember, UniverseSnapshot
from investment.crypto.infrastructure.paper_exchange import PaperExchangeGateway
from investment.crypto.infrastructure.sqlite_accounting import SqlitePaperPortfolioRepository
from investment.interfaces.api.fastapi.dependencies import (
    get_crypto_market_data_service,
    get_crypto_universe_service,
    get_paper_trading_service,
)
from investment.interfaces.api.fastapi.main import create_app


class FakeCryptoMarketDataService:
    def sync_pairs(
        self, pairs: tuple[str, ...], start: datetime, end: datetime
    ) -> tuple[CryptoPairSyncResult, ...]:
        return tuple(
            CryptoPairSyncResult(pair.replace("/", ""), 10, Path("raw"), Path("normalized"))
            for pair in pairs
        )


class FakeUniverseService:
    def __init__(self) -> None:
        observed = datetime(2025, 1, 1, tzinfo=UTC)
        pair = build_universe(("BTC/KRW",)).pairs[0]
        self.snapshot = UniverseSnapshot(
            observed,
            "upbit",
            (UniverseMember(pair, False, "upbit", observed),),
        )

    def capture_current(self) -> UniverseSnapshot:
        return self.snapshot

    def latest(self) -> UniverseSnapshot:
        return self.snapshot


def test_crypto_market_and_universe_contracts() -> None:
    app = create_app()
    app.dependency_overrides[get_crypto_market_data_service] = lambda: FakeCryptoMarketDataService()
    app.dependency_overrides[get_crypto_universe_service] = lambda: FakeUniverseService()
    client = TestClient(app)
    sync = client.post(
        "/api/v1/crypto/market/data/sync",
        json={
            "pairs": ["BTC/KRW", "ETH/KRW"],
            "start": "2024-01-01T00:00:00Z",
            "end": "2024-02-01T00:00:00Z",
        },
    )
    assert sync.status_code == 200
    assert sync.json()["pairs"][0] == {"pair": "BTCKRW", "rows": 10}
    captured = client.post("/api/v1/crypto/market/universe/snapshots")
    assert captured.status_code == 200
    assert captured.json()["members"] == [{"pair": "BTCKRW", "warning": False}]
    assert client.get("/api/v1/crypto/market/universe").status_code == 200


def test_paper_portfolio_create_and_read_contract(tmp_path) -> None:
    service = PaperTradingService(
        PaperExchangeGateway({}),
        SqlitePaperPortfolioRepository(tmp_path / "paper.sqlite3"),
    )
    app = create_app()
    app.dependency_overrides[get_paper_trading_service] = lambda: service
    client = TestClient(app)
    created = client.post(
        "/api/v1/crypto/paper/portfolios",
        json={
            "portfolioId": "paper-api",
            "purpose": "PAPER_TRADING",
            "cashAsset": "KRW",
            "initialCash": str(Decimal("100000")),
        },
    )
    assert created.status_code == 200
    assert created.json()["cashBalance"] == "100000"
    loaded = client.get("/api/v1/crypto/paper/portfolios/paper-api")
    assert loaded.status_code == 200
    assert loaded.json()["portfolioId"] == "paper-api"
