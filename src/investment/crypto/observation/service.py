"""Frozen-decision capture, forward evaluation, health, and evidence reports."""

import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import Any

from investment.core.domain.observation import require_utc
from investment.crypto.application.backtest_service import build_universe
from investment.crypto.application.dynamic_paper_rebalance import (
    DynamicPaperRebalanceResult,
    DynamicUniversePolicy,
    SelectionVariantResult,
)
from investment.crypto.domain.order import OrderSide
from investment.crypto.observation.domain import (
    DecisionAction,
    DecisionMarketContext,
    DecisionOutcome,
    DecisionSelectionVariant,
    DecisionSnapshot,
    ObservationExperiment,
    ObservationStatus,
    OutcomeStatus,
)
from investment.crypto.observation.repository import SqliteObservationRepository
from investment.crypto.ports.accounting import PaperPortfolioRepository
from investment.crypto.ports.market_data import CryptoMarketDataProvider
from investment.crypto.strategy_registry import strategy_policy_config_values

HORIZON_MINUTES = (15, 30, 60, 120, 240, 720, 1440)
OUTCOME_DATA_GRACE = timedelta(minutes=30)


def strategy_config_hash(policy: DynamicUniversePolicy) -> str:
    payload = json.dumps(
        strategy_policy_config_values(policy),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class FrozenObservationService:
    def __init__(
        self,
        repository: SqliteObservationRepository,
        paper_repository: PaperPortfolioRepository,
        market_data: CryptoMarketDataProvider,
        runtime_state_root: str | Path | None = None,
        runtime_job_names: dict[str, str] | None = None,
    ) -> None:
        self.repository = repository
        self.paper_repository = paper_repository
        self.market_data = market_data
        self.runtime_state_root = Path(runtime_state_root) if runtime_state_root else None
        self.runtime_job_names = dict(runtime_job_names or {})

    def start(
        self,
        experiment_id: str,
        portfolio_id: str,
        policy: DynamicUniversePolicy,
        *,
        started_at: datetime | None = None,
    ) -> ObservationExperiment:
        start = require_utc(started_at or datetime.now(UTC), "started_at")
        fingerprint = strategy_config_hash(policy)
        try:
            existing = self.repository.experiment(experiment_id)
        except KeyError:
            self.paper_repository.get(portfolio_id)
            experiment = ObservationExperiment(
                experiment_id,
                portfolio_id,
                policy.strategy_version,
                fingerprint,
                start,
                start + timedelta(hours=168),
                ObservationStatus.RUNNING,
                self._portfolio_equity(portfolio_id, start),
            )
            self.repository.save_experiment(experiment)
            return experiment
        if (
            existing.portfolio_id != portfolio_id
            or existing.strategy_version != policy.strategy_version
            or existing.config_hash != fingerprint
        ):
            raise ValueError("existing observation identity does not match frozen strategy")
        return existing

    def permits_execution(
        self,
        experiment_id: str,
        portfolio_id: str,
        policy: DynamicUniversePolicy,
        *,
        now: datetime,
    ) -> bool:
        """Check frozen identity and deadline BEFORE any paper order is submitted."""
        current = require_utc(now, "now")
        experiment = self.repository.experiment(experiment_id)
        if experiment.status is not ObservationStatus.RUNNING:
            return False
        if (
            experiment.portfolio_id != portfolio_id
            or experiment.strategy_version != policy.strategy_version
            or experiment.config_hash != strategy_config_hash(policy)
        ):
            self.repository.save_experiment(
                replace(
                    experiment,
                    status=ObservationStatus.INVALIDATED,
                    completed_at=current,
                    interruption_reason="execution identity does not match frozen experiment",
                )
            )
            raise ValueError("execution identity does not match frozen experiment")
        if current >= experiment.planned_end_at:
            self.repository.save_experiment(
                replace(
                    experiment,
                    status=ObservationStatus.COMPLETED,
                    completed_at=experiment.planned_end_at,
                )
            )
            return False
        return current >= experiment.started_at

    def capture(
        self,
        experiment_id: str,
        result: DynamicPaperRebalanceResult,
        policy: DynamicUniversePolicy,
    ) -> int:
        experiment = self.repository.experiment(experiment_id)
        if experiment.status is not ObservationStatus.RUNNING:
            return 0
        if not experiment.started_at <= result.as_of <= experiment.planned_end_at:
            return 0
        if result.portfolio_id != experiment.portfolio_id:
            raise ValueError("decision portfolio does not match observation")
        if (
            policy.strategy_version != experiment.strategy_version
            or strategy_config_hash(policy) != experiment.config_hash
        ):
            invalidated = replace(
                experiment,
                status=ObservationStatus.INVALIDATED,
                completed_at=result.as_of,
                interruption_reason="frozen strategy configuration changed",
            )
            self.repository.save_experiment(invalidated)
            raise ValueError("frozen strategy configuration changed; observation invalidated")
        if result.as_of >= experiment.planned_end_at:
            self.repository.save_experiment(
                replace(experiment, status=ObservationStatus.COMPLETED, completed_at=result.as_of)
            )
        selected = {item.pair: item for item in result.selected}
        orders = {item.pair: item for item in result.orders}
        scores = sorted(
            (item for item in result.assessments if item.eligible and item.score is not None),
            key=lambda item: (-float(item.score or 0), item.pair),
        )
        ranks = {item.pair: index for index, item in enumerate(scores, 1)}
        final_weights = self._final_weights(result)
        current_weights = dict(final_weights)
        for pair, order in orders.items():
            current_weights[pair] = float(order.current_weight)
        current_cash = max(0.0, float(result.equity) * (1 - sum(current_weights.values())))
        has_sell = any(item.side is OrderSide.SELL for item in result.orders)
        decision_id = hashlib.sha256(
            f"{result.portfolio_id}:{result.as_of.isoformat()}:{policy.strategy_version}".encode()
        ).hexdigest()[:24]
        snapshots = []
        snapshot_ids_by_pair: dict[str, str] = {}
        for item in result.assessments:
            target = float(selected[item.pair].target_weight) if item.pair in selected else 0.0
            current = current_weights.get(item.pair, 0.0)
            action, reason = self._action_reason(
                item.pair,
                item.eligible,
                current,
                selected,
                orders,
                item.decision_reasons,
                result.decision_reasons,
                item.reason,
                has_sell,
            )
            snapshot_id = hashlib.sha256(
                f"{experiment_id}:{result.as_of.isoformat()}:{item.pair}".encode()
            ).hexdigest()[:32]
            snapshot_ids_by_pair[item.pair] = snapshot_id
            snapshots.append(
                DecisionSnapshot(
                    snapshot_id,
                    experiment_id,
                    decision_id,
                    experiment.strategy_version,
                    experiment.config_hash,
                    result.as_of,
                    item.pair.removesuffix("KRW"),
                    item.pair,
                    action,
                    reason,
                    item.score,
                    ranks.get(item.pair),
                    item.eligible,
                    item.pair in selected,
                    current,
                    target,
                    current_cash,
                    float(result.equity),
                    sum(current_weights.values()),
                    sum(float(value.target_weight) for value in result.selected),
                    float(item.latest_price) if item.latest_price is not None else None,
                    float(item.average_quote_volume)
                    if item.average_quote_volume is not None
                    else None,
                    result.as_of.hour,
                    result.as_of.weekday(),
                    item.momentum_1h,
                    item.momentum_4h,
                    item.momentum_24h,
                    item.volatility,
                    item.reference_at,
                    ranks.get(item.pair) if item.pair in selected else None,
                    item.raw_score,
                    item.score_penalty,
                    item.expected_relative_return_1h,
                    item.expected_relative_return_4h,
                    item.fee_adjusted_expected_return,
                    json.dumps(list(item.decision_reasons), separators=(",", ":")),
                )
            )
        market_context = (
            DecisionMarketContext(
                experiment_id,
                decision_id,
                experiment.strategy_version,
                experiment.config_hash,
                result.as_of,
                json.dumps(
                    {
                        **result.derivatives_overlay.to_dict(),
                        "decisionDiagnostics": result.decision_diagnostics or {},
                        "executionModelVersion": result.execution_model_version,
                        "feeRate": str(result.execution_fee_rate),
                        "slippageRate": str(result.execution_slippage_rate),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
            if result.derivatives_overlay is not None
            else None
        )
        selection_variants = self._selection_variant_rows(
            result.selection_variants,
            snapshot_ids_by_pair,
        )
        return self.repository.save_decision_bundle(
            tuple(snapshots),
            market_context,
            selection_variants,
        )

    @staticmethod
    def _selection_variant_rows(
        variants: tuple[SelectionVariantResult, ...],
        snapshot_ids_by_pair: dict[str, str],
    ) -> tuple[DecisionSelectionVariant, ...]:
        """Expand a cohort-level control into one immutable row per candidate."""
        variant_ids = [variant.variant_id for variant in variants]
        if len(set(variant_ids)) != len(variant_ids):
            raise ValueError("selection variant identifiers must be unique within a cohort")
        rows = []
        for variant in variants:
            selected_by_pair = {item.pair: item for item in variant.selected}
            if len(selected_by_pair) != len(variant.selected):
                raise ValueError("selection variant cannot select the same pair twice")
            unknown_pairs = set(selected_by_pair).difference(snapshot_ids_by_pair)
            if unknown_pairs:
                raise ValueError(
                    "selection variant references a pair outside the frozen cohort: "
                    f"{sorted(unknown_pairs)}"
                )
            for pair, snapshot_id in snapshot_ids_by_pair.items():
                target = selected_by_pair.get(pair)
                rows.append(
                    DecisionSelectionVariant(
                        snapshot_id,
                        variant.variant_id,
                        target is not None,
                        float(target.target_weight) if target is not None else 0.0,
                        target.reason if target is not None else "NOT_SELECTED_BY_VARIANT",
                    )
                )
        return tuple(rows)

    def interrupt(
        self,
        experiment_id: str,
        reason: str,
        *,
        interrupted_at: datetime | None = None,
    ) -> ObservationExperiment:
        """Close a running observation without deleting its evidence."""
        current = self.repository.experiment(experiment_id)
        if current.status is not ObservationStatus.RUNNING:
            return current
        stopped_at = require_utc(interrupted_at or datetime.now(UTC), "interrupted_at")
        interrupted = replace(
            current,
            status=ObservationStatus.INTERRUPTED,
            completed_at=stopped_at,
            interruption_reason=reason,
        )
        self.repository.save_experiment(interrupted)
        return interrupted

    def evaluate_pending(self, experiment_id: str, *, now: datetime | None = None) -> int:
        evaluated_at = require_utc(now or datetime.now(UTC), "now")
        experiment = self.repository.experiment(experiment_id)
        if (
            experiment.status is ObservationStatus.RUNNING
            and evaluated_at >= experiment.planned_end_at
        ):
            self.repository.save_experiment(
                replace(
                    experiment,
                    status=ObservationStatus.COMPLETED,
                    completed_at=experiment.planned_end_at,
                )
            )
        existing = {
            (item.snapshot_id, item.horizon_minutes)
            for item in self.repository.outcomes(experiment_id)
        }
        inserted = 0
        for snapshot in self.repository.snapshots(experiment_id):
            if snapshot.reference_price is None or snapshot.reference_price <= 0:
                continue
            for horizon in HORIZON_MINUTES:
                target = snapshot.decision_time + timedelta(minutes=horizon)
                key = (snapshot.snapshot_id, horizon)
                if key in existing or evaluated_at < target:
                    continue
                outcome = self._outcome(snapshot, horizon, target, evaluated_at)
                # Collection and evaluation are separate jobs. Do not permanently
                # classify a transient sync delay as missing market data.
                if (
                    outcome.status is OutcomeStatus.MISSING_DATA
                    and evaluated_at < target + OUTCOME_DATA_GRACE
                ):
                    continue
                inserted += self.repository.save_outcome(outcome)
        return inserted

    def health(self, experiment_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        current = require_utc(now or datetime.now(UTC), "now")
        experiment = self.repository.experiment(experiment_id)
        counts = self.repository.health_counts(experiment_id, current)
        observed_until = self._effective_end(experiment, current)
        expected = max(0, int((observed_until - experiment.started_at).total_seconds() // 900))
        runtime = self._runtime_health(experiment)
        return {
            "experimentId": experiment_id,
            "status": experiment.status.value,
            "expectedDecisionCycles": expected,
            "actualDecisionCycles": counts["actualDecisionCycles"],
            "missingDecisionCycles": max(0, expected - counts["actualDecisionCycles"]),
            "candidateSnapshots": counts["candidateSnapshots"],
            "outcomeRows": counts["outcomeRows"],
            "outcomeEvaluationBacklog": max(0, counts["maturedOutcomes"] - counts["outcomeRows"]),
            "missingDataOutcomes": counts["missingDataOutcomes"],
            "unresolvedDecisions": counts["unresolvedDecisions"],
            "quarantinedMarkets": sorted(
                item.market
                for item in self.repository.latest_snapshots(experiment_id)
                if item.reason == "STALE_MARKET_DATA"
            ),
            "missingDataByReason": self.repository.missing_reason_counts(experiment_id),
            **runtime,
        }

    def report(self, experiment_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        current = require_utc(now or datetime.now(UTC), "now")
        experiment = self.repository.experiment(experiment_id)
        snapshots = self.repository.snapshots(experiment_id)
        outcomes = self.repository.outcomes(experiment_id)
        effective_end = self._effective_end(experiment, current)
        decision_equity = self._decision_equity(snapshots)
        ending_equity = decision_equity[-1] if decision_equity else experiment.starting_equity
        executions = tuple(
            item
            for item in self.paper_repository.list_executions(experiment.portfolio_id, 10_000)
            if experiment.started_at <= item.executed_at <= effective_end
        )
        sells = tuple(item for item in executions if item.side is OrderSide.SELL)
        fees = sum(float(item.fee) for item in executions)
        realized = [float(item.realized_pnl) for item in sells]
        winners = [item for item in realized if item > 0]
        losers = [item for item in realized if item < 0]
        net_pnl = ending_equity - experiment.starting_equity
        btc_return = self._benchmark_return(experiment.started_at, effective_end)
        net_return = net_pnl / experiment.starting_equity if experiment.starting_equity else 0.0
        return {
            "experiment": {
                "experimentId": experiment.experiment_id,
                "durationHours": (effective_end - experiment.started_at).total_seconds() / 3600,
                "strategyVersion": experiment.strategy_version,
                "configHash": experiment.config_hash,
                **self.health(experiment_id, now=current),
            },
            "performance": {
                "startingEquity": experiment.starting_equity,
                "endingEquity": ending_equity,
                "grossPnl": net_pnl + fees,
                "netPnl": net_pnl,
                "netReturn": net_return,
                "btcBenchmarkReturn": btc_return,
                "excessReturnVsBtc": net_return - btc_return if btc_return is not None else None,
                "maximumDrawdown": self._max_drawdown(decision_equity),
                "totalFees": fees,
                "totalTurnover": sum(float(item.quantity * item.price) for item in executions),
            },
            "trades": {
                "completedTrades": len(sells),
                "winRate": len(winners) / len(realized) if realized else None,
                "averageWinner": fmean(winners) if winners else None,
                "averageLoser": fmean(losers) if losers else None,
                "profitFactor": sum(winners) / -sum(losers) if losers else None,
                "expectancyNetPnl": fmean(realized) if realized else None,
                "realizedPnl": sum(realized),
                "averageHoldingHours": self._average_holding_hours(executions),
            },
            "signalQuality": {
                "scoreBuckets": self._group_analysis(snapshots, outcomes, "score"),
                "rankAnalysis": self._group_analysis(snapshots, outcomes, "rank"),
                "decisionReasonAnalysis": self._group_analysis(snapshots, outcomes, "reason"),
                "actionAnalysis": self._group_analysis(snapshots, outcomes, "action"),
                "marketRegimeAnalysis": {
                    "status": "UNAVAILABLE",
                    "reason": "no published BTC regime was consumed by v2.1 decisions",
                },
            },
            "systemHealth": self.health(experiment_id, now=current),
            "conventions": {
                "forwardReturn": (
                    "decision completed-candle close to latest completed close at horizon"
                ),
                "mfeMae": (
                    "highest high / lowest low available after decision through horizon "
                    "versus decision reference close"
                ),
                "expectancy": (
                    "mean authoritative net realized PnL of completed sells; fees are included"
                ),
            },
        }

    def diagnostics(self, experiment_id: str) -> dict[str, Any]:
        """Read-only candidate and forward-outcome view for the development dashboard."""
        experiment = self.repository.experiment(experiment_id)
        snapshots = self.repository.snapshots(experiment_id)
        latest = self.repository.latest_snapshots(experiment_id)
        outcomes = self.repository.outcomes(experiment_id)
        decision_times = sorted({item.decision_time for item in snapshots}, reverse=True)[:8]
        recent = tuple(item for item in snapshots if item.decision_time in decision_times)
        recent_ids = {item.snapshot_id for item in recent}
        recent_contexts = tuple(
            item
            for item in self.repository.market_contexts(experiment_id)
            if item.decision_time in decision_times
        )
        latest_outcomes: dict[str, dict[str, dict[str, Any]]] = {}
        for outcome in outcomes:
            if outcome.snapshot_id not in recent_ids:
                continue
            latest_outcomes.setdefault(outcome.snapshot_id, {})[str(outcome.horizon_minutes)] = {
                "status": outcome.status.value,
                "forwardReturn": outcome.forward_return,
                "mfe": outcome.mfe,
                "mae": outcome.mae,
                "missingReason": outcome.missing_reason,
            }
        return {
            "experiment": {
                "experimentId": experiment.experiment_id,
                "portfolioId": experiment.portfolio_id,
                "strategyVersion": experiment.strategy_version,
                "status": experiment.status.value,
                "startedAt": experiment.started_at,
            },
            "latestDecisionTime": latest[0].decision_time if latest else None,
            "decisionTimes": decision_times,
            "recentMarketContexts": [
                {
                    "decisionId": item.decision_id,
                    "decisionTime": item.decision_time,
                    "context": json.loads(item.context_json),
                }
                for item in sorted(
                    recent_contexts,
                    key=lambda value: value.decision_time,
                    reverse=True,
                )
            ],
            "recentCandidates": [
                {
                    **asdict(item),
                    "action": item.action.value,
                    "decisionAt": item.decision_time,
                    "referenceAt": item.reference_at,
                    "referenceAgeSeconds": (
                        (item.decision_time - item.reference_at).total_seconds()
                        if item.reference_at is not None
                        else None
                    ),
                    "outcomes": latest_outcomes.get(item.snapshot_id, {}),
                }
                for item in sorted(
                    recent,
                    key=lambda value: (
                        -value.decision_time.timestamp(),
                        not value.eligible,
                        value.rank or 9999,
                        value.market,
                    ),
                )
            ],
            "horizonSummary": self._cohort_summary(snapshots, outcomes),
            "componentIc": self._component_ic(snapshots, outcomes),
            "scoreDeciles": self._score_deciles(snapshots, outcomes),
        }

    def _outcome(
        self, snapshot: DecisionSnapshot, horizon: int, target: datetime, evaluated_at: datetime
    ) -> DecisionOutcome:
        pair = build_universe((f"{snapshot.asset}/KRW",)).pairs[0]
        missing_reason = "NO_POST_DECISION_CANDLE"
        try:
            bundle = self.market_data.fetch(
                build_universe((f"{snapshot.asset}/KRW",)),
                snapshot.decision_time - timedelta(minutes=15),
                target,
            )
            candles = tuple(
                item
                for item in bundle.candles[pair.symbol]
                if snapshot.decision_time < item.available_at <= target
            )
        except FileNotFoundError:
            missing_reason = "MARKET_DATA_FILE_NOT_FOUND"
            candles = ()
        except ValueError:
            missing_reason = "MARKET_DATA_INVALID"
            candles = ()
        if candles and target - candles[-1].available_at > timedelta(minutes=30):
            missing_reason = "TARGET_CANDLE_STALE"
        if not candles or missing_reason == "TARGET_CANDLE_STALE":
            return DecisionOutcome(
                snapshot.snapshot_id,
                horizon,
                target,
                evaluated_at,
                OutcomeStatus.MISSING_DATA,
                None,
                None,
                None,
                missing_reason,
            )
        reference = float(snapshot.reference_price or 0)
        return DecisionOutcome(
            snapshot.snapshot_id,
            horizon,
            target,
            evaluated_at,
            OutcomeStatus.COMPLETED,
            float(candles[-1].close) / reference - 1,
            max(float(item.high) for item in candles) / reference - 1,
            min(float(item.low) for item in candles) / reference - 1,
        )

    def _portfolio_equity(self, portfolio_id: str, as_of: datetime) -> float:
        decisions = self.paper_repository.list_rebalance_decisions(portfolio_id, 1)
        if decisions and decisions[0].as_of <= as_of:
            return float(decisions[0].equity)
        return float(self.paper_repository.get(portfolio_id).cash_balance)

    @staticmethod
    def _final_weights(result: DynamicPaperRebalanceResult) -> dict[str, float]:
        prices = {
            item.pair.removesuffix("KRW"): float(item.latest_price)
            for item in result.assessments
            if item.latest_price is not None
        }
        return {
            f"{position.asset.symbol}KRW": float(position.quantity)
            * prices[position.asset.symbol]
            / float(result.equity)
            for position in result.final_portfolio.positions
            if position.asset.symbol in prices
        }

    @staticmethod
    def _action_reason(
        pair: str,
        eligible: bool,
        current: float,
        selected: dict[str, Any],
        orders: dict[str, Any],
        candidate_reasons: tuple[str, ...],
        global_reasons: tuple[str, ...],
        assessment_reason: str,
        has_sell: bool,
    ) -> tuple[DecisionAction, str]:
        if pair in orders:
            order = orders[pair]
            if order.side is OrderSide.SELL:
                return DecisionAction.EXIT, "TARGET_POSITION_EXIT"
            return (
                DecisionAction.REPLACE if has_sell else DecisionAction.ENTRY,
                "ORDER_CREATED_FOR_TARGET_WEIGHT",
            )
        if pair in selected:
            if current > 0:
                return DecisionAction.HOLD, selected[pair].reason
            selected_reasons = candidate_reasons or global_reasons
            return (
                DecisionAction.REJECTED_ENTRY,
                "|".join(selected_reasons) or "ORDER_NOT_CREATED",
            )
        if eligible:
            joined = "|".join(candidate_reasons) or "NOT_SELECTED_BY_RANK"
            action = (
                DecisionAction.REJECTED_REPLACEMENT
                if "REPLACEMENT_SCORE_ADVANTAGE_INSUFFICIENT" in candidate_reasons and current == 0
                else DecisionAction.REJECTED_ENTRY
            )
            return action, joined
        return DecisionAction.NO_ACTION, assessment_reason

    @staticmethod
    def _decision_equity(snapshots: tuple[DecisionSnapshot, ...]) -> list[float]:
        by_time: dict[datetime, float] = {}
        for item in snapshots:
            by_time[item.decision_time] = item.portfolio_equity
        return [by_time[key] for key in sorted(by_time)]

    @staticmethod
    def _max_drawdown(values: list[float]) -> float:
        peak = 0.0
        drawdown = 0.0
        for value in values:
            peak = max(peak, value)
            if peak:
                drawdown = min(drawdown, value / peak - 1)
        return drawdown

    def _benchmark_return(self, start: datetime, end: datetime) -> float | None:
        if end <= start:
            return None
        universe = build_universe(("BTC/KRW",))
        try:
            candles = self.market_data.fetch(universe, start, end).candles["BTCKRW"]
        except (FileNotFoundError, ValueError):
            return None
        return float(candles[-1].close / candles[0].close - 1) if len(candles) >= 2 else None

    @staticmethod
    def _average_holding_hours(executions: tuple[Any, ...]) -> float | None:
        opened: dict[str, datetime] = {}
        durations = []
        for item in sorted(executions, key=lambda value: value.executed_at):
            if item.side is OrderSide.BUY:
                opened.setdefault(item.pair, item.executed_at)
            elif item.pair in opened:
                durations.append((item.executed_at - opened.pop(item.pair)).total_seconds() / 3600)
        return fmean(durations) if durations else None

    def _runtime_health(self, experiment: ObservationExperiment) -> dict[str, int]:
        if self.runtime_state_root is None:
            return {
                "failedCycles": 0,
                "runtimeErrors": 0,
                "duplicateExecutionAttempts": 0,
                "lockContentionSkips": 0,
                "reconciliationErrors": 0,
            }
        executions = []
        effective_end = self._effective_end(experiment, datetime.now(UTC))
        for path in (self.runtime_state_root / "executions").glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                scheduled = datetime.fromisoformat(value["scheduled_at"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                continue
            if experiment.started_at <= scheduled <= effective_end:
                executions.append(value)
        lane_job_name = self.runtime_job_names.get(
            experiment.strategy_version,
            "crypto_dynamic_paper_rebalance",
        )
        lane_executions = [item for item in executions if item.get("job_name") == lane_job_name]
        failed = [item for item in lane_executions if item.get("status") == "FAILED"]
        return {
            "failedCycles": len(failed),
            "runtimeErrors": len(failed),
            "duplicateExecutionAttempts": sum(
                item.get("status") == "SKIPPED_DUPLICATE" for item in lane_executions
            ),
            "lockContentionSkips": sum(
                item.get("status") == "SKIPPED_LOCKED" for item in lane_executions
            ),
            "reconciliationErrors": sum(
                "reconciliation" in str(item.get("job_name", "")) for item in failed
            ),
        }

    @staticmethod
    def _effective_end(experiment: ObservationExperiment, current: datetime) -> datetime:
        return min(current, experiment.completed_at or experiment.planned_end_at)

    @staticmethod
    def _group_analysis(
        snapshots: tuple[DecisionSnapshot, ...],
        outcomes: tuple[DecisionOutcome, ...],
        field: str,
    ) -> list[dict[str, Any]]:
        lookup = {item.snapshot_id: item for item in snapshots}
        groups: dict[str, list[DecisionOutcome]] = {}
        for outcome in outcomes:
            if outcome.status is not OutcomeStatus.COMPLETED:
                continue
            snapshot = lookup[outcome.snapshot_id]
            value = getattr(snapshot, field)
            if field == "score":
                value = (
                    "<0"
                    if value is None or value < 0
                    else "0-1%"
                    if value < 0.01
                    else "1-3%"
                    if value < 0.03
                    else ">=3%"
                )
            elif hasattr(value, "value"):
                value = value.value
            groups.setdefault(str(value), []).append(outcome)
        return [
            {
                "group": key,
                "n": len(values),
                **{
                    f"return{horizon}m": fmean(
                        item.forward_return
                        for item in values
                        if item.horizon_minutes == horizon and item.forward_return is not None
                    )
                    if any(
                        item.horizon_minutes == horizon and item.forward_return is not None
                        for item in values
                    )
                    else None
                    for horizon in HORIZON_MINUTES
                },
            }
            for key, values in sorted(groups.items())
        ]

    @staticmethod
    def _cohort_summary(
        snapshots: tuple[DecisionSnapshot, ...], outcomes: tuple[DecisionOutcome, ...]
    ) -> list[dict[str, Any]]:
        lookup = {item.snapshot_id: item for item in snapshots}
        groups: dict[tuple[int, str], list[float]] = {}
        for outcome in outcomes:
            snapshot = lookup[outcome.snapshot_id]
            if (
                outcome.status is not OutcomeStatus.COMPLETED
                or outcome.forward_return is None
                or not snapshot.eligible
            ):
                continue
            cohort = "selected" if snapshot.selected else "eligible-not-selected"
            groups.setdefault((outcome.horizon_minutes, cohort), []).append(outcome.forward_return)
        return [
            {
                "horizonMinutes": horizon,
                "cohort": cohort,
                "observations": len(values),
                "averageReturn": fmean(values),
            }
            for (horizon, cohort), values in sorted(groups.items())
        ]

    @staticmethod
    def _component_ic(
        snapshots: tuple[DecisionSnapshot, ...], outcomes: tuple[DecisionOutcome, ...]
    ) -> list[dict[str, Any]]:
        lookup = {item.snapshot_id: item for item in snapshots}
        fields = ("score", "momentum_1h", "momentum_4h", "momentum_24h", "volatility")
        pairs: dict[tuple[int, str], list[tuple[float, float]]] = {}
        for outcome in outcomes:
            if outcome.status is not OutcomeStatus.COMPLETED or outcome.forward_return is None:
                continue
            snapshot = lookup[outcome.snapshot_id]
            if not snapshot.eligible:
                continue
            for field in fields:
                value = getattr(snapshot, field)
                if value is not None:
                    pairs.setdefault((outcome.horizon_minutes, field), []).append(
                        (float(value), outcome.forward_return)
                    )
        return [
            {
                "horizonMinutes": horizon,
                "component": component,
                "observations": len(values),
                "correlation": FrozenObservationService._correlation(values),
            }
            for (horizon, component), values in sorted(pairs.items())
        ]

    @staticmethod
    def _score_deciles(
        snapshots: tuple[DecisionSnapshot, ...], outcomes: tuple[DecisionOutcome, ...]
    ) -> list[dict[str, Any]]:
        lookup = {item.snapshot_id: item for item in snapshots}
        eligible_counts: dict[str, int] = {}
        for snapshot in snapshots:
            if snapshot.eligible and snapshot.rank is not None:
                eligible_counts[snapshot.decision_id] = (
                    eligible_counts.get(snapshot.decision_id, 0) + 1
                )
        groups: dict[tuple[int, int], list[float]] = {}
        for outcome in outcomes:
            snapshot = lookup[outcome.snapshot_id]
            count = eligible_counts.get(snapshot.decision_id, 0)
            if (
                outcome.status is not OutcomeStatus.COMPLETED
                or outcome.forward_return is None
                or snapshot.rank is None
                or count == 0
            ):
                continue
            decile = min(10, ((snapshot.rank - 1) * 10 // count) + 1)
            groups.setdefault((outcome.horizon_minutes, decile), []).append(outcome.forward_return)
        return [
            {
                "horizonMinutes": horizon,
                "decile": decile,
                "observations": len(values),
                "averageReturn": fmean(values),
            }
            for (horizon, decile), values in sorted(groups.items())
        ]

    @staticmethod
    def _correlation(values: list[tuple[float, float]]) -> float | None:
        if len(values) < 2:
            return None
        mean_x = fmean(item[0] for item in values)
        mean_y = fmean(item[1] for item in values)
        numerator = sum((x - mean_x) * (y - mean_y) for x, y in values)
        denominator_x = sum((x - mean_x) ** 2 for x, _ in values)
        denominator_y = sum((y - mean_y) ** 2 for _, y in values)
        denominator = (denominator_x * denominator_y) ** 0.5
        return numerator / denominator if denominator else None
