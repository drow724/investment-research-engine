"""FastAPI adapter hosted by the autonomous long-running Python engine."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI

from investment import __version__
from investment.crypto.application.dynamic_paper_rebalance import (
    DynamicPaperRebalanceCommand,
    DynamicPaperRebalanceService,
    DynamicUniversePolicy,
)
from investment.crypto.application.intraday_service import CryptoIntradayMarketDataService
from investment.crypto.application.market_data_service import CryptoMarketDataService
from investment.crypto.application.universe_service import CryptoUniverseService
from investment.crypto.domain.market import Asset, AssetKind
from investment.crypto.domain.portfolio import PortfolioPurpose, TradingPortfolio
from investment.crypto.domain.timeframe import CandleTimeframe
from investment.crypto.infrastructure.market_data import ParquetCryptoMarketDataProvider
from investment.crypto.infrastructure.paper_exchange import PaperExchangeGatewayFactory
from investment.crypto.infrastructure.sqlite_accounting import SqlitePaperPortfolioRepository
from investment.crypto.infrastructure.storage import (
    CryptoCandleParquetStorage,
    CryptoRawCandleStorage,
)
from investment.crypto.infrastructure.universe_storage import UniverseSnapshotStorage
from investment.crypto.infrastructure.upbit import UpbitPublicClient
from investment.crypto.observation.repository import SqliteObservationRepository
from investment.crypto.observation.service import FrozenObservationService
from investment.interfaces.api.fastapi.crypto.backtest import routes as crypto_backtests
from investment.interfaces.api.fastapi.crypto.market import routes as crypto_market
from investment.interfaces.api.fastapi.crypto.ml import routes as crypto_ml
from investment.interfaces.api.fastapi.crypto.portfolio import routes as crypto_portfolios
from investment.interfaces.api.fastapi.crypto.research import routes as crypto_research
from investment.interfaces.api.fastapi.dashboard import routes as dashboard_routes
from investment.interfaces.api.fastapi.observation import routes as observation_routes
from investment.interfaces.api.fastapi.routers import bitcoin, health
from investment.interfaces.api.fastapi.runtime import routes as runtime_routes
from investment.interfaces.api.fastapi.settings import Settings
from investment.runtime.application import (
    build_autonomous_runtime,
    parse_float_tuple,
    parse_string_tuple,
)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = Settings()
        client = UpbitPublicClient(settings.upbit_base_url)
        universe_storage = UniverseSnapshotStorage(settings.crypto_universe_root)
        paper_repository = SqlitePaperPortfolioRepository(settings.crypto_paper_database)
        policy = DynamicUniversePolicy()
        observation_service = FrozenObservationService(
            SqliteObservationRepository(settings.crypto_observation_database),
            paper_repository,
            ParquetCryptoMarketDataProvider(settings.crypto_price_root, CandleTimeframe.MINUTE_15),
            settings.runtime_state_root,
        )
        if settings.runtime_dynamic_paper_portfolio_id is not None:
            paper_repository.create(
                TradingPortfolio(
                    settings.runtime_dynamic_paper_portfolio_id,
                    PortfolioPurpose.PAPER_TRADING,
                    Asset("KRW", AssetKind.CASH),
                    settings.runtime_dynamic_paper_initial_cash,
                )
            )
        if (
            settings.runtime_observation_experiment_id is not None
            and settings.runtime_dynamic_paper_portfolio_id is not None
        ):
            observation_service.start(
                settings.runtime_observation_experiment_id,
                settings.runtime_dynamic_paper_portfolio_id,
                policy,
            )

        def dynamic_paper_rebalance() -> None:
            portfolio_id = settings.runtime_dynamic_paper_portfolio_id
            if portfolio_id is None:
                return
            service = DynamicPaperRebalanceService(
                universe_storage.load(),
                ParquetCryptoMarketDataProvider(
                    settings.crypto_price_root, CandleTimeframe.MINUTE_15
                ),
                paper_repository,
                PaperExchangeGatewayFactory(),
                policy,
            )
            result = service.run(
                DynamicPaperRebalanceCommand(
                    portfolio_id,
                    datetime.now(UTC),
                    # Execution is an adapter setting; observation capture also supports dry runs.
                    execute=settings.runtime_dynamic_paper_execute,
                )
            )
            if settings.runtime_observation_experiment_id is not None:
                observation_service.capture(
                    settings.runtime_observation_experiment_id, result, policy
                )

        def evaluate_observation_outcomes() -> None:
            experiment_id = settings.runtime_observation_experiment_id
            if experiment_id is not None:
                observation_service.evaluate_pending(experiment_id)

        runtime = build_autonomous_runtime(
            instance_id=settings.runtime_instance_id,
            state_root=str(settings.runtime_state_root),
            event_endpoint=settings.runtime_event_endpoint,
            event_retry_delays=parse_float_tuple(settings.runtime_event_retry_delays_json),
            event_timeout_seconds=settings.runtime_event_timeout_seconds,
            heartbeat_cron=settings.runtime_heartbeat_cron,
            universe_snapshot_cron=settings.runtime_universe_snapshot_cron,
            market_sync_cron=settings.runtime_market_sync_cron,
            market_sync_pairs=parse_string_tuple(settings.runtime_market_sync_pairs_json),
            market_sync_lookback_days=settings.runtime_market_sync_lookback_days,
            intraday_sync_cron=settings.runtime_intraday_sync_cron,
            intraday_sync_lookback_hours=settings.runtime_intraday_sync_lookback_hours,
            intraday_maximum_assets=settings.runtime_intraday_maximum_assets,
            dynamic_rebalance_cron=settings.runtime_dynamic_rebalance_cron,
            dynamic_rebalance_handler=(
                dynamic_paper_rebalance
                if settings.runtime_dynamic_paper_portfolio_id is not None
                else None
            ),
            dynamic_rebalance_lock_key=(
                f"portfolio:{settings.runtime_dynamic_paper_portfolio_id}:rebalance"
                if settings.runtime_dynamic_paper_portfolio_id is not None
                else None
            ),
            observation_outcome_cron=settings.runtime_observation_outcome_cron,
            observation_outcome_handler=(
                evaluate_observation_outcomes
                if settings.runtime_observation_experiment_id is not None
                else None
            ),
            observation_outcome_lock_key=(
                f"experiment:{settings.runtime_observation_experiment_id}:outcomes"
                if settings.runtime_observation_experiment_id is not None
                else None
            ),
            universe_service=CryptoUniverseService(client, universe_storage),
            market_service=CryptoMarketDataService(
                client,
                CryptoRawCandleStorage(settings.crypto_raw_price_root),
                CryptoCandleParquetStorage(settings.crypto_price_root),
            ),
            intraday_service=CryptoIntradayMarketDataService(
                client,
                CryptoRawCandleStorage(settings.crypto_raw_price_root),
                CryptoCandleParquetStorage(settings.crypto_price_root),
            ),
        )
        app.state.autonomous_runtime = runtime
        app.state.observation_service = observation_service
        app.state.observation_experiment_id = settings.runtime_observation_experiment_id
        runtime.start()
        try:
            yield
        finally:
            runtime.stop()

    app = FastAPI(
        title="Investment Research Engine",
        description="Point-in-time-safe investment research and crypto backtest operations.",
        version=__version__,
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        lifespan=lifespan,
    )
    app.include_router(health.router, prefix="/api/v1")
    app.include_router(bitcoin.router, prefix="/api/v1")
    app.include_router(crypto_backtests.router, prefix="/api/v1")
    app.include_router(crypto_market.router, prefix="/api/v1")
    app.include_router(crypto_ml.router, prefix="/api/v1")
    app.include_router(crypto_portfolios.router, prefix="/api/v1")
    app.include_router(crypto_research.router, prefix="/api/v1")
    app.include_router(runtime_routes.router, prefix="/api/v1")
    app.include_router(observation_routes.router, prefix="/api/v1")
    app.include_router(dashboard_routes.router)
    return app


app = create_app()
