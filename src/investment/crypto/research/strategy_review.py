"""Deterministic, read-only promotion review for frozen paper experiments.

The analyzer intentionally reads the observation and paper-accounting databases
directly.  Repository constructors are not used here because they perform schema
initialization and migration, which would make a review mutate its evidence.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from statistics import fmean, median
from typing import Any, Literal
from zoneinfo import ZoneInfo

from investment.database.postgres import PostgresConnectionAdapter, postgres_connection

ReviewStage = Literal["PAPER", "SHADOW_TO_PAPER"]


@dataclass(frozen=True, slots=True)
class StrategyReviewThresholds:
    """Conservative defaults for promoting a paper challenger."""

    minimum_calendar_days: int = 3
    minimum_mature_1h_cohorts: int = 192
    minimum_mature_4h_cohorts: int = 96
    minimum_decision_coverage: float = 0.98
    maximum_outcome_missing_rate: float = 0.01
    minimum_selected_gross_return: float = 0.0025
    minimum_selected_spread: float = 0.002
    minimum_score_ic: float = 0.03
    minimum_paper_net_return: float = 0.0
    minimum_profit_factor: float = 1.2
    maximum_drawdown: float = 0.02
    maximum_selection_concentration: float = 0.25
    minimum_derivatives_context_coverage: float = 0.98
    minimum_derivatives_available_rate: float = 0.90
    required_derivatives_feature_version: str = "btc-squeeze-v2-market-streams"
    required_derivatives_basis_input_source: str | None = None
    minimum_derivatives_basis_input_source_match_rate: float = 1.0
    selection_control_variant_id: str | None = None
    minimum_selection_control_variant_coverage: float = 1.0
    minimum_selection_control_paired_4h_cohorts: int = 96
    minimum_selection_control_changed_cohorts: int = 30
    minimum_selection_control_outcome_coverage: float = 0.90
    maximum_selection_control_outcome_coverage_gap: float = 0.05
    minimum_selection_control_actual_minus_control: float = 0.0
    economic_round_trip_cost: float = 0.002
    decision_interval_minutes: int = 15
    outcome_settlement_grace_minutes: int = 30

    def __post_init__(self) -> None:
        positive_integers = (
            self.minimum_calendar_days,
            self.minimum_mature_1h_cohorts,
            self.minimum_mature_4h_cohorts,
            self.minimum_selection_control_paired_4h_cohorts,
            self.minimum_selection_control_changed_cohorts,
            self.decision_interval_minutes,
        )
        if any(value <= 0 for value in positive_integers):
            raise ValueError("minimum counts and decision interval must be positive")
        if self.outcome_settlement_grace_minutes < 0:
            raise ValueError("outcome settlement grace cannot be negative")
        unit_intervals = (
            self.minimum_decision_coverage,
            self.maximum_outcome_missing_rate,
            self.maximum_drawdown,
            self.maximum_selection_concentration,
            self.minimum_derivatives_context_coverage,
            self.minimum_derivatives_available_rate,
            self.minimum_derivatives_basis_input_source_match_rate,
            self.minimum_selection_control_variant_coverage,
            self.minimum_selection_control_outcome_coverage,
            self.maximum_selection_control_outcome_coverage_gap,
        )
        if any(not 0 <= value <= 1 for value in unit_intervals):
            raise ValueError("coverage, missing rate, drawdown, and concentration use [0, 1]")
        if self.minimum_profit_factor < 0:
            raise ValueError("minimum profit factor cannot be negative")
        if not math.isfinite(self.minimum_selection_control_actual_minus_control):
            raise ValueError("selection-control relative-return threshold must be finite")
        if (
            not math.isfinite(self.economic_round_trip_cost)
            or not 0 <= self.economic_round_trip_cost <= 1
        ):
            raise ValueError("economic round-trip cost must use [0, 1]")
        if not self.required_derivatives_feature_version.strip():
            raise ValueError("required derivatives feature version cannot be blank")
        if self.required_derivatives_basis_input_source is not None and (
            self.required_derivatives_basis_input_source.strip()
            not in {"OFFICIAL", "MARK_INDEX_PROXY"}
        ):
            raise ValueError("required derivatives basis input source is unsupported")
        if (
            self.selection_control_variant_id is not None
            and not self.selection_control_variant_id.strip()
        ):
            raise ValueError("selection control variant ID cannot be blank")


@dataclass(frozen=True, slots=True)
class _Experiment:
    experiment_id: str
    portfolio_id: str
    strategy_version: str
    config_hash: str
    started_at: datetime
    planned_end_at: datetime
    status: str
    starting_equity: float
    completed_at: datetime | None


@dataclass(frozen=True, slots=True)
class _Snapshot:
    snapshot_id: str
    decision_id: str
    decision_time: datetime
    asset: str
    score: float | None
    selected: bool
    candidate_reasons: tuple[str, ...] = ()
    candidate_reasons_recorded: bool = False


@dataclass(frozen=True, slots=True)
class _Outcome:
    snapshot_id: str
    horizon_minutes: int
    evaluated_at: datetime
    status: str
    forward_return: float | None
    mfe: float | None
    mae: float | None


@dataclass(frozen=True, slots=True)
class _SelectionVariant:
    """A persisted selection-rule counterfactual attached to one snapshot."""

    snapshot_id: str
    variant_id: str
    selected: bool


@dataclass(frozen=True, slots=True)
class _PaperDecision:
    decision_id: str
    as_of: datetime
    equity: float
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class _Execution:
    pair: str
    side: str
    quantity: float
    price: float
    fee: float
    realized_pnl: float
    executed_at: datetime


@dataclass(frozen=True, slots=True)
class _Cutoff:
    decision_id: str
    decision_time: datetime
    completed_at: datetime


@dataclass(frozen=True, slots=True)
class _MarketContext:
    decision_id: str
    decision_time: datetime
    payload: dict[str, Any]


class StrategyReviewAnalyzer:
    """Evaluate one frozen experiment without changing either SQLite database."""

    _HORIZONS = (60, 240)
    _COMPLETE_DECISION_STATUSES = frozenset({"EXECUTED", "DRY_RUN"})
    _REPORT_SCHEMA_VERSION = 5
    _GATE_POLICY_VERSION = "strategy-promotion-gates-v3"
    _CALENDAR_TIME_ZONE = ZoneInfo("Asia/Seoul")

    def __init__(
        self,
        observation_database: str | Path,
        paper_database: str | Path,
        thresholds: StrategyReviewThresholds | None = None,
        *,
        busy_timeout_ms: int = 5_000,
        database_url: str | None = None,
        database_schema: str = "investment",
    ) -> None:
        if busy_timeout_ms < 0:
            raise ValueError("busy timeout cannot be negative")
        self.observation_database = Path(observation_database)
        self.paper_database = Path(paper_database)
        self.thresholds = thresholds or StrategyReviewThresholds()
        self.busy_timeout_ms = busy_timeout_ms
        self.database_url = database_url
        self.database_schema = database_schema

    @property
    def _gate_policy_version(self) -> str:
        if self.thresholds.selection_control_variant_id == "v2.6-rule-control":
            return "strategy-promotion-gates-v6-v27-control"
        if self.thresholds.selection_control_variant_id == "v2.5-rule-control":
            return "strategy-promotion-gates-v5-v26-control"
        if (
            self.thresholds.selection_control_variant_id is not None
            or self.thresholds.required_derivatives_basis_input_source is not None
        ):
            return "strategy-promotion-gates-v4-v25-control"
        return self._GATE_POLICY_VERSION

    def analyze(
        self,
        experiment_id: str,
        *,
        portfolio_id: str | None = None,
        review_stage: ReviewStage = "PAPER",
    ) -> dict[str, Any]:
        """Return a strict-JSON-compatible point-in-time promotion report.

        The cutoff is the completion timestamp of the newest rebalance decision
        present in both databases.  All evidence is filtered to that single
        cutoff, so a concurrent scheduler cycle cannot produce a mixed report.
        """

        if review_stage not in {"PAPER", "SHADOW_TO_PAPER"}:
            raise ValueError(f"unsupported review stage: {review_stage}")

        with (
            closing(self._connect_read_only(self.observation_database)) as observation,
            closing(self._connect_read_only(self.paper_database)) as paper,
        ):
            observation.execute("BEGIN")
            paper.execute("BEGIN")
            try:
                experiment = self._load_experiment(observation, experiment_id)
                if portfolio_id is not None and portfolio_id != experiment.portfolio_id:
                    raise ValueError("portfolio override does not match observation experiment")
                selected_portfolio = experiment.portfolio_id
                paper_decisions = self._load_all_paper_decisions(
                    paper, selected_portfolio, experiment.strategy_version
                )
                observed_decisions = self._load_observed_decisions(observation, experiment_id)
                cutoff = self._select_cutoff(observed_decisions, paper_decisions)
                snapshots = self._load_snapshots(observation, experiment_id, cutoff)
                outcomes = self._load_outcomes(observation, experiment_id, cutoff)
                selection_variants = self._load_selection_variants(
                    observation, experiment_id, cutoff
                )
                # Context remains diagnostic for Paper reviews even though its
                # quality gates are required only during Shadow-to-Paper review.
                market_contexts = self._load_market_contexts(observation, experiment_id, cutoff)
                decisions = self._filter_paper_decisions(paper_decisions, cutoff)
                executions = self._load_executions(paper, selected_portfolio, cutoff)
            finally:
                observation.rollback()
                paper.rollback()

        return self._build_report(
            experiment,
            selected_portfolio,
            cutoff,
            snapshots,
            outcomes,
            selection_variants,
            market_contexts,
            decisions,
            executions,
            review_stage,
        )

    def _connect_read_only(self, path: Path) -> Any:
        if self.database_url:
            return postgres_connection(
                self.database_url,
                self.database_schema,
                read_only=True,
            )
        resolved = path.expanduser().resolve(strict=True)
        connection = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA query_only=ON")
        return connection

    @staticmethod
    def _load_experiment(connection: sqlite3.Connection, experiment_id: str) -> _Experiment:
        row = connection.execute(
            """SELECT experiment_id, portfolio_id, strategy_version, config_hash,
                      started_at, planned_end_at, status, starting_equity, completed_at
               FROM observation_experiment WHERE experiment_id=?""",
            (experiment_id,),
        ).fetchone()
        if row is None:
            raise KeyError(experiment_id)
        return _Experiment(
            str(row["experiment_id"]),
            str(row["portfolio_id"]),
            str(row["strategy_version"]),
            str(row["config_hash"]),
            _datetime(row["started_at"]),
            _datetime(row["planned_end_at"]),
            str(row["status"]),
            float(row["starting_equity"]),
            _optional_datetime(row["completed_at"]),
        )

    @staticmethod
    def _load_observed_decisions(
        connection: sqlite3.Connection, experiment_id: str
    ) -> dict[str, datetime]:
        rows = connection.execute(
            """SELECT decision_id, MAX(decision_time) AS decision_time
               FROM decision_snapshot WHERE experiment_id=? GROUP BY decision_id""",
            (experiment_id,),
        ).fetchall()
        return {str(row["decision_id"]): _datetime(row["decision_time"]) for row in rows}

    @classmethod
    def _load_all_paper_decisions(
        cls,
        connection: sqlite3.Connection,
        portfolio_id: str,
        strategy_version: str,
    ) -> tuple[_PaperDecision, ...]:
        rows = connection.execute(
            """SELECT decision_id, as_of, equity, status, created_at
               FROM paper_rebalance_decision
               WHERE portfolio_id=? AND strategy_version=?
               ORDER BY as_of, decision_id""",
            (portfolio_id, strategy_version),
        ).fetchall()
        return tuple(
            _PaperDecision(
                str(row["decision_id"]),
                _datetime(row["as_of"]),
                float(row["equity"]),
                str(row["status"]),
                _datetime(row["created_at"]),
            )
            for row in rows
            if str(row["status"]) in cls._COMPLETE_DECISION_STATUSES
        )

    @staticmethod
    def _select_cutoff(
        observed_decisions: dict[str, datetime],
        paper_decisions: tuple[_PaperDecision, ...],
    ) -> _Cutoff | None:
        common = [item for item in paper_decisions if item.decision_id in observed_decisions]
        if not common:
            return None
        latest = max(common, key=lambda item: (item.created_at, item.decision_id))
        return _Cutoff(
            latest.decision_id,
            observed_decisions[latest.decision_id],
            latest.created_at,
        )

    @staticmethod
    def _load_snapshots(
        connection: sqlite3.Connection,
        experiment_id: str,
        cutoff: _Cutoff | None,
    ) -> tuple[_Snapshot, ...]:
        if cutoff is None:
            return ()
        candidate_reasons_recorded = _table_has_column(
            connection, "decision_snapshot", "candidate_reasons_json"
        )
        candidate_reasons_expression = (
            "candidate_reasons_json" if candidate_reasons_recorded else "NULL"
        )
        rows = connection.execute(
            f"""SELECT snapshot_id, decision_id, decision_time, asset, score, selected,
                      {candidate_reasons_expression} AS candidate_reasons_json
               FROM decision_snapshot
               WHERE experiment_id=? AND decision_time<=?
                 AND (score IS NOT NULL OR selected=1)
               ORDER BY decision_time, decision_id, asset, snapshot_id""",
            (experiment_id, cutoff.decision_time.isoformat()),
        ).fetchall()
        return tuple(
            _Snapshot(
                str(row["snapshot_id"]),
                str(row["decision_id"]),
                _datetime(row["decision_time"]),
                str(row["asset"]),
                _optional_finite_float(row["score"]),
                bool(row["selected"]),
                _candidate_reasons(row["candidate_reasons_json"]),
                candidate_reasons_recorded,
            )
            for row in rows
        )

    @staticmethod
    def _load_outcomes(
        connection: sqlite3.Connection,
        experiment_id: str,
        cutoff: _Cutoff | None,
    ) -> tuple[_Outcome, ...]:
        if cutoff is None:
            return ()
        rows = connection.execute(
            """SELECT o.snapshot_id, o.horizon_minutes, o.evaluated_at,
                      o.status, o.forward_return, o.mfe, o.mae
               FROM decision_outcome_minute o
               JOIN decision_snapshot s ON s.snapshot_id=o.snapshot_id
               WHERE s.experiment_id=? AND s.decision_time<=?
                 AND (s.score IS NOT NULL OR s.selected=1)
                 AND o.evaluated_at<=? AND o.horizon_minutes IN (15, 60, 240)
               ORDER BY s.decision_time, s.decision_id, s.asset, o.horizon_minutes""",
            (
                experiment_id,
                cutoff.decision_time.isoformat(),
                cutoff.completed_at.isoformat(),
            ),
        ).fetchall()
        return tuple(
            _Outcome(
                str(row["snapshot_id"]),
                int(row["horizon_minutes"]),
                _datetime(row["evaluated_at"]),
                str(row["status"]),
                _optional_finite_float(row["forward_return"]),
                _optional_finite_float(row["mfe"]),
                _optional_finite_float(row["mae"]),
            )
            for row in rows
        )

    @staticmethod
    def _load_selection_variants(
        connection: sqlite3.Connection,
        experiment_id: str,
        cutoff: _Cutoff | None,
    ) -> tuple[_SelectionVariant, ...]:
        """Load optional additive rule controls without initializing old databases."""

        if cutoff is None or not _table_exists(connection, "decision_selection_variant"):
            return ()
        rows = connection.execute(
            """SELECT v.snapshot_id, v.variant_id, v.selected
               FROM decision_selection_variant v
               JOIN decision_snapshot s ON s.snapshot_id=v.snapshot_id
               WHERE s.experiment_id=? AND s.decision_time<=?
                 AND (s.score IS NOT NULL OR s.selected=1)
               ORDER BY s.decision_time, s.decision_id, s.asset, v.variant_id""",
            (experiment_id, cutoff.decision_time.isoformat()),
        ).fetchall()
        return tuple(
            _SelectionVariant(
                str(row["snapshot_id"]),
                str(row["variant_id"]),
                bool(row["selected"]),
            )
            for row in rows
        )

    @staticmethod
    def _load_market_contexts(
        connection: sqlite3.Connection,
        experiment_id: str,
        cutoff: _Cutoff | None,
    ) -> tuple[_MarketContext, ...]:
        if cutoff is None:
            return ()
        rows = connection.execute(
            """SELECT decision_id, decision_time, context_json
               FROM decision_market_context
               WHERE experiment_id=? AND decision_time<=?
               ORDER BY decision_time, decision_id""",
            (experiment_id, cutoff.decision_time.isoformat()),
        ).fetchall()
        contexts: list[_MarketContext] = []
        for row in rows:
            try:
                payload = json.loads(str(row["context_json"]))
            except (json.JSONDecodeError, TypeError):
                payload = {}
            contexts.append(
                _MarketContext(
                    str(row["decision_id"]),
                    _datetime(row["decision_time"]),
                    payload if isinstance(payload, dict) else {},
                )
            )
        return tuple(contexts)

    @staticmethod
    def _filter_paper_decisions(
        decisions: tuple[_PaperDecision, ...], cutoff: _Cutoff | None
    ) -> tuple[_PaperDecision, ...]:
        if cutoff is None:
            return ()
        return tuple(
            item
            for item in decisions
            if item.as_of <= cutoff.decision_time and item.created_at <= cutoff.completed_at
        )

    @staticmethod
    def _load_executions(
        connection: sqlite3.Connection,
        portfolio_id: str,
        cutoff: _Cutoff | None,
    ) -> tuple[_Execution, ...]:
        if cutoff is None:
            return ()
        rows = connection.execute(
            """SELECT pair, side, quantity, price, fee, realized_pnl, executed_at
               FROM paper_execution
               WHERE portfolio_id=? AND executed_at<=?
               ORDER BY executed_at, order_id""",
            (portfolio_id, cutoff.completed_at.isoformat()),
        ).fetchall()
        return tuple(
            _Execution(
                str(row["pair"]),
                str(row["side"]),
                float(row["quantity"]),
                float(row["price"]),
                float(row["fee"]),
                float(row["realized_pnl"]),
                _datetime(row["executed_at"]),
            )
            for row in rows
        )

    def _build_report(
        self,
        experiment: _Experiment,
        portfolio_id: str,
        cutoff: _Cutoff | None,
        snapshots: tuple[_Snapshot, ...],
        outcomes: tuple[_Outcome, ...],
        selection_variants: tuple[_SelectionVariant, ...],
        market_contexts: tuple[_MarketContext, ...],
        decisions: tuple[_PaperDecision, ...],
        executions: tuple[_Execution, ...],
        review_stage: ReviewStage,
    ) -> dict[str, Any]:
        thresholds = self.thresholds
        bounded_executions = tuple(
            item for item in executions if item.executed_at >= experiment.started_at
        )
        bounded_decisions = tuple(item for item in decisions if item.as_of >= experiment.started_at)
        cutoff_time = cutoff.completed_at if cutoff else None
        decision_times = sorted({item.decision_time for item in snapshots})
        decision_ids = {item.decision_id for item in snapshots}
        actual_cycles = len(decision_times)
        expected_cycles = self._expected_cycles(experiment.started_at, cutoff_time)
        coverage = min(1.0, actual_cycles / expected_cycles) if expected_cycles > 0 else None
        calendar_days = len(
            {
                item.astimezone(self._CALENDAR_TIME_ZONE).date().isoformat()
                for item in decision_times
            }
        )
        outcomes_by_key = {(item.snapshot_id, item.horizon_minutes): item for item in outcomes}
        horizon_quality: dict[str, dict[str, Any]] = {}
        signal_quality: dict[str, dict[str, Any]] = {}
        temporal_stability: dict[str, dict[str, Any]] = {}
        for horizon in self._HORIZONS:
            quality, signal, stability = self._horizon_analysis(
                horizon, cutoff_time, snapshots, outcomes_by_key
            )
            horizon_quality[str(horizon)] = quality
            signal_quality[str(horizon)] = signal
            temporal_stability[str(horizon)] = stability
        same_input_control = self._same_input_rule_control(
            cutoff,
            snapshots,
            outcomes_by_key,
            selection_variants,
        )
        signal_quality["240"]["sameInputRuleControl"] = same_input_control
        if same_input_control["variantId"] == "v2.4-rule-control":
            # Schema-3 compatibility for existing V2.5 consumers.
            signal_quality["240"]["sameInputV24RuleControl"] = same_input_control
        signal_quality["economicAlphaBaseline"] = self._economic_alpha_baseline(
            cutoff,
            snapshots,
            outcomes_by_key,
        )
        signal_quality["incrementalAlphaVariants"] = self._incremental_alpha_variants(
            cutoff,
            snapshots,
            outcomes_by_key,
            selection_variants,
        )
        signal_quality["crowdingGateDisagreement"] = self._crowding_gate_disagreement(
            cutoff,
            snapshots,
            outcomes_by_key,
            market_contexts,
        )

        selected_by_cohort: dict[str, set[str]] = defaultdict(set)
        for item in snapshots:
            if item.selected:
                selected_by_cohort[item.decision_id].add(item.asset)
        selection_cohorts = len(selected_by_cohort)
        selected_counts = Counter(
            asset for assets in selected_by_cohort.values() for asset in assets
        )
        selected_total = sum(selected_counts.values())
        concentration = (
            max(selected_counts.values()) / selection_cohorts if selection_cohorts else None
        )
        paper_metrics = self._paper_metrics(
            experiment.starting_equity, bounded_decisions, bounded_executions
        )
        derivatives_quality = self._derivatives_context_quality(
            decision_ids,
            market_contexts,
        )
        gates = self._evaluate_gates(
            calendar_days,
            coverage,
            horizon_quality,
            signal_quality,
            temporal_stability,
            paper_metrics,
            concentration,
            derivatives_quality,
            review_stage,
        )
        readiness_names = {
            "MINIMUM_CALENDAR_DAYS",
            "MINIMUM_MATURE_1H_COHORTS",
            "MINIMUM_MATURE_4H_COHORTS",
        }
        readiness_incomplete = any(gates[name]["passed"] is not True for name in readiness_names)
        required_gates = tuple(item for item in gates.values() if item["required"])
        ready_decision = (
            "SHADOW_READY_FOR_PAPER" if review_stage == "SHADOW_TO_PAPER" else "PROMOTION_READY"
        )
        if readiness_incomplete:
            decision = "COLLECTING"
        elif all(item["passed"] is True for item in required_gates):
            decision = ready_decision
        else:
            decision = "REJECTED"
        failed = [
            name for name, item in gates.items() if item["required"] and item["passed"] is False
        ]
        pending = [
            name for name, item in gates.items() if item["required"] and item["passed"] is None
        ]
        max_scored_missing = _finite_max(
            horizon_quality[str(value)]["scoredMissingRate"] for value in self._HORIZONS
        )
        max_selected_missing = _finite_max(
            horizon_quality[str(value)]["selectedMissingRate"] for value in self._HORIZONS
        )
        analysis_id = self._analysis_id(experiment, portfolio_id, cutoff, review_stage)
        return {
            "schemaVersion": self._REPORT_SCHEMA_VERSION,
            "gatePolicyVersion": self._gate_policy_version,
            "reviewStage": review_stage,
            "analysisId": analysis_id,
            "analysisCutoff": cutoff.completed_at.isoformat() if cutoff else None,
            "cutoffDecision": (
                {
                    "decisionId": cutoff.decision_id,
                    "decisionTime": cutoff.decision_time.isoformat(),
                    "completedAt": cutoff.completed_at.isoformat(),
                }
                if cutoff
                else None
            ),
            "experiment": {
                "experimentId": experiment.experiment_id,
                "portfolioId": portfolio_id,
                "recordedPortfolioId": experiment.portfolio_id,
                "strategyVersion": experiment.strategy_version,
                "configHash": experiment.config_hash,
                "status": experiment.status,
                "startedAt": experiment.started_at.isoformat(),
                "plannedEndAt": experiment.planned_end_at.isoformat(),
                "completedAt": (
                    experiment.completed_at.isoformat() if experiment.completed_at else None
                ),
            },
            "thresholds": _camelized_thresholds(thresholds),
            "dataQuality": {
                "calendarDays": calendar_days,
                "actualDecisionCycles": actual_cycles,
                "expectedDecisionCycles": expected_cycles,
                "decisionCoverage": coverage,
                "candidateSnapshots": len(snapshots),
                "outcomeRows": len(outcomes),
                "maximumScoredMissingRate": max_scored_missing,
                "maximumSelectedMissingRate": max_selected_missing,
                "horizons": horizon_quality,
                "derivativesContext": derivatives_quality,
            },
            "signalQuality": signal_quality,
            "temporalStability": temporal_stability,
            "selection": {
                "totalSelections": selected_total,
                "cohortsWithSelection": selection_cohorts,
                "assetCounts": dict(sorted(selected_counts.items())),
                "maximumAssetConcentration": concentration,
            },
            "paperPerformance": paper_metrics,
            "gates": gates,
            "decision": decision,
            "promotionEligible": decision == "PROMOTION_READY",
            "paperActivationEligible": decision in {"PROMOTION_READY", "SHADOW_READY_FOR_PAPER"},
            "failedGates": failed,
            "pendingGates": pending,
            "methodology": {
                "scoreIc": "mean within-decision Spearman rank correlation",
                "selectedSpread": (
                    "mean of within-decision selected minus scored-nonselected means"
                ),
                "missingRate": ("missing, invalid, or unresolved outcomes among settled snapshots"),
                "maximumDrawdown": "positive peak-to-trough loss fraction",
                "calendarDays": "distinct Asia/Seoul decision dates",
                "selectionConcentration": (
                    "cohorts containing an asset divided by cohorts with any selection"
                ),
                "temporalStability": (
                    "positive selected-minus-nonselected paired cohort spread in both time halves"
                ),
                "lossValidation": (
                    "descriptive per-horizon selected/nonselected negative-return rates; "
                    "paired loss means the selected cohort both lost and trailed its "
                    "nonselected alternatives, not a causal A/B comparison"
                ),
                "fourHourGuardCounterfactual": (
                    "descriptive 4h comparison of recorded falling-knife blocks and selected "
                    "candidates; guard-only means the guard was the only recorded candidate reason"
                ),
                "sameInputRuleControl": (
                    "same frozen-input strategy rule control identified by variantId; it is "
                    "not a historical portfolio replay or a causal performance proof"
                ),
                "derivativesContext": (
                    "one exact-version point-in-time BTC context per shadow decision; "
                    "only AVAILABLE rows count as usable"
                ),
                "basisCoverage": (
                    "official and Mark--Index raw Basis rates use recorded context rows; "
                    "signal-input coverage uses all decision cohorts"
                ),
                "cutoff": ("latest EXECUTED/DRY_RUN decision present in both SQLite databases"),
                "economicAlpha": (
                    "equal-weight decision-cohort forward returns; net estimates subtract the "
                    "configured round-trip cost once per selected opportunity"
                ),
                "incrementalAlphaVariants": (
                    "same frozen-input selection variants A/B/C/D; no portfolio-state replay, "
                    "causal claim, or execution simulation"
                ),
                "crowdingGateDisagreement": (
                    "descriptive outcomes where the frozen production state gate and a numeric "
                    "policy-only crowding gate disagreed"
                ),
            },
        }

    def _analysis_id(
        self,
        experiment: _Experiment,
        portfolio_id: str,
        cutoff: _Cutoff | None,
        review_stage: ReviewStage,
    ) -> str:
        identity = {
            "experimentId": experiment.experiment_id,
            "portfolioId": portfolio_id,
            "configHash": experiment.config_hash,
            "reportSchemaVersion": self._REPORT_SCHEMA_VERSION,
            "gatePolicyVersion": self._gate_policy_version,
            "reviewStage": review_stage,
            "thresholds": _camelized_thresholds(self.thresholds),
            "cutoffDecisionId": cutoff.decision_id if cutoff else None,
            "cutoffCompletedAt": cutoff.completed_at.isoformat() if cutoff else None,
        }
        canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
        return f"review-{sha256(canonical.encode()).hexdigest()[:24]}"

    def _expected_cycles(self, started_at: datetime, cutoff: datetime | None) -> int:
        if cutoff is None or cutoff < started_at:
            return 0
        interval = self.thresholds.decision_interval_minutes * 60
        return max(1, int((cutoff - started_at).total_seconds() // interval))

    def _horizon_analysis(
        self,
        horizon: int,
        cutoff: datetime | None,
        snapshots: tuple[_Snapshot, ...],
        outcomes: dict[tuple[str, int], _Outcome],
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if cutoff is None:
            mature: tuple[_Snapshot, ...] = ()
        else:
            delay = timedelta(minutes=horizon + self.thresholds.outcome_settlement_grace_minutes)
            mature = tuple(item for item in snapshots if item.decision_time + delay <= cutoff)
        scored = tuple(item for item in mature if item.score is not None)
        selected = tuple(item for item in mature if item.selected)
        scored_complete = tuple(
            item for item in scored if _is_complete(outcomes.get((item.snapshot_id, horizon)))
        )
        selected_complete = tuple(
            item for item in selected if _is_complete(outcomes.get((item.snapshot_id, horizon)))
        )
        scored_missing = _missing_rate(len(scored), len(scored_complete))
        selected_missing = _missing_rate(len(selected), len(selected_complete))
        mature_cohorts = len({item.decision_id for item in scored})

        completed_scored = self._completed_values(scored_complete, horizon, outcomes)
        selected_scored_outcomes = tuple(
            outcomes[(item.snapshot_id, horizon)] for item in scored_complete if item.selected
        )
        by_cohort: dict[str, list[tuple[_Snapshot, float]]] = defaultdict(list)
        for snapshot, value in completed_scored:
            by_cohort[snapshot.decision_id].append((snapshot, value))
        selected_values: list[float] = []
        nonselected_values: list[float] = []
        selected_cohort_means: list[float] = []
        nonselected_cohort_means: list[float] = []
        paired_spreads: list[float] = []
        paired_cohort_means: list[tuple[float, float]] = []
        cohort_ics: list[float] = []
        analyzable_cohorts = 0
        for cohort in sorted(by_cohort):
            values = by_cohort[cohort]
            selected_returns = [value for item, value in values if item.selected]
            nonselected_returns = [value for item, value in values if not item.selected]
            selected_values.extend(selected_returns)
            nonselected_values.extend(nonselected_returns)
            if selected_returns:
                selected_cohort_means.append(fmean(selected_returns))
            if nonselected_returns:
                nonselected_cohort_means.append(fmean(nonselected_returns))
            if selected_returns and nonselected_returns:
                analyzable_cohorts += 1
                selected_cohort_mean = fmean(selected_returns)
                nonselected_cohort_mean = fmean(nonselected_returns)
                paired_cohort_means.append((selected_cohort_mean, nonselected_cohort_mean))
                paired_spreads.append(selected_cohort_mean - nonselected_cohort_mean)
            scores = [float(item.score) for item, _ in values if item.score is not None]
            returns = [value for item, value in values if item.score is not None]
            if len(scores) >= 3:
                correlation = _spearman(scores, returns)
                if correlation is not None:
                    cohort_ics.append(correlation)

        selected_mean = _mean_or_none(selected_cohort_means)
        nonselected_mean = _mean_or_none(nonselected_cohort_means)
        raw_spread = (
            selected_mean - nonselected_mean
            if selected_mean is not None and nonselected_mean is not None
            else None
        )
        signal = {
            "horizonMinutes": horizon,
            "selectedObservations": len(selected_values),
            "scoredNonselectedObservations": len(nonselected_values),
            "analyzableCohorts": analyzable_cohorts,
            "selectedMeanForwardReturn": selected_mean,
            "scoredNonselectedMeanForwardReturn": nonselected_mean,
            "pooledSelectedMeanForwardReturn": _mean_or_none(selected_values),
            "pooledScoredNonselectedMeanForwardReturn": _mean_or_none(nonselected_values),
            "rawMeanSpread": raw_spread,
            "pairedCohortSpread": _mean_or_none(paired_spreads),
            "scoreIc": _mean_or_none(cohort_ics),
            "scoreIcCohorts": len(cohort_ics),
            "lossValidation": self._selection_loss_validation(
                selected_values,
                nonselected_values,
                selected_cohort_means,
                nonselected_cohort_means,
                paired_cohort_means,
                selected_scored_outcomes,
            ),
        }
        if horizon == 240:
            signal["fourHourGuardCounterfactual"] = self._four_hour_guard_counterfactual(
                scored,
                outcomes,
            )
        stability = self._temporal_stability(completed_scored)
        quality = {
            "horizonMinutes": horizon,
            "matureCohorts": mature_cohorts,
            "matureScoredSnapshots": len(scored),
            "completedScoredSnapshots": len(scored_complete),
            "scoredMissingSnapshots": len(scored) - len(scored_complete),
            "scoredMissingRate": scored_missing,
            "matureSelectedSnapshots": len(selected),
            "completedSelectedSnapshots": len(selected_complete),
            "selectedMissingSnapshots": len(selected) - len(selected_complete),
            "selectedMissingRate": selected_missing,
        }
        return quality, signal, stability

    @staticmethod
    def _selection_loss_validation(
        selected_values: list[float],
        nonselected_values: list[float],
        selected_cohort_means: list[float],
        nonselected_cohort_means: list[float],
        paired_cohort_means: list[tuple[float, float]],
        selected_outcomes: tuple[_Outcome, ...],
    ) -> dict[str, Any]:
        """Expose whether a negative horizon is market-wide or selection-specific.

        Observation rates use completed scored snapshots.  Cohort rates first
        average each side within a decision, so a large candidate universe does
        not give that decision disproportionate weight.  The paired loss rate
        is the most direct diagnostic for the V2.4 four-hour failure mode:
        selected assets lost and also trailed their nonselected alternatives.
        """

        selected_negative_values = [value for value in selected_values if value < 0]
        nonselected_negative_values = [value for value in nonselected_values if value < 0]
        selected_negative_cohorts = [value for value in selected_cohort_means if value < 0]
        nonselected_negative_cohorts = [value for value in nonselected_cohort_means if value < 0]
        selected_underperformed = [
            (selected, nonselected)
            for selected, nonselected in paired_cohort_means
            if selected < nonselected
        ]
        selected_negative_and_underperformed = [
            (selected, nonselected)
            for selected, nonselected in paired_cohort_means
            if selected < 0 and selected < nonselected
        ]
        paired_spreads = [selected - nonselected for selected, nonselected in paired_cohort_means]
        selected_mfe = [item.mfe for item in selected_outcomes if item.mfe is not None]
        selected_mae = [item.mae for item in selected_outcomes if item.mae is not None]
        return {
            "selectedMeanForwardReturn": _mean_or_none(selected_cohort_means),
            "scoredNonselectedMeanForwardReturn": _mean_or_none(nonselected_cohort_means),
            "pairedCohortSpread": _mean_or_none(paired_spreads),
            "selectedNegativeObservations": len(selected_negative_values),
            "selectedNegativeObservationRate": _rate(
                len(selected_negative_values), len(selected_values)
            ),
            "scoredNonselectedNegativeObservations": len(nonselected_negative_values),
            "scoredNonselectedNegativeObservationRate": _rate(
                len(nonselected_negative_values), len(nonselected_values)
            ),
            "selectedMeanNegativeForwardReturn": _mean_or_none(selected_negative_values),
            "scoredNonselectedMeanNegativeForwardReturn": _mean_or_none(
                nonselected_negative_values
            ),
            "selectedNegativeCohorts": len(selected_negative_cohorts),
            "selectedNegativeCohortRate": _rate(
                len(selected_negative_cohorts), len(selected_cohort_means)
            ),
            "scoredNonselectedNegativeCohorts": len(nonselected_negative_cohorts),
            "scoredNonselectedNegativeCohortRate": _rate(
                len(nonselected_negative_cohorts), len(nonselected_cohort_means)
            ),
            "pairedCohorts": len(paired_cohort_means),
            "selectedUnderperformedCohorts": len(selected_underperformed),
            "selectedUnderperformanceRate": _rate(
                len(selected_underperformed), len(paired_cohort_means)
            ),
            "selectedNegativeAndUnderperformedCohorts": len(selected_negative_and_underperformed),
            "selectedNegativeAndUnderperformedRate": _rate(
                len(selected_negative_and_underperformed), len(paired_cohort_means)
            ),
            "selectedMfeAvailableObservations": len(selected_mfe),
            "selectedMfeAvailabilityRate": _rate(len(selected_mfe), len(selected_outcomes)),
            "selectedMeanMfe": _mean_or_none(selected_mfe),
            "selectedMedianMfe": _median_or_none(selected_mfe),
            "selectedMaeAvailableObservations": len(selected_mae),
            "selectedMaeAvailabilityRate": _rate(len(selected_mae), len(selected_outcomes)),
            "selectedMeanMae": _mean_or_none(selected_mae),
            "selectedMedianMae": _median_or_none(selected_mae),
        }

    @staticmethod
    def _four_hour_guard_counterfactual(
        scored: tuple[_Snapshot, ...],
        outcomes: dict[tuple[str, int], _Outcome],
    ) -> dict[str, Any]:
        """Compare the recorded falling-knife blocks with selected outcomes.

        This is deliberately descriptive: blocked and selected candidates are
        different cross-sectional cohorts, not a same-input A/B treatment.  A
        guard-only row is a narrower approximation of an otherwise-passing
        entry, but it still cannot establish causal guard benefit on its own.
        """

        guard_reason = "NEW_ENTRY_BLOCKED_BY_4H_FALLING_KNIFE_GUARD"
        guard_candidates = tuple(item for item in scored if guard_reason in item.candidate_reasons)
        guard_only_candidates = tuple(
            item for item in guard_candidates if set(item.candidate_reasons) == {guard_reason}
        )
        selected_candidates = tuple(item for item in scored if item.selected)
        return {
            "guardReason": guard_reason,
            "comparisonType": "DESCRIPTIVE_NOT_SAME_COHORT_AB",
            "candidateReasonsRecorded": any(item.candidate_reasons_recorded for item in scored),
            "selected": _outcome_group_summary(selected_candidates, 240, outcomes),
            "guardCandidates": _outcome_group_summary(guard_candidates, 240, outcomes),
            "guardOnlyCandidates": _outcome_group_summary(
                guard_only_candidates,
                240,
                outcomes,
            ),
        }

    def _economic_alpha_baseline(
        self,
        cutoff: _Cutoff | None,
        snapshots: tuple[_Snapshot, ...],
        outcomes: dict[tuple[str, int], _Outcome],
    ) -> dict[str, Any]:
        """Report whether frozen selections clear an explicit economic hurdle."""

        cost = self.thresholds.economic_round_trip_cost
        horizons: dict[str, Any] = {}
        for horizon in (15, 60, 240):
            mature = self._mature_snapshots(cutoff, snapshots, horizon)
            by_decision: dict[str, list[_Snapshot]] = defaultdict(list)
            for snapshot in mature:
                by_decision[snapshot.decision_id].append(snapshot)
            selected_means: list[float] = []
            nonselected_means: list[float] = []
            paired_spreads: list[float] = []
            selected_outcomes: list[_Outcome] = []
            deciles: dict[int, list[float]] = defaultdict(list)
            for cohort in by_decision.values():
                completed = [
                    (item, outcome)
                    for item in cohort
                    if (outcome := outcomes.get((item.snapshot_id, horizon))) is not None
                    and _is_complete(outcome)
                    and outcome.forward_return is not None
                ]
                selected = [
                    outcome.forward_return
                    for item, outcome in completed
                    if item.selected and outcome.forward_return is not None
                ]
                nonselected = [
                    outcome.forward_return
                    for item, outcome in completed
                    if not item.selected and outcome.forward_return is not None
                ]
                selected_outcomes.extend(outcome for item, outcome in completed if item.selected)
                if selected:
                    selected_means.append(fmean(selected))
                if nonselected:
                    nonselected_means.append(fmean(nonselected))
                if selected and nonselected:
                    paired_spreads.append(fmean(selected) - fmean(nonselected))
                ranked = sorted(
                    (
                        (item, outcome.forward_return)
                        for item, outcome in completed
                        if item.score is not None and outcome.forward_return is not None
                    ),
                    key=lambda value: (float(value[0].score or 0), value[0].snapshot_id),
                )
                for index, (_, value) in enumerate(ranked):
                    decile = min(10, (index * 10) // max(1, len(ranked)) + 1)
                    deciles[decile].append(value)
            selected_gross = _mean_or_none(selected_means)
            selected_net = selected_gross - cost if selected_gross is not None else None
            mfe = [item.mfe for item in selected_outcomes if item.mfe is not None]
            mae = [item.mae for item in selected_outcomes if item.mae is not None]
            horizons[str(horizon)] = {
                "horizonMinutes": horizon,
                "aggregation": "EQUAL_WEIGHT_DECISION_COHORT_MEANS",
                "matureDecisionCohorts": len(by_decision),
                "selectedCompletedCohorts": len(selected_means),
                "selectedMeanGrossReturn": selected_gross,
                "selectedMedianGrossReturn": _median_or_none(selected_means),
                "selectedP05GrossReturn": _percentile_or_none(selected_means, 0.05),
                "estimatedRoundTripCost": cost,
                "selectedMeanNetReturn": selected_net,
                "averageTradeExpectancyAfterCost": selected_net,
                "grossPositiveCohortRate": _rate(
                    sum(value > 0 for value in selected_means), len(selected_means)
                ),
                "netPositiveCohortRate": _rate(
                    sum(value > cost for value in selected_means), len(selected_means)
                ),
                "scoredNonselectedMeanReturn": _mean_or_none(nonselected_means),
                "pairedSelectedMinusNonselectedMean": _mean_or_none(paired_spreads),
                "selectedMeanMfe": _mean_or_none(mfe),
                "selectedMedianMfe": _median_or_none(mfe),
                "selectedMeanMae": _mean_or_none(mae),
                "selectedMedianMae": _median_or_none(mae),
                "scoreDeciles": {
                    str(decile): {
                        "observations": len(values),
                        "meanForwardReturn": _mean_or_none(values),
                    }
                    for decile, values in sorted(deciles.items())
                },
            }
        return {
            "primaryHorizonMinutes": 60,
            "estimatedRoundTripCost": cost,
            "horizons": horizons,
        }

    def _incremental_alpha_variants(
        self,
        cutoff: _Cutoff | None,
        snapshots: tuple[_Snapshot, ...],
        outcomes: dict[tuple[str, int], _Outcome],
        variants: tuple[_SelectionVariant, ...],
    ) -> dict[str, Any]:
        """Compare persisted A/B/C/D selections without replaying portfolio state."""

        labels = {
            "v2.7a-momentum-only": "A_MOMENTUM_ONLY",
            "v2.7b-raw-derivatives": "B_RAW_DERIVATIVES",
            "v2.7c-crowding-only": "C_CROWDING_ONLY",
            "v2.7d-production-gates": "D_PRODUCTION_GATES",
        }
        selected_by_variant = {
            variant_id: {
                row.snapshot_id for row in variants if row.variant_id == variant_id and row.selected
            }
            for variant_id in labels
        }
        recorded_by_variant = {
            variant_id: {row.snapshot_id for row in variants if row.variant_id == variant_id}
            for variant_id in labels
        }
        snapshot_by_id = {row.snapshot_id: row for row in snapshots}
        all_snapshot_ids = set(snapshot_by_id)
        coverage = {
            variant_id: _rate(len(recorded_by_variant[variant_id]), len(all_snapshot_ids))
            for variant_id in labels
        }
        opportunity_decisions = {
            snapshot_by_id[snapshot_id].decision_id
            for snapshot_id in selected_by_variant["v2.7a-momentum-only"]
            if snapshot_id in snapshot_by_id
        }
        horizon_reports: dict[str, Any] = {}
        for horizon in (15, 60, 240):
            mature = self._mature_snapshots(cutoff, snapshots, horizon)
            mature_by_decision: dict[str, list[_Snapshot]] = defaultdict(list)
            for row in mature:
                mature_by_decision[row.decision_id].append(row)
            mature_opportunities = sorted(set(mature_by_decision) & opportunity_decisions)
            utilities: dict[str, dict[str, float]] = {}
            arms: dict[str, Any] = {}
            for variant_id, label in labels.items():
                arm_utility: dict[str, float] = {}
                accepted_returns: list[float] = []
                accepted_cohorts = 0
                missing_accepted_cohorts = 0
                for decision_id in mature_opportunities:
                    selected = [
                        row
                        for row in mature_by_decision[decision_id]
                        if row.snapshot_id in selected_by_variant[variant_id]
                    ]
                    if not selected:
                        arm_utility[decision_id] = 0.0
                        continue
                    accepted_cohorts += 1
                    values = _completed_forward_returns(selected, horizon, outcomes)
                    if not values:
                        missing_accepted_cohorts += 1
                        continue
                    gross = fmean(values)
                    accepted_returns.append(gross)
                    arm_utility[decision_id] = gross - self.thresholds.economic_round_trip_cost
                utilities[variant_id] = arm_utility
                ordered_utility = [
                    arm_utility[key] for key in mature_opportunities if key in arm_utility
                ]
                first, second = _chronological_halves(ordered_utility)
                arms[label] = {
                    "variantId": variant_id,
                    "opportunityCohorts": len(mature_opportunities),
                    "acceptedCohorts": accepted_cohorts,
                    "blockedCohorts": len(mature_opportunities) - accepted_cohorts,
                    "missingAcceptedOutcomeCohorts": missing_accepted_cohorts,
                    "acceptedMeanGrossReturn": _mean_or_none(accepted_returns),
                    "acceptedMedianGrossReturn": _median_or_none(accepted_returns),
                    "acceptedP05GrossReturn": _percentile_or_none(accepted_returns, 0.05),
                    "acceptedNetPositiveRate": _rate(
                        sum(
                            value > self.thresholds.economic_round_trip_cost
                            for value in accepted_returns
                        ),
                        len(accepted_returns),
                    ),
                    "opportunityUtilityMeanAfterCost": _mean_or_none(ordered_utility),
                    "opportunityUtilityMedianAfterCost": _median_or_none(ordered_utility),
                    "opportunityUtilityP05AfterCost": _percentile_or_none(ordered_utility, 0.05),
                    "firstHalfUtilityMeanAfterCost": _mean_or_none(first),
                    "secondHalfUtilityMeanAfterCost": _mean_or_none(second),
                }
            comparisons: dict[str, Any] = {}
            for left, right in (
                ("v2.7a-momentum-only", "v2.7b-raw-derivatives"),
                ("v2.7b-raw-derivatives", "v2.7c-crowding-only"),
                ("v2.7c-crowding-only", "v2.7d-production-gates"),
            ):
                common = [
                    decision_id
                    for decision_id in mature_opportunities
                    if decision_id in utilities[left] and decision_id in utilities[right]
                ]
                differences = [utilities[right][key] - utilities[left][key] for key in common]
                comparisons[f"{labels[left]}_TO_{labels[right]}"] = {
                    "pairedOpportunityCohorts": len(common),
                    "incrementalUtilityMeanAfterCost": _mean_or_none(differences),
                    "improvedCohortRate": _rate(
                        sum(value > 0 for value in differences), len(differences)
                    ),
                }
            horizon_reports[str(horizon)] = {
                "horizonMinutes": horizon,
                "arms": arms,
                "incrementalComparisons": comparisons,
            }
        availability = (
            "NOT_RECORDED"
            if not variants
            else "AVAILABLE"
            if all(value == 1.0 for value in coverage.values())
            else "PARTIAL"
        )
        return {
            "comparisonType": "SAME_FROZEN_INPUT_SELECTION_ONLY_NOT_CAUSAL",
            "availability": availability,
            "variantCoverage": {labels[key]: value for key, value in coverage.items()},
            "horizons": horizon_reports,
        }

    def _crowding_gate_disagreement(
        self,
        cutoff: _Cutoff | None,
        snapshots: tuple[_Snapshot, ...],
        outcomes: dict[tuple[str, int], _Outcome],
        contexts: tuple[_MarketContext, ...],
    ) -> dict[str, Any]:
        disagreement_decisions = {
            row.decision_id
            for row in contexts
            if _mapping(row.payload.get("decisionDiagnostics")).get("crowdingGateDisagreement")
            is True
        }
        reports: dict[str, Any] = {}
        for horizon in (15, 60, 240):
            mature = self._mature_snapshots(cutoff, snapshots, horizon)
            selected = tuple(
                row for row in mature if row.selected and row.decision_id in disagreement_decisions
            )
            values_by_decision: dict[str, list[float]] = defaultdict(list)
            for row in selected:
                outcome = outcomes.get((row.snapshot_id, horizon))
                if (
                    _is_complete(outcome)
                    and outcome is not None
                    and outcome.forward_return is not None
                ):
                    values_by_decision[row.decision_id].append(outcome.forward_return)
            cohort_values = [fmean(values) for values in values_by_decision.values()]
            mean_gross = _mean_or_none(cohort_values)
            reports[str(horizon)] = {
                "horizonMinutes": horizon,
                "matureDisagreementDecisionCohorts": len(
                    disagreement_decisions & {row.decision_id for row in mature}
                ),
                "completedSelectedCohorts": len(cohort_values),
                "selectedMeanGrossReturn": mean_gross,
                "selectedP05GrossReturn": _percentile_or_none(cohort_values, 0.05),
                "selectedMeanNetReturn": (
                    mean_gross - self.thresholds.economic_round_trip_cost
                    if mean_gross is not None
                    else None
                ),
                "selectedNetPositiveRate": _rate(
                    sum(
                        value > self.thresholds.economic_round_trip_cost for value in cohort_values
                    ),
                    len(cohort_values),
                ),
            }
        return {
            "recordedDecisionContexts": len(contexts),
            "disagreementDecisionCohorts": len(disagreement_decisions),
            "horizons": reports,
        }

    def _mature_snapshots(
        self,
        cutoff: _Cutoff | None,
        snapshots: tuple[_Snapshot, ...],
        horizon: int,
    ) -> tuple[_Snapshot, ...]:
        if cutoff is None:
            return ()
        delay = timedelta(minutes=horizon + self.thresholds.outcome_settlement_grace_minutes)
        return tuple(row for row in snapshots if row.decision_time + delay <= cutoff.completed_at)

    def _same_input_rule_control(
        self,
        cutoff: _Cutoff | None,
        snapshots: tuple[_Snapshot, ...],
        outcomes: dict[tuple[str, int], _Outcome],
        variants: tuple[_SelectionVariant, ...],
    ) -> dict[str, Any]:
        """Compare the actual selection with its persisted same-input rule control."""

        variant_id = self.thresholds.selection_control_variant_id or "v2.4-rule-control"
        horizon = 240
        if cutoff is None:
            mature: tuple[_Snapshot, ...] = ()
        else:
            delay = timedelta(minutes=horizon + self.thresholds.outcome_settlement_grace_minutes)
            mature = tuple(
                item for item in snapshots if item.decision_time + delay <= cutoff.completed_at
            )
        mature_ids = {row.snapshot_id for row in mature}
        target_variants = tuple(item for item in variants if item.variant_id == variant_id)
        control_by_snapshot = {
            item.snapshot_id: item for item in target_variants if item.snapshot_id in mature_ids
        }
        snapshots_by_decision: dict[str, tuple[_Snapshot, ...]] = {}
        actual_by_decision: dict[str, set[str]] = defaultdict(set)
        control_by_decision: dict[str, set[str]] = defaultdict(set)
        control_rows_by_decision: dict[str, set[str]] = defaultdict(set)
        times: dict[str, datetime] = {}
        for item in mature:
            times[item.decision_id] = item.decision_time
            snapshots_by_decision[item.decision_id] = (
                *snapshots_by_decision.get(item.decision_id, ()),
                item,
            )
            if item.selected:
                actual_by_decision[item.decision_id].add(item.snapshot_id)
            control = control_by_snapshot.get(item.snapshot_id)
            if control is not None:
                control_rows_by_decision[item.decision_id].add(item.snapshot_id)
                if control.selected:
                    control_by_decision[item.decision_id].add(item.snapshot_id)
        decision_ids = sorted(
            {item.decision_id for item in mature},
            key=lambda value: (times[value], value),
        )
        complete_control_decision_ids = tuple(
            decision_id
            for decision_id in decision_ids
            if {item.snapshot_id for item in snapshots_by_decision[decision_id]}
            == control_rows_by_decision[decision_id]
        )
        complete_control_decision_id_set = set(complete_control_decision_ids)
        comparable_snapshots = tuple(
            item for item in mature if item.decision_id in complete_control_decision_id_set
        )
        actual_selected = tuple(item for item in comparable_snapshots if item.selected)
        control_selected = tuple(
            item
            for item in comparable_snapshots
            if (control := control_by_snapshot.get(item.snapshot_id)) is not None
            and control.selected
        )
        actual_set = {item.snapshot_id for item in actual_selected}
        control_set = {item.snapshot_id for item in control_selected}
        union = actual_set | control_set
        changed_decisions = [
            value
            for value in complete_control_decision_ids
            if actual_by_decision[value] != control_by_decision[value]
        ]
        paired: list[tuple[str, float, float]] = []
        for decision_id in complete_control_decision_ids:
            actual_values = _completed_forward_returns(
                (item for item in snapshots_by_decision[decision_id] if item.selected),
                horizon,
                outcomes,
            )
            control_values = _completed_forward_returns(
                (
                    item
                    for item in snapshots_by_decision[decision_id]
                    if control_by_snapshot.get(item.snapshot_id) is not None
                    and control_by_snapshot[item.snapshot_id].selected
                ),
                horizon,
                outcomes,
            )
            if actual_values and control_values:
                paired.append((decision_id, fmean(actual_values), fmean(control_values)))
        paired_differences = [actual - control for _, actual, control in paired]
        first_half, second_half = _chronological_halves(paired_differences)
        first_half_mean = _mean_or_none(first_half)
        second_half_mean = _mean_or_none(second_half)
        temporal = {
            "pairedCohorts": len(paired),
            "actualMinusControlMean": _mean_or_none(paired_differences),
            "actualOutperformedCohorts": sum(value > 0 for value in paired_differences),
            "actualUnderperformedCohorts": sum(value < 0 for value in paired_differences),
            "actualNonnegativeSpreadRate": _rate(
                sum(value >= 0 for value in paired_differences), len(paired_differences)
            ),
            "firstHalfPairedCohorts": len(first_half),
            "firstHalfActualMinusControl": first_half_mean,
            "secondHalfPairedCohorts": len(second_half),
            "secondHalfActualMinusControl": second_half_mean,
            "bothHalvesNonnegative": (
                first_half_mean >= 0 and second_half_mean >= 0
                if first_half_mean is not None and second_half_mean is not None
                else None
            ),
        }
        coverage = _rate(len(control_by_snapshot), len(mature_ids))
        if not target_variants:
            availability = "NOT_RECORDED"
        elif not mature:
            availability = "PENDING_OUTCOMES"
        elif coverage != 1.0:
            availability = "INCOMPLETE_CONTROL"
        elif not paired:
            availability = "PENDING_OUTCOMES"
        else:
            availability = "AVAILABLE"
        return {
            "variantId": variant_id,
            "comparisonType": (
                "SAME_FROZEN_INPUT_RULE_CONTROL_NOT_HISTORICAL_REPLAY_OR_CAUSAL_PROOF"
            ),
            "availability": availability,
            "coverage": {
                "matureScoredSnapshots": len(mature),
                "controlRows": len(control_by_snapshot),
                "controlCoverage": coverage,
                "missingControlRows": len(mature_ids) - len(control_by_snapshot),
                "matureDecisionCohorts": len(decision_ids),
                "completeControlDecisionCohorts": len(complete_control_decision_ids),
                "incompleteControlDecisionCohorts": (
                    len(decision_ids) - len(complete_control_decision_ids)
                ),
            },
            "selection": {
                "actualSelectedSnapshots": len(actual_set),
                "controlSelectedSnapshots": len(control_set),
                "bothSelectedSnapshots": len(actual_set & control_set),
                "actualOnlySelectedSnapshots": len(actual_set - control_set),
                "v25OnlySelectedSnapshots": len(actual_set - control_set),
                "controlOnlySelectedSnapshots": len(control_set - actual_set),
                "unionSelectedSnapshots": len(union),
                "selectionJaccard": _rate(len(actual_set & control_set), len(union)),
                "matureDecisionCohorts": len(decision_ids),
                "changedDecisionCohorts": len(changed_decisions),
                "changedDecisionCohortRate": _rate(
                    len(changed_decisions), len(complete_control_decision_ids)
                ),
            },
            "outcomes": {
                "actual": _selection_control_outcome_summary(
                    actual_selected,
                    horizon,
                    outcomes,
                ),
                "control": _selection_control_outcome_summary(
                    control_selected,
                    horizon,
                    outcomes,
                ),
                "actualV25": _selection_control_outcome_summary(
                    actual_selected,
                    horizon,
                    outcomes,
                ),
                "v24RuleControl": _selection_control_outcome_summary(
                    control_selected,
                    horizon,
                    outcomes,
                ),
                "pairedCohortComparison": temporal,
            },
        }

    @staticmethod
    def _completed_values(
        snapshots: Iterable[_Snapshot],
        horizon: int,
        outcomes: dict[tuple[str, int], _Outcome],
    ) -> list[tuple[_Snapshot, float]]:
        values: list[tuple[_Snapshot, float]] = []
        for snapshot in snapshots:
            outcome = outcomes[(snapshot.snapshot_id, horizon)]
            if outcome.forward_return is not None:
                values.append((snapshot, outcome.forward_return))
        return values

    @staticmethod
    def _temporal_stability(
        completed: list[tuple[_Snapshot, float]],
    ) -> dict[str, Any]:
        selected_by_cohort: dict[str, list[float]] = defaultdict(list)
        nonselected_by_cohort: dict[str, list[float]] = defaultdict(list)
        times: dict[str, datetime] = {}
        for snapshot, value in completed:
            if snapshot.selected:
                selected_by_cohort[snapshot.decision_id].append(value)
            else:
                nonselected_by_cohort[snapshot.decision_id].append(value)
            times[snapshot.decision_id] = snapshot.decision_time
        paired = {
            key: fmean(selected_by_cohort[key]) - fmean(nonselected_by_cohort[key])
            for key in selected_by_cohort.keys() & nonselected_by_cohort.keys()
        }
        ordered = sorted(paired, key=lambda key: (times[key], key))
        split = len(ordered) // 2
        first_keys = ordered[:split]
        second_keys = ordered[split:]
        first = [paired[key] for key in first_keys]
        second = [paired[key] for key in second_keys]
        first_mean = _mean_or_none(first)
        second_mean = _mean_or_none(second)
        both_positive = (
            first_mean > 0 and second_mean > 0
            if first_mean is not None and second_mean is not None
            else None
        )
        return {
            "firstHalfCohorts": len(first_keys),
            "secondHalfCohorts": len(second_keys),
            "firstHalfPairedCohortSpread": first_mean,
            "secondHalfPairedCohortSpread": second_mean,
            "bothPositive": both_positive,
        }

    def _derivatives_context_quality(
        self,
        decision_ids: set[str],
        contexts: tuple[_MarketContext, ...],
    ) -> dict[str, Any]:
        matched = tuple(item for item in contexts if item.decision_id in decision_ids)
        denominator = len(decision_ids)
        availability = Counter(str(item.payload.get("availability", "INVALID")) for item in matched)
        recommendations = Counter(
            str(item.payload.get("recommendation", "INVALID")) for item in matched
        )
        basis_sources = Counter(
            str(item.payload.get("basisInputSource", "UNKNOWN")) for item in matched
        )
        available_contexts = tuple(
            item for item in matched if item.payload.get("availability") == "AVAILABLE"
        )
        required_basis_source = self.thresholds.required_derivatives_basis_input_source
        basis_source_matches = sum(
            _normalized_basis_source(item.payload.get("basisInputSource")) == required_basis_source
            and _is_finite_json_number(item.payload.get("basisInputRate"))
            for item in available_contexts
        )
        expected_version = self.thresholds.required_derivatives_feature_version
        version_matches = sum(
            item.payload.get("featureVersion") == expected_version for item in matched
        )
        return {
            "expectedFeatureVersion": expected_version,
            "decisionCohorts": denominator,
            "contextRows": len(matched),
            "contextCoverage": len(matched) / denominator if denominator else None,
            "availableRate": availability["AVAILABLE"] / denominator if denominator else None,
            "featureVersionMatchRate": (version_matches / denominator if denominator else None),
            "availabilityCounts": dict(sorted(availability.items())),
            "recommendationCounts": dict(sorted(recommendations.items())),
            "basisInputSourceCounts": dict(sorted(basis_sources.items())),
            "requiredBasisInputSource": required_basis_source,
            "basisInputSourceMatchRateAmongAvailable": (
                _rate(basis_source_matches, len(available_contexts))
                if required_basis_source is not None
                else None
            ),
            "availableContextRows": len(available_contexts),
            "basisCoverage": self._basis_coverage(denominator, matched),
        }

    @staticmethod
    def _basis_coverage(
        decision_cohorts: int,
        contexts: tuple[_MarketContext, ...],
    ) -> dict[str, Any]:
        """Describe raw Basis availability separately from the signal input.

        The official Binance Basis endpoint is optional and can be unavailable
        while a complete Mark--Index proxy signal remains usable.  Keeping
        these denominators separate lets a review distinguish an upstream
        official-Basis outage from an actual V2.5 input-coverage gap.
        """

        context_count = len(contexts)
        official_available = sum(
            _is_finite_json_number(item.payload.get("basisRate")) for item in contexts
        )
        proxy_available = sum(
            _is_finite_json_number(item.payload.get("markIndexBasisRate")) for item in contexts
        )
        declared_sources = Counter(
            _normalized_basis_source(item.payload.get("basisInputSource")) for item in contexts
        )
        complete_source_counts: Counter[str] = Counter()
        input_rate_missing_contexts = 0
        for item in contexts:
            source = _normalized_basis_source(item.payload.get("basisInputSource"))
            has_rate = _is_finite_json_number(item.payload.get("basisInputRate"))
            if not has_rate:
                input_rate_missing_contexts += 1
            if source != "UNKNOWN" and has_rate:
                complete_source_counts[source] += 1
        complete_inputs = sum(complete_source_counts.values())
        return {
            "decisionCohorts": decision_cohorts,
            "contextRows": context_count,
            "officialBasis": {
                "availableContexts": official_available,
                "missingContexts": context_count - official_available,
                "availableRateAmongContexts": _rate(official_available, context_count),
                "missingRateAmongContexts": _rate(
                    context_count - official_available, context_count
                ),
            },
            "markIndexProxy": {
                "availableContexts": proxy_available,
                "missingContexts": context_count - proxy_available,
                "availableRateAmongContexts": _rate(proxy_available, context_count),
                "missingRateAmongContexts": _rate(context_count - proxy_available, context_count),
            },
            "signalInput": {
                "completeContexts": complete_inputs,
                "incompleteContexts": context_count - complete_inputs,
                "inputRateMissingContexts": input_rate_missing_contexts,
                "unknownSourceContexts": declared_sources["UNKNOWN"],
                "sourceCounts": dict(sorted(declared_sources.items())),
                "sourceRatesAmongContexts": {
                    source: _rate(count, context_count)
                    for source, count in sorted(declared_sources.items())
                },
                "completeSourceCounts": dict(sorted(complete_source_counts.items())),
                "completeSourceCoverage": {
                    source: _rate(count, decision_cohorts)
                    for source, count in sorted(complete_source_counts.items())
                },
                "coverage": _rate(complete_inputs, decision_cohorts),
                "missingDecisionCohorts": decision_cohorts - complete_inputs,
                "missingRate": _rate(decision_cohorts - complete_inputs, decision_cohorts),
            },
        }

    @staticmethod
    def _paper_metrics(
        starting_equity: float,
        decisions: tuple[_PaperDecision, ...],
        executions: tuple[_Execution, ...],
    ) -> dict[str, Any]:
        equity_values = [starting_equity, *(item.equity for item in decisions)]
        ending_equity = decisions[-1].equity if decisions else None
        net_pnl = ending_equity - starting_equity if ending_equity is not None else None
        net_return = (
            net_pnl / starting_equity if net_pnl is not None and starting_equity > 0 else None
        )
        sells = tuple(item for item in executions if item.side == "SELL")
        realized = [item.realized_pnl for item in sells]
        winners = [value for value in realized if value > 0]
        losers = [value for value in realized if value < 0]
        gross_profit = sum(winners)
        gross_loss = -sum(losers)
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        return {
            "startingEquity": starting_equity,
            "endingEquity": ending_equity,
            "netPnl": net_pnl,
            "netReturn": net_return,
            "totalFees": sum(item.fee for item in executions),
            "totalTurnover": sum(item.quantity * item.price for item in executions),
            "executionCount": len(executions),
            "completedTrades": len(sells),
            "winRate": len(winners) / len(realized) if realized else None,
            "grossProfit": gross_profit,
            "grossLoss": gross_loss,
            "profitFactor": profit_factor,
            "profitFactorUnbounded": bool(realized and gross_profit > 0 and gross_loss == 0),
            "realizedPnl": sum(realized),
            "maximumDrawdown": _maximum_drawdown(equity_values) if decisions else None,
        }

    def _evaluate_gates(
        self,
        calendar_days: int,
        coverage: float | None,
        horizon_quality: dict[str, dict[str, Any]],
        signal_quality: dict[str, dict[str, Any]],
        temporal_stability: dict[str, dict[str, Any]],
        paper: dict[str, Any],
        concentration: float | None,
        derivatives_quality: dict[str, Any],
        review_stage: ReviewStage,
    ) -> dict[str, dict[str, Any]]:
        values = self.thresholds
        gates: dict[str, dict[str, Any]] = {}
        gates["MINIMUM_CALENDAR_DAYS"] = _numeric_gate(
            "READINESS", calendar_days, ">=", values.minimum_calendar_days
        )
        gates["MINIMUM_MATURE_1H_COHORTS"] = _numeric_gate(
            "READINESS",
            horizon_quality["60"]["matureCohorts"],
            ">=",
            values.minimum_mature_1h_cohorts,
        )
        gates["MINIMUM_MATURE_4H_COHORTS"] = _numeric_gate(
            "READINESS",
            horizon_quality["240"]["matureCohorts"],
            ">=",
            values.minimum_mature_4h_cohorts,
        )
        gates["DECISION_COVERAGE"] = _numeric_gate(
            "QUALITY", coverage, ">=", values.minimum_decision_coverage
        )
        for horizon, suffix in ((60, "1H"), (240, "4H")):
            quality = horizon_quality[str(horizon)]
            signal = signal_quality[str(horizon)]
            gates[f"SCORED_OUTCOME_MISSING_RATE_{suffix}"] = _numeric_gate(
                "QUALITY",
                quality["scoredMissingRate"],
                "<=",
                values.maximum_outcome_missing_rate,
            )
            gates[f"SELECTED_OUTCOME_MISSING_RATE_{suffix}"] = _numeric_gate(
                "QUALITY",
                quality["selectedMissingRate"],
                "<=",
                values.maximum_outcome_missing_rate,
            )
            gates[f"SELECTED_GROSS_RETURN_{suffix}"] = _numeric_gate(
                "PERFORMANCE",
                signal["selectedMeanForwardReturn"],
                ">",
                values.minimum_selected_gross_return,
            )
            gates[f"SELECTED_SPREAD_{suffix}"] = _numeric_gate(
                "PERFORMANCE",
                signal["pairedCohortSpread"],
                ">",
                values.minimum_selected_spread,
            )
            gates[f"SCORE_IC_{suffix}"] = _numeric_gate(
                "PERFORMANCE", signal["scoreIc"], ">", values.minimum_score_ic
            )
            stability = temporal_stability[str(horizon)]["bothPositive"]
            gates[f"TEMPORAL_STABILITY_{suffix}"] = {
                "category": "STABILITY",
                "required": True,
                "actual": stability,
                "operator": "is",
                "threshold": True,
                "passed": stability if isinstance(stability, bool) else None,
            }
        gates["PAPER_NET_RETURN"] = _numeric_gate(
            "PERFORMANCE", paper["netReturn"], ">", values.minimum_paper_net_return
        )
        profit_factor = paper["profitFactor"]
        if paper["profitFactorUnbounded"]:
            profit_factor_passed: bool | None = True
        elif profit_factor is None:
            profit_factor_passed = None
        else:
            profit_factor_passed = profit_factor >= values.minimum_profit_factor
        gates["PROFIT_FACTOR"] = {
            "category": "PERFORMANCE",
            "required": True,
            "actual": profit_factor,
            "operator": ">=",
            "threshold": values.minimum_profit_factor,
            "passed": profit_factor_passed,
            "unbounded": paper["profitFactorUnbounded"],
        }
        gates["MAXIMUM_DRAWDOWN"] = _numeric_gate(
            "RISK", paper["maximumDrawdown"], "<=", values.maximum_drawdown
        )
        if review_stage == "SHADOW_TO_PAPER":
            for name in ("PAPER_NET_RETURN", "PROFIT_FACTOR", "MAXIMUM_DRAWDOWN"):
                gates[name]["required"] = False
            gates["DERIVATIVES_CONTEXT_COVERAGE"] = _numeric_gate(
                "QUALITY",
                derivatives_quality["contextCoverage"],
                ">=",
                values.minimum_derivatives_context_coverage,
            )
            gates["DERIVATIVES_AVAILABLE_RATE"] = _numeric_gate(
                "QUALITY",
                derivatives_quality["availableRate"],
                ">=",
                values.minimum_derivatives_available_rate,
            )
            gates["DERIVATIVES_FEATURE_VERSION_MATCH"] = _numeric_gate(
                "QUALITY",
                derivatives_quality["featureVersionMatchRate"],
                ">=",
                1.0,
            )
            if values.required_derivatives_basis_input_source is not None:
                gates["DERIVATIVES_BASIS_INPUT_SOURCE_MATCH"] = _numeric_gate(
                    "QUALITY",
                    derivatives_quality["basisInputSourceMatchRateAmongAvailable"],
                    ">=",
                    values.minimum_derivatives_basis_input_source_match_rate,
                )
            if values.selection_control_variant_id is not None:
                self._add_selection_control_gates(
                    gates,
                    signal_quality["240"]["sameInputRuleControl"],
                )
        gates["SELECTION_CONCENTRATION"] = _numeric_gate(
            "RISK", concentration, "<=", values.maximum_selection_concentration
        )
        return gates

    def _add_selection_control_gates(
        self,
        gates: dict[str, dict[str, Any]],
        control: dict[str, Any],
    ) -> None:
        """Register a version-bound, pre-registered same-input control check.

        These gates are opt-in through ``selection_control_variant_id``.  That
        preserves the V2.3/V2.4 review contract while preventing V2.5 from
        being declared Shadow-ready before the narrow four-hour hypothesis can
        be evaluated against its persisted, identical-input control.
        """

        values = self.thresholds
        availability = control.get("availability")
        if availability == "AVAILABLE":
            availability_passed: bool | None = True
        elif availability == "PENDING_OUTCOMES":
            availability_passed = None
        else:
            availability_passed = False
        prefix = {
            "v2.4-rule-control": "V25_RULE_CONTROL",
            "v2.5-rule-control": "V26_RULE_CONTROL",
            "v2.6-rule-control": "V27_RULE_CONTROL",
        }.get(values.selection_control_variant_id or "", "SELECTION_RULE_CONTROL")
        gates[f"{prefix}_AVAILABILITY"] = {
            "category": "QUALITY",
            "required": True,
            "actual": availability,
            "operator": "is",
            "threshold": "AVAILABLE",
            "passed": availability_passed,
        }

        coverage = _mapping(control.get("coverage"))
        selection = _mapping(control.get("selection"))
        outcomes = _mapping(control.get("outcomes"))
        actual = _mapping(outcomes.get("actual") or outcomes.get("actualV25"))
        rule_control = _mapping(outcomes.get("control") or outcomes.get("v24RuleControl"))
        paired = _mapping(outcomes.get("pairedCohortComparison"))
        actual_outcome_coverage = _outcome_completion(actual.get("missingRate"))
        control_outcome_coverage = _outcome_completion(rule_control.get("missingRate"))
        outcome_coverage_gap = _absolute_difference(
            actual_outcome_coverage,
            control_outcome_coverage,
        )
        gates[f"{prefix}_COVERAGE"] = _numeric_gate(
            "QUALITY",
            coverage.get("controlCoverage"),
            ">=",
            values.minimum_selection_control_variant_coverage,
        )
        gates[f"{prefix}_PAIRED_4H_COHORTS"] = _numeric_gate(
            "READINESS",
            paired.get("pairedCohorts"),
            ">=",
            values.minimum_selection_control_paired_4h_cohorts,
        )
        gates[f"{prefix}_CHANGED_COHORTS"] = _numeric_gate(
            "READINESS",
            selection.get("changedDecisionCohorts"),
            ">=",
            values.minimum_selection_control_changed_cohorts,
        )
        gates[f"{prefix}_ACTUAL_OUTCOME_COVERAGE"] = _numeric_gate(
            "QUALITY",
            actual_outcome_coverage,
            ">=",
            values.minimum_selection_control_outcome_coverage,
        )
        gates[f"{prefix}_CONTROL_OUTCOME_COVERAGE"] = _numeric_gate(
            "QUALITY",
            control_outcome_coverage,
            ">=",
            values.minimum_selection_control_outcome_coverage,
        )
        gates[f"{prefix}_OUTCOME_COVERAGE_GAP"] = _numeric_gate(
            "QUALITY",
            outcome_coverage_gap,
            "<=",
            values.maximum_selection_control_outcome_coverage_gap,
        )
        gates[f"{prefix}_ACTUAL_MINUS_CONTROL"] = _numeric_gate(
            "PERFORMANCE",
            paired.get("actualMinusControlMean"),
            ">=",
            values.minimum_selection_control_actual_minus_control,
        )
        temporal = paired.get("bothHalvesNonnegative")
        gates[f"{prefix}_TEMPORAL_STABILITY_4H"] = {
            "category": "STABILITY",
            "required": True,
            "actual": temporal,
            "operator": "is",
            "threshold": True,
            "passed": temporal if isinstance(temporal, bool) else None,
        }


def analyze_strategy(
    observation_database: str | Path,
    paper_database: str | Path,
    experiment_id: str,
    *,
    portfolio_id: str | None = None,
    thresholds: StrategyReviewThresholds | None = None,
    review_stage: ReviewStage = "PAPER",
) -> dict[str, Any]:
    """Convenience wrapper for callers that do not need a reusable analyzer."""

    return StrategyReviewAnalyzer(observation_database, paper_database, thresholds).analyze(
        experiment_id, portfolio_id=portfolio_id, review_stage=review_stage
    )


def _datetime(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("review timestamps must be timezone-aware")
    return parsed


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(value)


def _table_has_column(connection: sqlite3.Connection, table: str, column: str) -> bool:
    """Check an additive observation-schema field without initializing a repository."""

    if isinstance(connection, PostgresConnectionAdapter):
        row = connection.execute(
            """SELECT 1 FROM information_schema.columns
               WHERE table_schema=%s AND table_name=%s AND column_name=%s""",
            (connection.schema, table, column),
        ).fetchone()
        return row is not None
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return any(str(row["name"]) == column for row in rows)


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    """Check an optional additive table in a read-only legacy database."""

    if isinstance(connection, PostgresConnectionAdapter):
        row = connection.execute(
            """SELECT 1 FROM information_schema.tables
               WHERE table_schema=%s AND table_name=%s""",
            (connection.schema, table),
        ).fetchone()
        return row is not None
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _candidate_reasons(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(decoded, list):
        return ()
    return tuple(item for item in decoded if isinstance(item, str) and item)


def _optional_finite_float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (str, bytes, int, float)):
        raise TypeError("SQLite numeric value has an unsupported type")
    numeric = float(value)
    return numeric if math.isfinite(numeric) else None


def _is_complete(outcome: _Outcome | None) -> bool:
    return bool(
        outcome is not None and outcome.status == "COMPLETED" and outcome.forward_return is not None
    )


def _outcome_group_summary(
    snapshots: Iterable[_Snapshot],
    horizon: int,
    outcomes: dict[tuple[str, int], _Outcome],
) -> dict[str, Any]:
    """Summarize a named scored subset without treating missing outcomes as zero."""

    group = tuple(snapshots)
    completed: list[float] = []
    for snapshot in group:
        outcome = outcomes.get((snapshot.snapshot_id, horizon))
        if _is_complete(outcome) and outcome is not None and outcome.forward_return is not None:
            completed.append(outcome.forward_return)
    negative = [value for value in completed if value < 0]
    return {
        "matureScoredSnapshots": len(group),
        "completedScoredSnapshots": len(completed),
        "missingScoredSnapshots": len(group) - len(completed),
        "missingRate": _missing_rate(len(group), len(completed)),
        "meanForwardReturn": _mean_or_none(completed),
        "negativeObservations": len(negative),
        "negativeObservationRate": _rate(len(negative), len(completed)),
        "meanNegativeForwardReturn": _mean_or_none(negative),
    }


def _selection_control_outcome_summary(
    snapshots: Iterable[_Snapshot],
    horizon: int,
    outcomes: dict[tuple[str, int], _Outcome],
) -> dict[str, Any]:
    """Summarize a selection-rule arm with one equal-weight value per decision.

    A V2.5 decision can select more than one asset.  The same-input control is
    therefore evaluated at the decision/cohort level: first average its selected
    assets within each decision, then average those cohort means.  The pooled
    snapshot mean is retained only as a diagnostic, so a wide candidate cohort
    cannot dominate the headline rule-control comparison.
    """

    group = tuple(snapshots)
    values_by_decision: dict[str, list[float]] = defaultdict(list)
    completed_values: list[float] = []
    mature_decision_ids = {item.decision_id for item in group}
    for snapshot in group:
        outcome = outcomes.get((snapshot.snapshot_id, horizon))
        if _is_complete(outcome) and outcome is not None and outcome.forward_return is not None:
            values_by_decision[snapshot.decision_id].append(outcome.forward_return)
            completed_values.append(outcome.forward_return)
    cohort_means = [fmean(values_by_decision[key]) for key in sorted(values_by_decision)]
    negative_cohort_means = [value for value in cohort_means if value < 0]
    negative_values = [value for value in completed_values if value < 0]
    return {
        "aggregation": "EQUAL_WEIGHT_DECISION_COHORT_MEANS",
        "matureSelectedSnapshots": len(group),
        "completedSelectedSnapshots": len(completed_values),
        "missingSelectedSnapshots": len(group) - len(completed_values),
        "missingRate": _missing_rate(len(group), len(completed_values)),
        "matureDecisionCohorts": len(mature_decision_ids),
        "completedDecisionCohorts": len(cohort_means),
        "missingDecisionCohorts": len(mature_decision_ids) - len(cohort_means),
        "meanForwardReturn": _mean_or_none(cohort_means),
        "pooledSnapshotMeanForwardReturn": _mean_or_none(completed_values),
        "negativeDecisionCohorts": len(negative_cohort_means),
        "negativeDecisionCohortRate": _rate(len(negative_cohort_means), len(cohort_means)),
        "meanNegativeDecisionCohortForwardReturn": _mean_or_none(negative_cohort_means),
        "negativeObservations": len(negative_values),
        "negativeObservationRate": _rate(len(negative_values), len(completed_values)),
    }


def _completed_forward_returns(
    snapshots: Iterable[_Snapshot],
    horizon: int,
    outcomes: dict[tuple[str, int], _Outcome],
) -> list[float]:
    values: list[float] = []
    for snapshot in snapshots:
        outcome = outcomes.get((snapshot.snapshot_id, horizon))
        if _is_complete(outcome) and outcome is not None and outcome.forward_return is not None:
            values.append(outcome.forward_return)
    return values


def _chronological_halves(values: list[float]) -> tuple[list[float], list[float]]:
    split = len(values) // 2
    return values[:split], values[split:]


def _missing_rate(total: int, completed: int) -> float | None:
    return (total - completed) / total if total else None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _outcome_completion(missing_rate: object) -> float | None:
    if isinstance(missing_rate, bool) or not isinstance(missing_rate, (int, float)):
        return None
    numeric = float(missing_rate)
    return 1 - numeric if math.isfinite(numeric) and 0 <= numeric <= 1 else None


def _absolute_difference(left: float | None, right: float | None) -> float | None:
    return abs(left - right) if left is not None and right is not None else None


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _is_finite_json_number(value: object) -> bool:
    """Accept JSON numeric strings while rejecting booleans and non-finite values."""

    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return False
    try:
        return math.isfinite(float(value))
    except ValueError:
        return False


def _normalized_basis_source(value: object) -> str:
    if not isinstance(value, str):
        return "UNKNOWN"
    normalized = value.strip().upper()
    return normalized if normalized in {"OFFICIAL", "MARK_INDEX_PROXY"} else "UNKNOWN"


def _mean_or_none(values: Iterable[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    return fmean(finite) if finite else None


def _median_or_none(values: Iterable[float]) -> float | None:
    finite = [value for value in values if math.isfinite(value)]
    return median(finite) if finite else None


def _percentile_or_none(values: Iterable[float], quantile: float) -> float | None:
    finite = sorted(value for value in values if math.isfinite(value))
    if not finite:
        return None
    if len(finite) == 1:
        return finite[0]
    position = quantile * (len(finite) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return finite[lower]
    fraction = position - lower
    return finite[lower] + (finite[upper] - finite[lower]) * fraction


def _finite_max(values: Iterable[object]) -> float | None:
    finite = [float(value) for value in values if isinstance(value, (float, int))]
    return max(finite) if finite else None


def _same_nonzero_direction(left: float, right: float) -> bool:
    return (left > 0 and right > 0) or (left < 0 and right < 0)


def _average_ranks(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0.0] * len(values)
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average = (start + 1 + end) / 2
        for index in range(start, end):
            ranks[ordered[index][0]] = average
        start = end
    return ranks


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    return _pearson(_average_ranks(left), _average_ranks(right))


def _pearson(left: list[float], right: list[float]) -> float | None:
    left_mean = fmean(left)
    right_mean = fmean(right)
    left_centered = [value - left_mean for value in left]
    right_centered = [value - right_mean for value in right]
    denominator = math.sqrt(
        sum(value * value for value in left_centered)
        * sum(value * value for value in right_centered)
    )
    if denominator == 0:
        return None
    return (
        sum(
            left_value * right_value
            for left_value, right_value in zip(left_centered, right_centered, strict=True)
        )
        / denominator
    )


def _maximum_drawdown(equity: Iterable[float]) -> float:
    peak: float | None = None
    maximum = 0.0
    for value in equity:
        if not math.isfinite(value) or value < 0:
            continue
        peak = value if peak is None else max(peak, value)
        if peak > 0:
            maximum = max(maximum, 1 - value / peak)
    return maximum


def _numeric_gate(
    category: str,
    actual: object,
    operator: str,
    threshold: float | int,
) -> dict[str, Any]:
    if not isinstance(actual, (float, int)) or not math.isfinite(float(actual)):
        passed: bool | None = None
    elif operator == ">=":
        passed = actual >= threshold
    elif operator == ">":
        passed = actual > threshold
    elif operator == "<=":
        passed = actual <= threshold
    else:
        raise ValueError(f"unsupported gate operator: {operator}")
    return {
        "category": category,
        "required": True,
        "actual": actual,
        "operator": operator,
        "threshold": threshold,
        "passed": passed,
    }


def _camelized_thresholds(value: StrategyReviewThresholds) -> dict[str, Any]:
    return {
        _snake_to_camel(item.name): getattr(value, item.name)
        for item in fields(StrategyReviewThresholds)
    }


def _snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(item.capitalize() for item in tail)
