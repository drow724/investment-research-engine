"""Composition root for HTTP adapter dependencies."""

from decimal import Decimal

from fastapi import Depends, Request

from investment.application.services.experiment_service import ExperimentService
from investment.application.services.health_service import HealthService
from investment.application.services.market_data_service import MarketDataService
from investment.application.services.research_service import ResearchService
from investment.core.data.storage import (
    MetricParquetStorage,
    NormalizedParquetStorage,
    RawJsonStorage,
)
from investment.core.research.experiment import ExperimentRegistry
from investment.crypto.application.backtest_service import CryptoBacktestService
from investment.crypto.application.dynamic_paper_rebalance import (
    DynamicPaperRebalanceService,
    dynamic_policy_for_version,
)
from investment.crypto.application.intraday_service import (
    CryptoIntradayBacktestService,
    CryptoIntradayMarketDataService,
)
from investment.crypto.application.market_data_service import CryptoMarketDataService
from investment.crypto.application.market_overview_service import CryptoMarketOverviewService
from investment.crypto.application.ml_paper_job import MLPaperJobService
from investment.crypto.application.ml_service import CryptoMLService
from investment.crypto.application.paper_trading_service import PaperTradingService
from investment.crypto.application.research_lifecycle_service import (
    CryptoResearchLifecycleService,
)
from investment.crypto.application.universe_service import CryptoUniverseService
from investment.crypto.domain.timeframe import CandleTimeframe
from investment.crypto.infrastructure.market_data import ParquetCryptoMarketDataProvider
from investment.crypto.infrastructure.paper_exchange import (
    PaperExchangeGateway,
    PaperExchangeGatewayFactory,
)
from investment.crypto.infrastructure.research_repository import JsonResearchLifecycleRepository
from investment.crypto.infrastructure.sqlite_accounting import SqlitePaperPortfolioRepository
from investment.crypto.infrastructure.storage import (
    CryptoCandleParquetStorage,
    CryptoRawCandleStorage,
)
from investment.crypto.infrastructure.universe_storage import UniverseSnapshotStorage
from investment.crypto.infrastructure.upbit import UpbitPublicClient
from investment.crypto.ml.registry import CryptoModelRegistry
from investment.crypto.universe.liquidity import PointInTimeLiquidityUniverse
from investment.database.factory import paper_repository
from investment.interfaces.api.fastapi.settings import Settings
from investment.market_data.crypto.binance import (
    BinanceBitcoinPriceProvider,
    BinanceDailyNormalizer,
)


def get_settings() -> Settings:
    return Settings()


def get_health_service() -> HealthService:
    return HealthService()


def get_market_data_service(settings: Settings = Depends(get_settings)) -> MarketDataService:
    return MarketDataService(
        provider=BinanceBitcoinPriceProvider(base_url=settings.binance_base_url),
        normalizer=BinanceDailyNormalizer(),
        raw_storage=RawJsonStorage(settings.raw_data_root),
        normalized_storage=NormalizedParquetStorage(settings.normalized_data_root),
    )


def get_research_service(settings: Settings = Depends(get_settings)) -> ResearchService:
    return ResearchService(NormalizedParquetStorage(settings.normalized_data_root))


def get_experiment_service(settings: Settings = Depends(get_settings)) -> ExperimentService:
    return ExperimentService(
        NormalizedParquetStorage(settings.normalized_data_root),
        MetricParquetStorage(settings.normalized_data_root),
        ExperimentRegistry(settings.experiment_output_root),
    )


def get_crypto_backtest_service(
    settings: Settings = Depends(get_settings),
) -> CryptoBacktestService:
    history = UniverseSnapshotStorage(settings.crypto_universe_root).load()
    eligibility = PointInTimeLiquidityUniverse(
        history,
        lookback_days=settings.crypto_universe_lookback_days,
        minimum_average_quote_volume=settings.crypto_minimum_average_quote_volume,
        maximum_assets=settings.crypto_maximum_universe_assets,
    )
    return CryptoBacktestService(
        ParquetCryptoMarketDataProvider(settings.crypto_price_root), eligibility
    )


def get_crypto_market_data_service(
    settings: Settings = Depends(get_settings),
) -> CryptoMarketDataService:
    return CryptoMarketDataService(
        UpbitPublicClient(settings.upbit_base_url),
        CryptoRawCandleStorage(settings.crypto_raw_price_root),
        CryptoCandleParquetStorage(settings.crypto_price_root),
    )


def get_crypto_market_overview_service(
    settings: Settings = Depends(get_settings),
) -> CryptoMarketOverviewService:
    return CryptoMarketOverviewService(ParquetCryptoMarketDataProvider(settings.crypto_price_root))


def get_crypto_intraday_market_data_service(
    settings: Settings = Depends(get_settings),
) -> CryptoIntradayMarketDataService:
    return CryptoIntradayMarketDataService(
        UpbitPublicClient(settings.upbit_base_url),
        CryptoRawCandleStorage(settings.crypto_raw_price_root),
        CryptoCandleParquetStorage(settings.crypto_price_root),
    )


def get_crypto_intraday_backtest_service(
    settings: Settings = Depends(get_settings),
) -> CryptoIntradayBacktestService:
    return CryptoIntradayBacktestService(
        ParquetCryptoMarketDataProvider(settings.crypto_price_root, CandleTimeframe.MINUTE_15)
    )


def get_crypto_universe_service(
    settings: Settings = Depends(get_settings),
) -> CryptoUniverseService:
    return CryptoUniverseService(
        UpbitPublicClient(settings.upbit_base_url),
        UniverseSnapshotStorage(settings.crypto_universe_root),
    )


def get_paper_trading_service(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> PaperTradingService:
    return PaperTradingService(
        PaperExchangeGateway({}),
        _paper_repository(request, settings),
    )


def get_dynamic_paper_rebalance_service(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> DynamicPaperRebalanceService:
    history = UniverseSnapshotStorage(settings.crypto_universe_root).load()
    return DynamicPaperRebalanceService(
        history,
        ParquetCryptoMarketDataProvider(settings.crypto_price_root, CandleTimeframe.MINUTE_15),
        _paper_repository(request, settings),
        PaperExchangeGatewayFactory(
            fee_rate=(
                policy := dynamic_policy_for_version(settings.runtime_dynamic_strategy_version)
            ).exchange_fee_rate,
            slippage_rate=(
                policy.estimated_slippage_rate
                if settings.runtime_paper_execution_model_version == "paper-fill-v2"
                else Decimal("0")
            ),
            execution_model_version=settings.runtime_paper_execution_model_version,
        ),
        policy,
    )


def _paper_repository(
    request: Request, settings: Settings
) -> SqlitePaperPortfolioRepository:
    """Reuse the lifespan-owned ledger instead of initializing SQLite per request."""
    repository = getattr(request.app.state, "paper_repository", None)
    if isinstance(repository, SqlitePaperPortfolioRepository):
        return repository
    return paper_repository(
        settings.database_url,
        settings.database_schema,
        settings.crypto_paper_database,
    )


def get_crypto_ml_service(settings: Settings = Depends(get_settings)) -> CryptoMLService:
    history = UniverseSnapshotStorage(settings.crypto_universe_root).load()
    eligibility = PointInTimeLiquidityUniverse(
        history,
        lookback_days=settings.crypto_universe_lookback_days,
        minimum_average_quote_volume=settings.crypto_minimum_average_quote_volume,
        maximum_assets=settings.crypto_maximum_universe_assets,
    )
    return CryptoMLService(
        ParquetCryptoMarketDataProvider(settings.crypto_price_root),
        CryptoModelRegistry(settings.crypto_model_root),
        eligibility,
    )


def get_crypto_ml_paper_job_service(
    ml_service: CryptoMLService = Depends(get_crypto_ml_service),
    paper_service: PaperTradingService = Depends(get_paper_trading_service),
) -> MLPaperJobService:
    return MLPaperJobService(ml_service, paper_service)


def get_crypto_research_lifecycle_service(
    settings: Settings = Depends(get_settings),
) -> CryptoResearchLifecycleService:
    return CryptoResearchLifecycleService(
        ParquetCryptoMarketDataProvider(settings.crypto_price_root),
        JsonResearchLifecycleRepository(settings.crypto_research_lifecycle_root),
    )
