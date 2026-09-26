"""FastAPI adapter hosted by the autonomous long-running Python engine."""

import logging
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
from fastapi import FastAPI

from investment import __version__
from investment.crypto.application.dynamic_paper_rebalance import (
    V28_STRATEGY_VERSIONS,
    V29_STRATEGY_VERSIONS,
    DynamicPaperRebalanceCommand,
    DynamicPaperRebalanceService,
    DynamicUniversePolicy,
    dynamic_policy_for_version,
)
from investment.crypto.application.intraday_service import CryptoIntradayMarketDataService
from investment.crypto.application.market_data_service import CryptoMarketDataService
from investment.crypto.application.universe_service import CryptoUniverseService
from investment.crypto.derivatives.binance import BinanceIntradayDerivativesClient
from investment.crypto.derivatives.crowding import BtcAlgorithmicCrowdingCalculator
from investment.crypto.derivatives.overlay import PointInTimeBtcDerivativesOverlayProvider
from investment.crypto.derivatives.service import (
    BtcSqueezeBasisProxySignalCalculator,
    BtcSqueezeSignalCalculator,
    DerivativesObservationService,
)
from investment.crypto.derivatives.streams import DerivativesMarketStreams
from investment.crypto.domain.market import Asset, AssetKind
from investment.crypto.domain.portfolio import PortfolioPurpose, TradingPortfolio
from investment.crypto.domain.timeframe import CandleTimeframe
from investment.crypto.infrastructure.market_data import ParquetCryptoMarketDataProvider
from investment.crypto.infrastructure.paper_exchange import PaperExchangeGatewayFactory
from investment.crypto.infrastructure.storage import (
    CryptoCandleParquetStorage,
    CryptoRawCandleStorage,
)
from investment.crypto.infrastructure.universe_storage import UniverseSnapshotStorage
from investment.crypto.infrastructure.upbit import UpbitPublicClient
from investment.crypto.observation.service import FrozenObservationService
from investment.crypto.research.strategy_review import ReviewStage, StrategyReviewAnalyzer
from investment.crypto.research.strategy_review_policy import thresholds_for_strategy_version
from investment.crypto.strategy_review_workflow import StrategyReviewWorkflow
from investment.database.factory import (
    derivatives_repository,
    observation_repository,
    paper_repository,
)
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

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings = Settings()
        client = UpbitPublicClient(
            settings.upbit_base_url,
            persistent=True,
            request_timeout_seconds=5.0,
        )
        universe_storage = UniverseSnapshotStorage(settings.crypto_universe_root)
        paper_repository_store = paper_repository(
            settings.database_url,
            settings.database_schema,
            settings.crypto_paper_database,
        )
        policy = dynamic_policy_for_version(settings.runtime_dynamic_strategy_version)
        shadow_policy = None
        shadow_startup_error: str | None = None
        if (
            settings.runtime_shadow_dynamic_enabled
            and settings.runtime_shadow_dynamic_strategy_version is not None
        ):
            try:
                shadow_policy = dynamic_policy_for_version(
                    settings.runtime_shadow_dynamic_strategy_version
                )
            except ValueError as error:
                shadow_startup_error = f"shadow policy initialization failed: {error}"
                logger.exception(shadow_startup_error)
        drain_experiment_ids = parse_string_tuple(
            settings.runtime_observation_drain_experiment_ids_json
        )
        observation_service = FrozenObservationService(
            observation_repository(
                settings.database_url,
                settings.database_schema,
                settings.crypto_observation_database,
            ),
            paper_repository_store,
            ParquetCryptoMarketDataProvider(settings.crypto_price_root, CandleTimeframe.MINUTE_15),
            settings.runtime_state_root,
            {
                policy.strategy_version: "crypto_dynamic_paper_rebalance",
                **(
                    {shadow_policy.strategy_version: ("crypto_dynamic_paper_shadow_rebalance")}
                    if shadow_policy is not None
                    else {}
                ),
            },
        )
        strategy_review_analyzer = StrategyReviewAnalyzer(
            settings.crypto_observation_database,
            settings.crypto_paper_database,
            database_url=settings.database_url,
            database_schema=settings.database_schema,
        )

        def strategy_review_analyzer_for(
            lane_policy: DynamicUniversePolicy,
        ) -> StrategyReviewAnalyzer:
            """Bind review provenance to the frozen policy of one lane.

            V2.5 deliberately observes the V3 Mark–Index Basis signal rather
            than V2's official-Basis signal.  Reusing the default V2 threshold
            here would incorrectly mark every V2.5 context as a version
            mismatch, even when the point-in-time context is correct.
            """

            thresholds = thresholds_for_strategy_version(
                lane_policy.strategy_version,
                lane_policy.derivatives_feature_version,
                base=strategy_review_analyzer.thresholds,
            )
            if thresholds == strategy_review_analyzer.thresholds:
                return strategy_review_analyzer
            return StrategyReviewAnalyzer(
                settings.crypto_observation_database,
                settings.crypto_paper_database,
                thresholds,
                database_url=settings.database_url,
                database_schema=settings.database_schema,
            )

        strategy_review_workflow = StrategyReviewWorkflow(
            registry_root=settings.crypto_strategy_config_root,
            review_root=settings.crypto_strategy_review_root,
            observation_database=settings.crypto_observation_database,
            paper_database=settings.crypto_paper_database,
            database_url=settings.database_url,
            database_schema=settings.database_schema,
        )
        derivatives_overlay_provider: PointInTimeBtcDerivativesOverlayProvider | None = None
        derivatives_observation_service: DerivativesObservationService | None = None
        derivatives_market_streams: DerivativesMarketStreams | None = None
        derivatives_startup_error: str | None = None
        try:
            derivatives_repository_store = derivatives_repository(
                settings.database_url,
                settings.database_schema,
                settings.crypto_derivatives_database,
            )
            derivatives_overlay_provider = PointInTimeBtcDerivativesOverlayProvider(
                derivatives_repository_store
            )
            derivatives_observation_service = DerivativesObservationService(
                BinanceIntradayDerivativesClient(
                    base_url=settings.binance_futures_base_url,
                    mark_price_provider=derivatives_repository_store.mark_price_known_at,
                    official_basis_enabled=False,
                ),
                derivatives_repository_store,
                ParquetCryptoMarketDataProvider(
                    settings.crypto_price_root, CandleTimeframe.MINUTE_15
                ),
                calculator=BtcSqueezeBasisProxySignalCalculator(),
                additional_calculators=(BtcSqueezeSignalCalculator(),),
                crowding_calculator=BtcAlgorithmicCrowdingCalculator(),
            )
            derivatives_market_streams = (
                DerivativesMarketStreams(
                    derivatives_repository_store,
                    mark_price_url=settings.binance_mark_price_websocket_url,
                    binance_url=settings.binance_liquidation_websocket_url,
                    coinbase_url=settings.coinbase_market_websocket_url,
                )
                if settings.runtime_derivatives_websocket_enabled
                else None
            )
        except (OSError, ValueError, sqlite3.Error, psycopg.Error) as error:
            derivatives_startup_error = f"derivatives observation initialization failed: {error}"
            logger.exception(derivatives_startup_error)
        if settings.runtime_dynamic_paper_portfolio_id is not None:
                paper_repository_store.create(
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
        shadow_lane_ready = bool(
            shadow_startup_error is None
            and shadow_policy is not None
            and settings.runtime_shadow_observation_experiment_id is not None
            and settings.runtime_shadow_dynamic_paper_portfolio_id is not None
        )
        if shadow_lane_ready:
            try:
                shadow_portfolio_id = settings.runtime_shadow_dynamic_paper_portfolio_id
                shadow_experiment_id = settings.runtime_shadow_observation_experiment_id
                assert shadow_portfolio_id is not None
                assert shadow_experiment_id is not None
                assert shadow_policy is not None
                paper_repository_store.create(
                    TradingPortfolio(
                        shadow_portfolio_id,
                        PortfolioPurpose.PAPER_TRADING,
                        Asset("KRW", AssetKind.CASH),
                        settings.runtime_dynamic_paper_initial_cash,
                    )
                )
                observation_service.start(
                    shadow_experiment_id,
                    shadow_portfolio_id,
                    shadow_policy,
                )
            except (KeyError, OSError, ValueError, sqlite3.Error, psycopg.Error) as error:
                shadow_lane_ready = False
                shadow_startup_error = f"shadow lane initialization failed: {error}"
                logger.exception(shadow_startup_error)
        active_shadow_experiment_id = (
            settings.runtime_shadow_observation_experiment_id if shadow_lane_ready else None
        )
        if (
            settings.runtime_shadow_observation_experiment_id is not None
            and not shadow_lane_ready
            and shadow_startup_error is None
        ):
            shadow_startup_error = "shadow lane is configured but could not be initialized"
        v28_lanes: list[tuple[str, str, DynamicUniversePolicy]] = []
        if settings.runtime_v28_paper_experiment_prefix is not None:
            if settings.runtime_paper_execution_model_version != "paper-fill-v2":
                raise ValueError("V2.8 experiments require paper-fill-v2")
            suite_started_at = datetime.now(UTC)
            for version in V28_STRATEGY_VERSIONS:
                suffix = version.removeprefix("dynamic-intraday-v2.8-")
                experiment_id = f"{settings.runtime_v28_paper_experiment_prefix}-{suffix}"
                portfolio_id = f"{experiment_id}-paper"
                if portfolio_id in {
                    settings.runtime_dynamic_paper_portfolio_id,
                    settings.runtime_shadow_dynamic_paper_portfolio_id,
                } or experiment_id in {
                    settings.runtime_observation_experiment_id,
                    settings.runtime_shadow_observation_experiment_id,
                    *drain_experiment_ids,
                }:
                    raise ValueError("V2.8 experiment identity collides with another lane")
                lane_policy = dynamic_policy_for_version(version)
                paper_repository_store.create(
                    TradingPortfolio(
                        portfolio_id,
                        PortfolioPurpose.PAPER_TRADING,
                        Asset("KRW", AssetKind.CASH),
                        settings.runtime_dynamic_paper_initial_cash,
                    )
                )
                observation_service.start(
                    experiment_id,
                    portfolio_id,
                    lane_policy,
                    started_at=suite_started_at,
                )
                v28_lanes.append((portfolio_id, experiment_id, lane_policy))
        v29_lanes: list[tuple[str, str, DynamicUniversePolicy]] = []
        if settings.runtime_v29_paper_experiment_prefix is not None:
            if settings.runtime_paper_execution_model_version != "paper-fill-v2":
                raise ValueError("V2.9 experiments require paper-fill-v2")
            suite_started_at = datetime.now(UTC)
            occupied_experiment_ids = {
                settings.runtime_observation_experiment_id,
                settings.runtime_shadow_observation_experiment_id,
                *drain_experiment_ids,
                *(lane[1] for lane in v28_lanes),
            }
            occupied_portfolio_ids = {
                settings.runtime_dynamic_paper_portfolio_id,
                settings.runtime_shadow_dynamic_paper_portfolio_id,
                *(lane[0] for lane in v28_lanes),
            }
            for version in V29_STRATEGY_VERSIONS:
                suffix = version.removeprefix("dynamic-intraday-v2.9-")
                experiment_id = f"{settings.runtime_v29_paper_experiment_prefix}-{suffix}"
                portfolio_id = f"{experiment_id}-paper"
                if (
                    portfolio_id in occupied_portfolio_ids
                    or experiment_id in occupied_experiment_ids
                ):
                    raise ValueError("V2.9 experiment identity collides with another lane")
                lane_policy = dynamic_policy_for_version(version)
                paper_repository_store.create(
                    TradingPortfolio(
                        portfolio_id,
                        PortfolioPurpose.PAPER_TRADING,
                        Asset("KRW", AssetKind.CASH),
                        settings.runtime_dynamic_paper_initial_cash,
                    )
                )
                observation_service.start(
                    experiment_id,
                    portfolio_id,
                    lane_policy,
                    started_at=suite_started_at,
                )
                v29_lanes.append((portfolio_id, experiment_id, lane_policy))
                occupied_portfolio_ids.add(portfolio_id)
                occupied_experiment_ids.add(experiment_id)
        experimental_lanes = (*v28_lanes, *v29_lanes)
        active_experiment_ids = tuple(
            dict.fromkeys(
                experiment_id
                for experiment_id in (
                    settings.runtime_observation_experiment_id,
                    active_shadow_experiment_id,
                    *(lane[1] for lane in experimental_lanes),
                )
                if experiment_id is not None
            )
        )
        for drain_experiment_id in drain_experiment_ids:
            if drain_experiment_id in active_experiment_ids:
                continue
            try:
                observation_service.interrupt(
                    drain_experiment_id,
                    f"superseded by {settings.runtime_observation_experiment_id}",
                )
            except KeyError:
                continue

        def run_dynamic_lane(
            portfolio_id: str,
            experiment_id: str | None,
            lane_policy: DynamicUniversePolicy,
            *,
            execute: bool,
            as_of: datetime | None = None,
        ) -> None:
            decision_at = as_of or datetime.now(UTC)
            if experiment_id is not None and not observation_service.permits_execution(
                experiment_id, portfolio_id, lane_policy, now=datetime.now(UTC)
            ):
                return
            service = DynamicPaperRebalanceService(
                universe_storage.load(),
                ParquetCryptoMarketDataProvider(
                    settings.crypto_price_root, CandleTimeframe.MINUTE_15
                ),
                paper_repository_store,
                PaperExchangeGatewayFactory(
                    fee_rate=lane_policy.exchange_fee_rate,
                    slippage_rate=(
                        lane_policy.estimated_slippage_rate
                        if settings.runtime_paper_execution_model_version == "paper-fill-v2"
                        else Decimal("0")
                    ),
                    execution_model_version=settings.runtime_paper_execution_model_version,
                ),
                lane_policy,
                derivatives_overlay_provider,
            )
            result = service.run(
                DynamicPaperRebalanceCommand(
                    portfolio_id,
                    decision_at,
                    # Execution is an adapter setting; observation capture also supports dry runs.
                    execute=execute,
                    execution_deadline=(
                        observation_service.repository.experiment(experiment_id).planned_end_at
                        if experiment_id is not None
                        else None
                    ),
                )
            )
            if experiment_id is not None:
                observation_service.capture(experiment_id, result, lane_policy)

        def dynamic_paper_rebalance() -> None:
            decision_at = datetime.now(UTC)
            failures = []
            for lane_portfolio, lane_experiment, lane_policy in experimental_lanes:
                try:
                    run_dynamic_lane(
                        lane_portfolio,
                        lane_experiment,
                        lane_policy,
                        execute=True,
                        as_of=decision_at,
                    )
                except Exception as error:
                    logger.exception("Paper experiment lane failed: %s", lane_experiment)
                    failures.append(f"{lane_experiment}: {error}")
            portfolio_id = settings.runtime_dynamic_paper_portfolio_id
            if portfolio_id is not None:
                run_dynamic_lane(
                    portfolio_id,
                    settings.runtime_observation_experiment_id,
                    policy,
                    execute=settings.runtime_dynamic_paper_execute,
                )
            if failures:
                raise RuntimeError("; ".join(failures))

        def shadow_dynamic_paper_rebalance() -> None:
            portfolio_id = settings.runtime_shadow_dynamic_paper_portfolio_id
            if portfolio_id is not None and shadow_policy is not None and shadow_lane_ready:
                run_dynamic_lane(
                    portfolio_id,
                    active_shadow_experiment_id,
                    shadow_policy,
                    execute=False,
                )

        def evaluate_observation_outcomes() -> None:
            experiment_ids = tuple(dict.fromkeys((*active_experiment_ids, *drain_experiment_ids)))
            for experiment_id in experiment_ids:
                try:
                    observation_service.evaluate_pending(experiment_id)
                except KeyError:
                    continue

        def review_dynamic_strategy() -> None:
            failures = []
            review_targets: tuple[
                tuple[str | None, ReviewStage, DynamicUniversePolicy | None], ...
            ] = (
                # Preserve the established primary-Paper report identity.  Its
                # review stage does not evaluate a derivatives feature gate.
                (
                    settings.runtime_observation_experiment_id,
                    "PAPER",
                    (policy if policy.strategy_version == "dynamic-intraday-v2.7" else None),
                ),
                (
                    active_shadow_experiment_id,
                    "SHADOW_TO_PAPER",
                    shadow_policy if shadow_policy is not None else policy,
                ),
                *((lane[1], "PAPER", lane[2]) for lane in experimental_lanes),
            )
            for experiment_id, review_stage, lane_policy in review_targets:
                if experiment_id is None:
                    continue
                try:
                    analyzer = (
                        strategy_review_analyzer_for(lane_policy)
                        if lane_policy is not None
                        else strategy_review_analyzer
                    )
                    report = analyzer.analyze(
                        experiment_id,
                        review_stage=review_stage,
                    )
                    strategy_review_workflow.publish_analysis(report)
                except (KeyError, OSError, ValueError, sqlite3.Error, psycopg.Error) as error:
                    failures.append(f"{experiment_id}: {error}")
                    logger.exception("strategy review failed for %s", experiment_id)
            if failures:
                raise RuntimeError("; ".join(failures))

        def capture_derivatives_snapshot() -> None:
            if derivatives_observation_service is None:
                raise RuntimeError(
                    derivatives_startup_error or "derivatives observation is unavailable"
                )
            derivatives_observation_service.capture()

        def supervise_derivatives_streams() -> None:
            if derivatives_market_streams is not None and not derivatives_market_streams.is_running:
                derivatives_market_streams.start()

        runtime = build_autonomous_runtime(
            instance_id=settings.runtime_instance_id,
            state_root=str(settings.runtime_state_root),
            event_endpoint=settings.runtime_event_endpoint,
            event_retry_delays=parse_float_tuple(settings.runtime_event_retry_delays_json),
            event_timeout_seconds=settings.runtime_event_timeout_seconds,
            heartbeat_cron=settings.runtime_heartbeat_cron,
            runtime_supervision_handler=(
                supervise_derivatives_streams if derivatives_market_streams is not None else None
            ),
            universe_snapshot_cron=settings.runtime_universe_snapshot_cron,
            market_sync_cron=settings.runtime_market_sync_cron,
            market_sync_pairs=parse_string_tuple(settings.runtime_market_sync_pairs_json),
            market_sync_lookback_days=settings.runtime_market_sync_lookback_days,
            intraday_sync_cron=settings.runtime_intraday_sync_cron,
            intraday_sync_lookback_hours=settings.runtime_intraday_sync_lookback_hours,
            intraday_maximum_assets=settings.runtime_intraday_maximum_assets,
            intraday_sync_budget_seconds=settings.runtime_intraday_sync_budget_seconds,
            derivatives_snapshot_cron=settings.runtime_derivatives_snapshot_cron,
            derivatives_snapshot_handler=(
                capture_derivatives_snapshot
                if derivatives_observation_service is not None
                else None
            ),
            dynamic_rebalance_cron=settings.runtime_dynamic_rebalance_cron,
            dynamic_rebalance_handler=(
                dynamic_paper_rebalance
                if settings.runtime_dynamic_paper_portfolio_id is not None or experimental_lanes
                else None
            ),
            dynamic_rebalance_lock_key=(
                "paper:experiment-suite:rebalance"
                if experimental_lanes
                else f"portfolio:{settings.runtime_dynamic_paper_portfolio_id}:rebalance"
                if settings.runtime_dynamic_paper_portfolio_id is not None
                else None
            ),
            shadow_dynamic_rebalance_cron=settings.runtime_shadow_dynamic_rebalance_cron,
            shadow_dynamic_rebalance_handler=(
                shadow_dynamic_paper_rebalance if shadow_lane_ready else None
            ),
            shadow_dynamic_rebalance_lock_key=(
                f"portfolio:{settings.runtime_shadow_dynamic_paper_portfolio_id}:rebalance"
                if shadow_lane_ready
                and settings.runtime_shadow_dynamic_paper_portfolio_id is not None
                else None
            ),
            observation_outcome_cron=settings.runtime_observation_outcome_cron,
            observation_outcome_handler=(
                evaluate_observation_outcomes
                if active_experiment_ids or drain_experiment_ids
                else None
            ),
            observation_outcome_lock_key=(
                "database:crypto-observation:outcomes"
                if active_experiment_ids or drain_experiment_ids
                else None
            ),
            strategy_review_cron=settings.runtime_strategy_review_cron,
            strategy_review_handler=(review_dynamic_strategy if active_experiment_ids else None),
            strategy_review_lock_key=("strategy-review:reports" if active_experiment_ids else None),
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
        app.state.paper_repository = paper_repository_store
        app.state.observation_service = observation_service
        app.state.v28_paper_experiments = [lane[1] for lane in v28_lanes]
        app.state.v29_paper_experiments = [lane[1] for lane in v29_lanes]
        app.state.observation_experiment_id = settings.runtime_observation_experiment_id
        app.state.shadow_observation_experiment_id = active_shadow_experiment_id
        app.state.shadow_dynamic_paper_portfolio_id = (
            settings.runtime_shadow_dynamic_paper_portfolio_id
        )
        app.state.shadow_startup_error = shadow_startup_error
        app.state.dynamic_policy = policy
        app.state.shadow_dynamic_policy = shadow_policy
        app.state.strategy_review_analyzer = strategy_review_analyzer
        app.state.strategy_review_workflow = strategy_review_workflow
        app.state.derivatives_observation_service = derivatives_observation_service
        app.state.derivatives_market_streams = derivatives_market_streams
        app.state.derivatives_startup_error = derivatives_startup_error
        streams_started = False
        runtime_started = False
        try:
            if derivatives_market_streams is not None:
                streams_started = True
                derivatives_market_streams.start()
            runtime_started = True
            runtime.start()
            yield
        finally:
            try:
                if streams_started and derivatives_market_streams is not None:
                    derivatives_market_streams.stop()
            finally:
                try:
                    if runtime_started:
                        runtime.stop()
                finally:
                    client.close()

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
