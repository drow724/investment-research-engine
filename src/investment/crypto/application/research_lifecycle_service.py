"""Use cases for reproducible strategy experiments and manual promotion."""

import hashlib
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from investment.crypto.application.backtest_service import build_universe
from investment.crypto.backtest.engine import BacktestEngine
from investment.crypto.backtest.models import BacktestConfig, BacktestResult
from investment.crypto.domain.market import MarketDataBundle
from investment.crypto.domain.research import (
    BacktestRun,
    DatasetSnapshot,
    Experiment,
    ExperimentStatus,
    ExperimentType,
    ResearchPeriod,
    RunStatus,
    StrategyVersion,
    ValidationMethod,
    ValidationRun,
    VersionStatus,
    canonical_parameters,
    deterministic_id,
)
from investment.crypto.portfolio.equal_weight import EqualWeightPortfolioConstructor
from investment.crypto.ports.market_data import CryptoMarketDataProvider
from investment.crypto.ports.research import ResearchLifecycleRepository
from investment.crypto.regime.btc_trend import BtcTrendRegimeModel
from investment.crypto.research.candidate import ConservativeCandidateEvaluationPolicy
from investment.crypto.research.validation import HoldoutValidation, WalkForwardValidation
from investment.crypto.risk.engine import DeterministicRiskEngine, RiskPolicy
from investment.crypto.strategy.momentum import CrossSectionalMomentumStrategy


@dataclass(frozen=True, slots=True)
class CreateStrategyExperimentCommand:
    hypothesis: str
    strategy_name: str
    candidate_parameters: dict[str, object]
    pair_symbols: tuple[str, ...]
    train_period: ResearchPeriod
    validation_period: ResearchPeriod
    validation_method: ValidationMethod = ValidationMethod.WALK_FORWARD
    feature_changes: tuple[str, ...] = ()
    requested_metrics: tuple[str, ...] = (
        "sharpe_ratio",
        "maximum_drawdown",
        "fee_adjusted_return",
        "turnover",
    )


@dataclass(frozen=True, slots=True)
class RunExperimentResult:
    experiment: Experiment
    champion_run: BacktestRun
    challenger_run: BacktestRun
    validation_run: ValidationRun


class CryptoResearchLifecycleService:
    def __init__(
        self,
        provider: CryptoMarketDataProvider,
        repository: ResearchLifecycleRepository,
        policy: ConservativeCandidateEvaluationPolicy | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._policy = policy or ConservativeCandidateEvaluationPolicy()

    def initialize_strategy(self, name: str, parameters: dict[str, object]) -> StrategyVersion:
        if self._repository.list_strategies():
            raise ValueError("strategy registry is already initialized")
        version = StrategyVersion(
            f"{name}:v1",
            name,
            1,
            canonical_parameters(parameters),
            VersionStatus.ACTIVE,
            datetime.now(UTC),
        )
        self._repository.save_strategy(version, create=True)
        return version

    def create_experiment(self, command: CreateStrategyExperimentCommand) -> Experiment:
        champion = self._repository.active_strategy(command.strategy_name)
        candidate_number = (
            max(
                (
                    item.version
                    for item in self._repository.list_strategies()
                    if item.strategy_name == command.strategy_name
                ),
                default=champion.version,
            )
            + 1
        )
        payload = canonical_parameters(
            {
                "hypothesis": command.hypothesis,
                "base": champion.version_id,
                "candidate": command.candidate_parameters,
                "pairs": command.pair_symbols,
                "train_start": command.train_period.start.isoformat(),
                "train_end": command.train_period.end.isoformat(),
                "validation_start": command.validation_period.start.isoformat(),
                "validation_end": command.validation_period.end.isoformat(),
                "validation_method": command.validation_method.value,
                "feature_changes": command.feature_changes,
                "requested_metrics": command.requested_metrics,
            }
        )
        experiment_id = deterministic_id("crypto-exp", payload)
        candidate_id = f"{command.strategy_name}:v{candidate_number}"
        candidate = StrategyVersion(
            candidate_id,
            command.strategy_name,
            candidate_number,
            canonical_parameters(command.candidate_parameters),
            VersionStatus.CHALLENGER,
            datetime.now(UTC),
            experiment_id,
        )
        experiment = Experiment(
            experiment_id,
            command.hypothesis,
            ExperimentType.STRATEGY,
            champion.version_id,
            None,
            candidate_id,
            None,
            payload,
            command.feature_changes,
            command.train_period,
            command.validation_period,
            command.validation_method,
            command.requested_metrics,
            ExperimentStatus.DRAFT,
            datetime.now(UTC),
        )
        self._repository.save_strategy(candidate, create=True)
        self._repository.save_experiment(experiment, create=True)
        return experiment

    def mark_ready(self, experiment_id: str) -> Experiment:
        experiment = self._repository.get_experiment(experiment_id).transition(
            ExperimentStatus.READY
        )
        self._repository.save_experiment(experiment)
        return experiment

    def run(self, experiment_id: str) -> RunExperimentResult:
        experiment = self._repository.get_experiment(experiment_id)
        running = experiment.transition(ExperimentStatus.RUNNING)
        self._repository.save_experiment(running)
        try:
            result = self._execute(running)
        except Exception:
            self._repository.save_experiment(running.transition(ExperimentStatus.FAILED))
            raise
        return result

    def promote(self, experiment_id: str, approved_by: str) -> Experiment:
        if not approved_by.strip():
            raise ValueError("approved_by is required for manual promotion")
        experiment = self._repository.get_experiment(experiment_id)
        if experiment.status is not ExperimentStatus.CANDIDATE:
            raise ValueError("only a validated candidate can be promoted")
        if (
            experiment.base_strategy_version is None
            or experiment.candidate_strategy_version is None
        ):
            raise ValueError("strategy candidate references are missing")
        champion = self._repository.get_strategy(experiment.base_strategy_version)
        challenger = self._repository.get_strategy(experiment.candidate_strategy_version)
        self._repository.save_strategy(champion.with_status(VersionStatus.RETIRED))
        self._repository.save_strategy(challenger.with_status(VersionStatus.ACTIVE))
        promoted = replace(
            experiment.transition(ExperimentStatus.PROMOTED), approved_by=approved_by.strip()
        )
        self._repository.save_experiment(promoted)
        return promoted

    def reject(self, experiment_id: str) -> Experiment:
        experiment = self._repository.get_experiment(experiment_id)
        rejected = experiment.transition(ExperimentStatus.REJECTED)
        if rejected.candidate_strategy_version is not None:
            candidate = self._repository.get_strategy(rejected.candidate_strategy_version)
            self._repository.save_strategy(candidate.with_status(VersionStatus.REJECTED))
        self._repository.save_experiment(rejected)
        return rejected

    def get(self, experiment_id: str) -> Experiment:
        return self._repository.get_experiment(experiment_id)

    def list_experiments(self) -> tuple[Experiment, ...]:
        return self._repository.list_experiments()

    def list_strategies(self) -> tuple[StrategyVersion, ...]:
        return self._repository.list_strategies()

    def _execute(self, experiment: Experiment) -> RunExperimentResult:
        if (
            experiment.base_strategy_version is None
            or experiment.candidate_strategy_version is None
        ):
            raise ValueError("strategy experiment is incomplete")
        champion = self._repository.get_strategy(experiment.base_strategy_version)
        challenger = self._repository.get_strategy(experiment.candidate_strategy_version)
        pairs_value = _payload(experiment)["pairs"]
        if not isinstance(pairs_value, list):
            raise ValueError("experiment pairs must be a list")
        pairs = tuple(str(value) for value in pairs_value)
        universe = build_universe(pairs)
        start = experiment.train_period.start - timedelta(days=420)
        bundle = self._provider.fetch(universe, start, experiment.validation_period.end)
        snapshot = _snapshot(bundle, experiment)
        self._repository.save_snapshot(snapshot)
        champion_run, champion_result = self._backtest(experiment, champion, snapshot, bundle)
        challenger_run, challenger_result = self._backtest(experiment, challenger, snapshot, bundle)
        checks = self._validation_checks(experiment)
        now = datetime.now(UTC)
        validation = ValidationRun(
            deterministic_id("validation-run", f"{experiment.experiment_id}:1"),
            experiment.experiment_id,
            snapshot.snapshot_id,
            experiment.validation_method,
            RunStatus.SUCCEEDED,
            not checks,
            checks,
            now,
            now,
        )
        self._repository.save_validation_run(validation, create=True)
        evaluation = self._policy.evaluate(
            champion_result.metrics,
            challenger_result.metrics,
            validation_successful=validation.successful,
        )
        completed = replace(
            experiment.transition(ExperimentStatus.COMPLETED),
            dataset_snapshot_id=snapshot.snapshot_id,
            run_ids=(champion_run.run_id, challenger_run.run_id, validation.run_id),
            evaluation=evaluation,
        )
        final_status = (
            ExperimentStatus.CANDIDATE if evaluation.passed else ExperimentStatus.REJECTED
        )
        final = completed.transition(final_status)
        candidate_status = VersionStatus.CANDIDATE if evaluation.passed else VersionStatus.REJECTED
        self._repository.save_strategy(challenger.with_status(candidate_status))
        self._repository.save_experiment(final)
        return RunExperimentResult(final, champion_run, challenger_run, validation)

    def _backtest(
        self,
        experiment: Experiment,
        version: StrategyVersion,
        snapshot: DatasetSnapshot,
        bundle: MarketDataBundle,
    ) -> tuple[BacktestRun, BacktestResult]:
        parameters = version.parameters
        started = datetime.now(UTC)
        run_id = deterministic_id(
            "backtest-run", f"{experiment.experiment_id}:{version.version_id}"
        )
        result = _engine(parameters).run(
            bundle,
            BacktestConfig(
                experiment.validation_period.start,
                experiment.validation_period.end,
                Decimal(str(parameters.get("initial_capital", "100000"))),
                _int_parameter(parameters, "rebalance_days", 7),
                Decimal(str(parameters.get("fee_rate", "0.0005"))),
                Decimal(str(parameters.get("slippage_rate", "0.001"))),
            ),
        )
        run = BacktestRun(
            run_id,
            experiment.experiment_id,
            version.version_id,
            snapshot.snapshot_id,
            str(parameters.get("fee_rate", "0.0005")),
            str(parameters.get("slippage_rate", "0.001")),
            RunStatus.SUCCEEDED,
            started,
            datetime.now(UTC),
            result.metrics,
        )
        self._repository.save_backtest_run(run, create=True)
        return run, result

    @staticmethod
    def _validation_checks(experiment: Experiment) -> tuple[str, ...]:
        strategy = (
            WalkForwardValidation(_int_parameter(_payload(experiment), "purge_days", 1))
            if experiment.validation_method is ValidationMethod.WALK_FORWARD
            else HoldoutValidation()
        )
        return strategy.validate_periods(experiment.train_period, experiment.validation_period)


def _engine(parameters: dict[str, object]) -> BacktestEngine:
    maximum_positions = _int_parameter(parameters, "maximum_positions", 3)
    maximum_weight = Decimal(str(parameters.get("maximum_asset_weight", "0.5")))
    return BacktestEngine(
        BtcTrendRegimeModel(),
        CrossSectionalMomentumStrategy(
            _int_parameter(parameters, "momentum_window_days", 30),
            maximum_positions,
            Decimal(str(parameters.get("minimum_average_quote_volume", "0"))),
        ),
        EqualWeightPortfolioConstructor(maximum_positions, maximum_weight),
        DeterministicRiskEngine(
            RiskPolicy(maximum_positions=maximum_positions, maximum_asset_fraction=maximum_weight)
        ),
    )


def _payload(experiment: Experiment) -> dict[str, object]:
    import json

    value = json.loads(experiment.parameters_json)
    if not isinstance(value, dict):
        raise ValueError("invalid experiment parameters")
    return value


def _int_parameter(parameters: dict[str, object], name: str, default: int) -> int:
    value = parameters.get(name, default)
    if not isinstance(value, (int, str)):
        raise ValueError(f"{name} must be an integer")
    return int(value)


def _snapshot(bundle: MarketDataBundle, experiment: Experiment) -> DatasetSnapshot:
    digest = hashlib.sha256()
    for symbol in sorted(bundle.candles):
        for candle in bundle.candles[symbol]:
            digest.update(
                f"{symbol}|{candle.open_time.isoformat()}|{candle.available_at.isoformat()}|"
                f"{candle.open}|{candle.high}|{candle.low}|{candle.close}|{candle.volume}\n".encode()
            )
    checksum = digest.hexdigest()
    snapshot_id = deterministic_id(
        "crypto-snapshot",
        f"{checksum}:{experiment.train_period.start.isoformat()}:"
        f"{experiment.validation_period.end.isoformat()}",
    )
    return DatasetSnapshot(
        snapshot_id,
        experiment.train_period.start,
        experiment.validation_period.end,
        "point-in-time-universe-v1",
        "momentum-strategy-features-v1",
        "normalized-parquet",
        checksum,
        datetime.now(UTC),
    )
