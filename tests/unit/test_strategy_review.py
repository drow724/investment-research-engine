import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from investment.crypto.research.strategy_review import StrategyReviewAnalyzer
from investment.crypto.research.strategy_review_policy import thresholds_for_strategy_version

ASSETS = ("A", "B", "C", "D", "E", "F", "G", "H")


def _create_databases(
    root: Path,
    *,
    cycles: int,
    positive_signal: bool = True,
    missing_selected_asset: str | None = None,
    rising_equity: bool = True,
    include_market_contexts: bool = True,
    context_availability: str = "AVAILABLE",
    context_feature_version: str = "btc-squeeze-v2-market-streams",
) -> tuple[Path, Path, str, str]:
    observation_path = root / "observation.sqlite3"
    paper_path = root / "paper.sqlite3"
    experiment_id = "paper-review-experiment"
    portfolio_id = "paper-review-portfolio"
    origin = datetime(2026, 1, 1, 23, tzinfo=UTC)
    final_decision_time = origin + timedelta(minutes=15 * (cycles - 1))
    final_completed_at = final_decision_time + timedelta(seconds=5)

    with sqlite3.connect(observation_path) as connection:
        connection.executescript(
            """
            CREATE TABLE observation_experiment (
                experiment_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL, config_hash TEXT NOT NULL,
                started_at TEXT NOT NULL, planned_end_at TEXT NOT NULL,
                status TEXT NOT NULL, starting_equity REAL NOT NULL,
                completed_at TEXT, interruption_reason TEXT
            );
            CREATE TABLE decision_snapshot (
                snapshot_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
                decision_id TEXT NOT NULL, decision_time TEXT NOT NULL,
                asset TEXT NOT NULL, score REAL, selected INTEGER NOT NULL
            );
            CREATE TABLE decision_outcome_minute (
                snapshot_id TEXT NOT NULL, horizon_minutes INTEGER NOT NULL,
                target_at TEXT NOT NULL, evaluated_at TEXT NOT NULL,
                status TEXT NOT NULL, forward_return REAL, mfe REAL, mae REAL,
                PRIMARY KEY(snapshot_id, horizon_minutes)
            );
            CREATE TABLE decision_market_context (
                experiment_id TEXT NOT NULL, decision_id TEXT NOT NULL,
                decision_time TEXT NOT NULL, context_json TEXT NOT NULL,
                PRIMARY KEY(experiment_id, decision_id)
            );
            """
        )
        connection.execute(
            "INSERT INTO observation_experiment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                experiment_id,
                portfolio_id,
                "dynamic-intraday-review",
                "config-hash",
                origin.isoformat(),
                (origin + timedelta(days=7)).isoformat(),
                "RUNNING",
                1_000_000.0,
                None,
                None,
            ),
        )
        snapshots = []
        outcomes = []
        contexts = []
        for cycle in range(cycles):
            decision_time = origin + timedelta(minutes=15 * cycle)
            decision_id = f"decision-{cycle:04d}"
            selected_asset = ASSETS[cycle % 4]
            if include_market_contexts:
                contexts.append(
                    (
                        experiment_id,
                        decision_id,
                        decision_time.isoformat(),
                        json.dumps(
                            {
                                "availability": context_availability,
                                "recommendation": "NO_CONFIRMATION",
                                "featureVersion": context_feature_version,
                            },
                            separators=(",", ":"),
                        ),
                    )
                )
            for asset_index, asset in enumerate(ASSETS):
                snapshot_id = f"snapshot-{cycle:04d}-{asset}"
                selected = asset == selected_asset
                snapshots.append(
                    (
                        snapshot_id,
                        experiment_id,
                        decision_id,
                        decision_time.isoformat(),
                        asset,
                        float(len(ASSETS) - asset_index),
                        int(selected),
                    )
                )
                for horizon in (60, 240):
                    settled_at = decision_time + timedelta(minutes=horizon + 30)
                    if settled_at > final_completed_at:
                        continue
                    if missing_selected_asset == asset:
                        continue
                    forward_return = _forward_return(asset_index, positive_signal, selected)
                    target_at = decision_time + timedelta(minutes=horizon)
                    outcomes.append(
                        (
                            snapshot_id,
                            horizon,
                            target_at.isoformat(),
                            target_at.isoformat(),
                            "COMPLETED",
                            forward_return,
                            forward_return + 0.002,
                            forward_return - 0.002,
                        )
                    )
        connection.executemany(
            "INSERT INTO decision_snapshot VALUES (?, ?, ?, ?, ?, ?, ?)", snapshots
        )
        connection.executemany(
            "INSERT INTO decision_outcome_minute VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            outcomes,
        )
        connection.executemany(
            "INSERT INTO decision_market_context VALUES (?, ?, ?, ?)",
            contexts,
        )

    with sqlite3.connect(paper_path) as connection:
        connection.executescript(
            """
            CREATE TABLE paper_rebalance_decision (
                decision_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL,
                strategy_version TEXT NOT NULL, as_of TEXT NOT NULL,
                equity TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE paper_execution (
                order_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL,
                pair TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL,
                price TEXT NOT NULL, fee TEXT NOT NULL, realized_pnl TEXT NOT NULL,
                executed_at TEXT NOT NULL
            );
            """
        )
        decisions = []
        for cycle in range(cycles):
            decision_time = origin + timedelta(minutes=15 * cycle)
            change = 50 * cycle * (1 if rising_equity else -1)
            decisions.append(
                (
                    f"decision-{cycle:04d}",
                    portfolio_id,
                    "dynamic-intraday-review",
                    decision_time.isoformat(),
                    str(1_000_000 + change),
                    "EXECUTED",
                    (decision_time + timedelta(seconds=5)).isoformat(),
                )
            )
        connection.executemany(
            "INSERT INTO paper_rebalance_decision VALUES (?, ?, ?, ?, ?, ?, ?)",
            decisions,
        )
        executions = []
        realized = (100.0,) * 7 + (-50.0,) * 3
        for index, pnl in enumerate(realized):
            executed_at = origin + timedelta(minutes=15 * index, seconds=2)
            executions.append(
                (
                    f"order-{index}",
                    portfolio_id,
                    "AKRW",
                    "SELL",
                    "1",
                    "10000",
                    "10",
                    str(pnl),
                    executed_at.isoformat(),
                )
            )
        connection.executemany(
            "INSERT INTO paper_execution VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            executions,
        )
        # This incomplete future cycle and execution must not leak past the common cutoff.
        future = final_decision_time + timedelta(minutes=15)
        connection.execute(
            "INSERT INTO paper_execution VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "future-order",
                portfolio_id,
                "BKRW",
                "SELL",
                "1",
                "10000",
                "9999",
                "-9999",
                future.isoformat(),
            ),
        )
    return observation_path, paper_path, experiment_id, portfolio_id


def _forward_return(asset_index: int, positive: bool, selected: bool) -> float:
    score = len(ASSETS) - asset_index
    score_component = (score - 4.5) * 0.0002
    if positive:
        return score_component + (0.006 if selected else 0.0)
    return -score_component - (0.006 if selected else 0.0)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ready_experiment_passes_all_gates_and_report_is_strict_json(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(tmp_path, cycles=220)

    analyzer = StrategyReviewAnalyzer(observation, paper)
    report = analyzer.analyze(experiment_id)
    repeated = analyzer.analyze(experiment_id)

    assert report["schemaVersion"] == 5
    assert report["decision"] == "PROMOTION_READY"
    assert report["analysisId"] == repeated["analysisId"]
    assert report["analysisCutoff"] == report["cutoffDecision"]["completedAt"]
    assert report["gatePolicyVersion"] == "strategy-promotion-gates-v3"
    assert report["reviewStage"] == "PAPER"
    assert "minimumCalendarDays" in report["thresholds"]
    assert "minimum_calendar_days" not in report["thresholds"]
    assert report["promotionEligible"] is True
    assert report["failedGates"] == []
    assert report["pendingGates"] == []
    assert report["dataQuality"]["calendarDays"] == 3
    assert report["dataQuality"]["decisionCoverage"] == 1.0
    assert report["dataQuality"]["horizons"]["60"]["matureCohorts"] == 214
    assert report["dataQuality"]["horizons"]["240"]["matureCohorts"] == 202
    assert report["signalQuality"]["60"]["pairedCohortSpread"] > 0.006
    assert report["signalQuality"]["240"]["scoreIc"] > 0.7
    alpha = report["signalQuality"]["economicAlphaBaseline"]
    assert alpha["primaryHorizonMinutes"] == 60
    assert alpha["estimatedRoundTripCost"] == 0.002
    assert alpha["horizons"]["60"]["selectedMeanNetReturn"] > 0
    assert alpha["horizons"]["60"]["pairedSelectedMinusNonselectedMean"] > 0
    variants = report["signalQuality"]["incrementalAlphaVariants"]
    assert variants["availability"] == "NOT_RECORDED"
    assert set(variants["variantCoverage"]) == {
        "A_MOMENTUM_ONLY",
        "B_RAW_DERIVATIVES",
        "C_CROWDING_ONLY",
        "D_PRODUCTION_GATES",
    }
    assert report["paperPerformance"]["netReturn"] == pytest.approx(0.01095)
    assert report["paperPerformance"]["totalFees"] == 100.0
    assert report["paperPerformance"]["winRate"] == 0.7
    assert report["paperPerformance"]["profitFactor"] == pytest.approx(14 / 3)
    assert report["paperPerformance"]["maximumDrawdown"] == 0.0
    assert report["selection"]["maximumAssetConcentration"] == 0.25
    assert report["paperPerformance"]["executionCount"] == 10
    assert all(gate["required"] is True for gate in report["gates"].values())
    json.dumps(report, allow_nan=False)


def test_insufficient_evidence_remains_collecting_even_with_failed_performance(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(
        tmp_path, cycles=12, positive_signal=False
    )

    report = StrategyReviewAnalyzer(observation, paper).analyze(experiment_id)

    assert report["decision"] == "COLLECTING"
    assert report["promotionEligible"] is False
    assert "MINIMUM_CALENDAR_DAYS" in report["failedGates"]
    assert "MINIMUM_MATURE_4H_COHORTS" in report["failedGates"]
    assert "SELECTED_GROSS_RETURN_1H" in report["failedGates"]
    assert "SELECTED_GROSS_RETURN_4H" in report["pendingGates"]


def test_mature_underperforming_experiment_is_rejected(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(
        tmp_path, cycles=220, positive_signal=False
    )

    report = StrategyReviewAnalyzer(observation, paper).analyze(experiment_id)

    assert report["decision"] == "REJECTED"
    assert report["promotionEligible"] is False
    assert "SELECTED_GROSS_RETURN_1H" in report["failedGates"]
    assert "SELECTED_SPREAD_4H" in report["failedGates"]
    assert "SCORE_IC_1H" in report["failedGates"]
    assert report["gates"]["TEMPORAL_STABILITY_1H"]["passed"] is False


def test_four_hour_loss_validation_exposes_selection_specific_loss_and_excursions(
    tmp_path,
) -> None:
    observation, paper, experiment_id, _ = _create_databases(
        tmp_path,
        cycles=220,
        positive_signal=False,
    )

    report = StrategyReviewAnalyzer(observation, paper).analyze(experiment_id)

    four_hour = report["signalQuality"]["240"]
    validation = four_hour["lossValidation"]
    assert validation["selectedMeanForwardReturn"] == four_hour["selectedMeanForwardReturn"]
    assert (
        validation["scoredNonselectedMeanForwardReturn"]
        == four_hour["scoredNonselectedMeanForwardReturn"]
    )
    assert validation["pairedCohortSpread"] == four_hour["pairedCohortSpread"]
    assert validation["selectedNegativeObservationRate"] == 1.0
    assert validation["selectedNegativeCohortRate"] == 1.0
    assert validation["selectedUnderperformanceRate"] == 1.0
    assert validation["selectedNegativeAndUnderperformedRate"] == 1.0
    assert validation["selectedMfeAvailabilityRate"] == 1.0
    assert validation["selectedMaeAvailabilityRate"] == 1.0
    assert validation["selectedMeanMfe"] == pytest.approx(
        validation["selectedMeanForwardReturn"] + 0.002
    )
    assert validation["selectedMeanMae"] == pytest.approx(
        validation["selectedMeanForwardReturn"] - 0.002
    )
    assert four_hour["fourHourGuardCounterfactual"]["candidateReasonsRecorded"] is False


def test_four_hour_guard_counterfactual_reports_guard_only_outcomes_descriptively(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(
        tmp_path,
        cycles=220,
        positive_signal=False,
    )
    guard = "NEW_ENTRY_BLOCKED_BY_4H_FALLING_KNIFE_GUARD"
    with sqlite3.connect(observation) as connection:
        connection.execute("ALTER TABLE decision_snapshot ADD COLUMN candidate_reasons_json TEXT")
        connection.execute(
            "UPDATE decision_snapshot SET candidate_reasons_json=? WHERE asset='H'",
            (json.dumps([guard]),),
        )
        connection.execute(
            "UPDATE decision_snapshot SET candidate_reasons_json=? WHERE asset='G'",
            (json.dumps([guard, "OTHER_REJECT_REASON"]),),
        )
        connection.execute(
            """UPDATE decision_outcome_minute
               SET forward_return=-0.01, mfe=-0.005, mae=-0.015
               WHERE horizon_minutes=240 AND snapshot_id LIKE '%-G'"""
        )
        connection.execute(
            """UPDATE decision_outcome_minute
               SET forward_return=-0.01, mfe=-0.005, mae=-0.015
               WHERE horizon_minutes=240 AND snapshot_id LIKE '%-H'"""
        )

    report = StrategyReviewAnalyzer(observation, paper).analyze(experiment_id)

    comparison = report["signalQuality"]["240"]["fourHourGuardCounterfactual"]
    assert comparison["guardReason"] == guard
    assert comparison["comparisonType"] == "DESCRIPTIVE_NOT_SAME_COHORT_AB"
    assert comparison["candidateReasonsRecorded"] is True
    assert comparison["selected"]["completedScoredSnapshots"] == 202
    assert comparison["guardCandidates"]["completedScoredSnapshots"] == 404
    assert comparison["guardCandidates"]["meanForwardReturn"] == pytest.approx(-0.01)
    assert comparison["guardCandidates"]["negativeObservationRate"] == 1.0
    assert comparison["guardOnlyCandidates"]["completedScoredSnapshots"] == 202
    assert comparison["guardOnlyCandidates"]["meanForwardReturn"] == pytest.approx(-0.01)
    assert comparison["guardOnlyCandidates"]["negativeObservationRate"] == 1.0


def test_same_input_v24_rule_control_compares_persisted_selection_variants(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(
        tmp_path,
        cycles=220,
        positive_signal=False,
    )
    with sqlite3.connect(observation) as connection:
        connection.execute(
            """CREATE TABLE decision_selection_variant (
                   snapshot_id TEXT NOT NULL,
                   variant_id TEXT NOT NULL,
                   selected INTEGER NOT NULL,
                   target_position REAL NOT NULL,
                   reason TEXT NOT NULL,
                   PRIMARY KEY(snapshot_id, variant_id)
               )"""
        )
        snapshots = connection.execute(
            "SELECT snapshot_id, asset FROM decision_snapshot ORDER BY snapshot_id"
        ).fetchall()
        connection.executemany(
            """INSERT INTO decision_selection_variant
               (snapshot_id, variant_id, selected, target_position, reason)
               VALUES (?, 'v2.4-rule-control', ?, ?, 'V2_4_RULE_CONTROL')""",
            [
                (str(row[0]), int(str(row[1]) == "H"), 0.25 if row[1] == "H" else 0.0)
                for row in snapshots
            ],
        )

    report = StrategyReviewAnalyzer(observation, paper).analyze(experiment_id)

    control = report["signalQuality"]["240"]["sameInputV24RuleControl"]
    assert control["variantId"] == "v2.4-rule-control"
    assert control["comparisonType"] == (
        "SAME_FROZEN_INPUT_RULE_CONTROL_NOT_HISTORICAL_REPLAY_OR_CAUSAL_PROOF"
    )
    assert control["availability"] == "AVAILABLE"
    coverage = control["coverage"]
    assert coverage["matureScoredSnapshots"] == 202 * len(ASSETS)
    assert coverage["controlRows"] == 202 * len(ASSETS)
    assert coverage["controlCoverage"] == 1.0
    assert coverage["missingControlRows"] == 0
    assert coverage["matureDecisionCohorts"] == 202
    assert coverage["completeControlDecisionCohorts"] == 202
    assert coverage["incompleteControlDecisionCohorts"] == 0
    assert control["selection"]["actualSelectedSnapshots"] == 202
    assert control["selection"]["controlSelectedSnapshots"] == 202
    assert control["selection"]["bothSelectedSnapshots"] == 0
    assert control["selection"]["changedDecisionCohorts"] == 202
    paired = control["outcomes"]["pairedCohortComparison"]
    assert paired["pairedCohorts"] == 202
    assert paired["actualMinusControlMean"] < 0
    assert paired["bothHalvesNonnegative"] is False
    assert control["outcomes"]["actualV25"] == {
        "aggregation": "EQUAL_WEIGHT_DECISION_COHORT_MEANS",
        "matureSelectedSnapshots": 202,
        "completedSelectedSnapshots": 202,
        "missingSelectedSnapshots": 0,
        "missingRate": 0.0,
        "matureDecisionCohorts": 202,
        "completedDecisionCohorts": 202,
        "missingDecisionCohorts": 0,
        "meanForwardReturn": pytest.approx(-0.0064, abs=1e-5),
        "pooledSnapshotMeanForwardReturn": pytest.approx(-0.0064, abs=1e-5),
        "negativeDecisionCohorts": 202,
        "negativeDecisionCohortRate": 1.0,
        "meanNegativeDecisionCohortForwardReturn": pytest.approx(-0.0064, abs=1e-5),
        "negativeObservations": 202,
        "negativeObservationRate": 1.0,
    }


def test_v25_shadow_gates_require_complete_control_and_proxy_provenance(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(
        tmp_path,
        cycles=220,
        context_feature_version="btc-squeeze-v3-mark-index-basis-proxy",
    )
    thresholds = thresholds_for_strategy_version(
        "dynamic-intraday-v2.5",
        "btc-squeeze-v3-mark-index-basis-proxy",
    )
    with sqlite3.connect(observation) as connection:
        context_rows = connection.execute(
            "SELECT decision_id FROM decision_market_context ORDER BY decision_time, decision_id"
        ).fetchall()
        connection.executemany(
            "UPDATE decision_market_context SET context_json=? WHERE decision_id=?",
            [
                (
                    json.dumps(
                        {
                            "availability": "AVAILABLE",
                            "recommendation": "NO_CONFIRMATION",
                            "featureVersion": "btc-squeeze-v3-mark-index-basis-proxy",
                            "markIndexBasisRate": "0.0002",
                            "basisInputRate": "0.0002",
                            "basisInputSource": "MARK_INDEX_PROXY",
                        },
                        separators=(",", ":"),
                    ),
                    row[0],
                )
                for row in context_rows
            ],
        )

    missing_control = StrategyReviewAnalyzer(observation, paper, thresholds).analyze(
        experiment_id,
        review_stage="SHADOW_TO_PAPER",
    )
    assert missing_control["decision"] == "REJECTED"
    assert missing_control["gates"]["DERIVATIVES_BASIS_INPUT_SOURCE_MATCH"]["passed"] is True
    assert missing_control["gates"]["V25_RULE_CONTROL_AVAILABILITY"]["passed"] is False
    assert "V25_RULE_CONTROL_AVAILABILITY" in missing_control["failedGates"]

    with sqlite3.connect(observation) as connection:
        connection.execute(
            """CREATE TABLE decision_selection_variant (
                   snapshot_id TEXT NOT NULL,
                   variant_id TEXT NOT NULL,
                   selected INTEGER NOT NULL,
                   target_position REAL NOT NULL,
                   reason TEXT NOT NULL,
                   PRIMARY KEY(snapshot_id, variant_id)
               )"""
        )
        snapshots = connection.execute(
            "SELECT snapshot_id, asset FROM decision_snapshot ORDER BY snapshot_id"
        ).fetchall()
        connection.executemany(
            """INSERT INTO decision_selection_variant
               (snapshot_id, variant_id, selected, target_position, reason)
               VALUES (?, 'v2.4-rule-control', ?, ?, 'V2_4_RULE_CONTROL')""",
            [
                (str(row[0]), int(str(row[1]) == "H"), 0.25 if row[1] == "H" else 0.0)
                for row in snapshots
            ],
        )

    passing = StrategyReviewAnalyzer(observation, paper, thresholds).analyze(
        experiment_id,
        review_stage="SHADOW_TO_PAPER",
    )
    assert passing["decision"] == "SHADOW_READY_FOR_PAPER"
    assert passing["gates"]["V25_RULE_CONTROL_COVERAGE"]["passed"] is True
    assert passing["gates"]["V25_RULE_CONTROL_PAIRED_4H_COHORTS"]["passed"] is True
    assert passing["gates"]["V25_RULE_CONTROL_CHANGED_COHORTS"]["passed"] is True
    assert passing["gates"]["V25_RULE_CONTROL_ACTUAL_MINUS_CONTROL"]["passed"] is True
    assert passing["gates"]["V25_RULE_CONTROL_TEMPORAL_STABILITY_4H"]["passed"] is True

    with sqlite3.connect(observation) as connection:
        first_decision = connection.execute(
            "SELECT decision_id FROM decision_market_context ORDER BY decision_time LIMIT 1"
        ).fetchone()
        assert first_decision is not None
        connection.execute(
            "UPDATE decision_market_context SET context_json=? WHERE decision_id=?",
            (
                json.dumps(
                    {
                        "availability": "AVAILABLE",
                        "recommendation": "NO_CONFIRMATION",
                        "featureVersion": "btc-squeeze-v3-mark-index-basis-proxy",
                        "markIndexBasisRate": "0.0002",
                        "basisInputRate": "0.0002",
                        "basisInputSource": "OFFICIAL",
                    },
                    separators=(",", ":"),
                ),
                first_decision[0],
            ),
        )

    source_mismatch = StrategyReviewAnalyzer(observation, paper, thresholds).analyze(
        experiment_id,
        review_stage="SHADOW_TO_PAPER",
    )
    assert source_mismatch["decision"] == "REJECTED"
    assert source_mismatch["gates"]["DERIVATIVES_BASIS_INPUT_SOURCE_MATCH"]["passed"] is False
    assert "DERIVATIVES_BASIS_INPUT_SOURCE_MATCH" in source_mismatch["failedGates"]


def test_same_input_v24_rule_control_is_safe_when_legacy_database_has_no_table(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(tmp_path, cycles=220)

    report = StrategyReviewAnalyzer(observation, paper).analyze(experiment_id)

    control = report["signalQuality"]["240"]["sameInputV24RuleControl"]
    assert control["availability"] == "NOT_RECORDED"
    assert control["coverage"]["matureScoredSnapshots"] == 202 * len(ASSETS)
    assert control["coverage"]["controlRows"] == 0
    assert control["coverage"]["controlCoverage"] == 0.0
    assert control["coverage"]["completeControlDecisionCohorts"] == 0
    assert control["outcomes"]["pairedCohortComparison"]["pairedCohorts"] == 0


def test_same_input_v24_rule_control_excludes_partial_control_cohorts(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(
        tmp_path,
        cycles=220,
        positive_signal=False,
    )
    with sqlite3.connect(observation) as connection:
        connection.execute(
            """CREATE TABLE decision_selection_variant (
                   snapshot_id TEXT NOT NULL,
                   variant_id TEXT NOT NULL,
                   selected INTEGER NOT NULL,
                   target_position REAL NOT NULL,
                   reason TEXT NOT NULL,
                   PRIMARY KEY(snapshot_id, variant_id)
               )"""
        )
        snapshots = connection.execute(
            """SELECT snapshot_id, asset FROM decision_snapshot
               WHERE snapshot_id != 'snapshot-0000-A' ORDER BY snapshot_id"""
        ).fetchall()
        connection.executemany(
            """INSERT INTO decision_selection_variant
               (snapshot_id, variant_id, selected, target_position, reason)
               VALUES (?, 'v2.4-rule-control', ?, ?, 'V2_4_RULE_CONTROL')""",
            [
                (str(row[0]), int(str(row[1]) == "H"), 0.25 if row[1] == "H" else 0.0)
                for row in snapshots
            ],
        )

    report = StrategyReviewAnalyzer(observation, paper).analyze(experiment_id)

    control = report["signalQuality"]["240"]["sameInputV24RuleControl"]
    assert control["availability"] == "INCOMPLETE_CONTROL"
    assert control["coverage"]["missingControlRows"] == 1
    assert control["coverage"]["completeControlDecisionCohorts"] == 201
    assert control["coverage"]["incompleteControlDecisionCohorts"] == 1
    assert control["selection"]["changedDecisionCohorts"] == 201
    assert control["outcomes"]["pairedCohortComparison"]["pairedCohorts"] == 201


def test_derivatives_basis_coverage_separates_official_missing_from_proxy_input(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(tmp_path, cycles=220)
    with sqlite3.connect(observation) as connection:
        rows = connection.execute(
            "SELECT decision_id FROM decision_market_context ORDER BY decision_time, decision_id"
        ).fetchall()
        for index, row in enumerate(rows):
            has_official_basis = index % 3 == 0
            has_signal_input = index % 3 != 2
            payload = {
                "availability": "AVAILABLE",
                "recommendation": "NO_CONFIRMATION",
                "featureVersion": "btc-squeeze-v2-market-streams",
                "basisRate": "0.0001" if has_official_basis else None,
                "markIndexBasisRate": "0.0002",
                "basisInputRate": "0.0002" if has_signal_input else None,
                "basisInputSource": "MARK_INDEX_PROXY" if has_signal_input else None,
            }
            connection.execute(
                "UPDATE decision_market_context SET context_json=? WHERE decision_id=?",
                (json.dumps(payload, separators=(",", ":")), row[0]),
            )

    report = StrategyReviewAnalyzer(observation, paper).analyze(
        experiment_id,
        review_stage="SHADOW_TO_PAPER",
    )

    coverage = report["dataQuality"]["derivativesContext"]["basisCoverage"]
    assert coverage["decisionCohorts"] == 220
    assert coverage["contextRows"] == 220
    assert coverage["officialBasis"]["availableContexts"] == 74
    assert coverage["officialBasis"]["missingContexts"] == 146
    assert coverage["officialBasis"]["missingRateAmongContexts"] == pytest.approx(146 / 220)
    assert coverage["markIndexProxy"]["availableRateAmongContexts"] == 1.0
    signal_input = coverage["signalInput"]
    assert signal_input["sourceCounts"] == {"MARK_INDEX_PROXY": 147, "UNKNOWN": 73}
    assert signal_input["completeSourceCounts"] == {"MARK_INDEX_PROXY": 147}
    assert signal_input["completeSourceCoverage"] == {"MARK_INDEX_PROXY": pytest.approx(147 / 220)}
    assert signal_input["coverage"] == pytest.approx(147 / 220)
    assert signal_input["missingDecisionCohorts"] == 73
    assert signal_input["missingRate"] == pytest.approx(73 / 220)


def test_shadow_review_requires_signal_gates_but_not_paper_performance(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(
        tmp_path,
        cycles=220,
        positive_signal=True,
        rising_equity=False,
    )
    with sqlite3.connect(paper) as connection:
        connection.execute("DELETE FROM paper_execution")

    report = StrategyReviewAnalyzer(observation, paper).analyze(
        experiment_id,
        review_stage="SHADOW_TO_PAPER",
    )

    assert report["decision"] == "SHADOW_READY_FOR_PAPER"
    assert report["promotionEligible"] is False
    assert report["paperActivationEligible"] is True
    assert report["gates"]["PAPER_NET_RETURN"]["passed"] is False
    assert report["gates"]["PAPER_NET_RETURN"]["required"] is False
    assert report["gates"]["PROFIT_FACTOR"]["required"] is False
    assert report["gates"]["TEMPORAL_STABILITY_1H"]["passed"] is True
    assert report["gates"]["DERIVATIVES_CONTEXT_COVERAGE"]["passed"] is True
    assert report["gates"]["DERIVATIVES_AVAILABLE_RATE"]["passed"] is True
    assert report["gates"]["DERIVATIVES_FEATURE_VERSION_MATCH"]["passed"] is True
    assert report["dataQuality"]["derivativesContext"]["contextCoverage"] == 1.0
    assert report["temporalStability"]["60"]["firstHalfPairedCohortSpread"] > 0
    assert report["temporalStability"]["60"]["secondHalfPairedCohortSpread"] > 0


def test_shadow_review_rejects_missing_derivatives_context_even_when_spot_gates_pass(
    tmp_path,
) -> None:
    observation, paper, experiment_id, _ = _create_databases(
        tmp_path,
        cycles=220,
        positive_signal=True,
        include_market_contexts=False,
    )

    report = StrategyReviewAnalyzer(observation, paper).analyze(
        experiment_id,
        review_stage="SHADOW_TO_PAPER",
    )

    assert report["decision"] == "REJECTED"
    assert "DERIVATIVES_CONTEXT_COVERAGE" in report["failedGates"]
    assert "DERIVATIVES_AVAILABLE_RATE" in report["failedGates"]


def test_missing_outcomes_are_counted_and_analysis_does_not_mutate_databases(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(
        tmp_path, cycles=220, missing_selected_asset="A"
    )
    before = (_digest(observation), _digest(paper))

    report = StrategyReviewAnalyzer(observation, paper).analyze(experiment_id)

    assert report["decision"] == "REJECTED"
    one_hour = report["dataQuality"]["horizons"]["60"]
    assert one_hour["scoredMissingRate"] == pytest.approx(1 / 8)
    assert one_hour["selectedMissingRate"] == pytest.approx(54 / 214)
    assert report["gates"]["SCORED_OUTCOME_MISSING_RATE_1H"]["passed"] is False
    assert report["gates"]["SELECTED_OUTCOME_MISSING_RATE_1H"]["passed"] is False
    assert (_digest(observation), _digest(paper)) == before


def test_missing_performance_metric_is_rejected_after_readiness_passes(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(tmp_path, cycles=220)
    with sqlite3.connect(paper) as connection:
        connection.execute("DELETE FROM paper_execution")

    report = StrategyReviewAnalyzer(observation, paper).analyze(experiment_id)

    assert report["dataQuality"]["horizons"]["240"]["matureCohorts"] >= 96
    assert report["gates"]["PROFIT_FACTOR"]["passed"] is None
    assert report["decision"] == "REJECTED"


def test_portfolio_override_must_match_experiment(tmp_path) -> None:
    observation, paper, experiment_id, _ = _create_databases(tmp_path, cycles=12)

    with pytest.raises(ValueError, match="does not match"):
        StrategyReviewAnalyzer(observation, paper).analyze(
            experiment_id, portfolio_id="different-portfolio"
        )
