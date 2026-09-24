import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from investment.crypto.observation.service import strategy_config_hash
from investment.crypto.strategy_registry import StrategyRegistry
from investment.crypto.strategy_review_workflow import (
    StrategyReviewError,
    StrategyReviewWorkflow,
)

CONFIG_ROOT = Path(__file__).parents[2] / "config" / "strategies"
NOW = datetime(2026, 8, 22, 1, 30, tzinfo=UTC)


@pytest.mark.parametrize("schema_version", (1, 2, 3, 4))
def test_publish_analysis_accepts_collecting_without_cutoff_and_is_idempotent(
    tmp_path: Path,
    schema_version: int,
) -> None:
    workflow, _, _, _ = _workflow_fixture(tmp_path)
    report = {
        "schemaVersion": schema_version,
        "analysisId": "analysis-still-collecting",
        "experiment": {
            "experimentId": "paper-v2.3-source",
            "portfolioId": "paper-v2.3-main",
            "strategyVersion": "dynamic-intraday-v2.3",
            "configHash": "stored-by-analyzer",
        },
        "analysisCutoff": None,
        "cutoffDecision": None,
        "gatePolicyVersion": "crypto-paper-promotion-v1",
        "decision": "COLLECTING",
        "gates": {
            "MINIMUM_MATURE_4H_COHORTS": {
                "threshold": 96,
                "actual": None,
                "passed": None,
                "required": True,
            }
        },
    }

    first = workflow.publish_analysis(report)
    assert workflow.publish_analysis(report) == first
    conflicting = {**report, "gatePolicyVersion": "other-gates"}
    with pytest.raises(StrategyReviewError, match="other content"):
        workflow.publish_analysis(conflicting)


def test_full_workflow_prepares_paper_next_restart_without_mutating_databases(
    tmp_path: Path,
) -> None:
    workflow, observation_db, paper_db, analysis_path = _workflow_fixture(tmp_path)

    proposal_path = workflow.propose_candidate(
        proposal_id="proposal-v2.4",
        analysis_path=analysis_path,
        parent_version="dynamic-intraday-v2.3",
        candidate_version="dynamic-intraday-v2.4-rc1",
        patch={"turnover_sell_weight": Decimal("0.75")},
        created_at=NOW,
    )
    proposal = _json(proposal_path)
    assert proposal["state"] == "PROPOSED"
    assert proposal["sourceExperimentId"] == "paper-v2.3-source"
    assert proposal["sourcePortfolioId"] == "paper-v2.3-main"
    assert proposal["patch"] == [
        {
            "declaredType": "decimal",
            "field": "turnover_sell_weight",
            "newValue": "0.75",
            "oldValue": "0.50",
        }
    ]

    validation_path = workflow.validate_candidate(
        validation_id="validation-v2.4",
        proposal_path=proposal_path,
        validated_at=NOW,
    )
    validation = _json(validation_path)
    assert validation["state"] == "VALIDATED"
    assert validation["candidateConfigHash"] == proposal["candidateConfigHash"]
    assert validation["analysisHash"] == proposal["analysisHash"]

    _insert_observation(
        observation_db,
        experiment_id="paper-v2.4-shadow",
        portfolio_id="paper-v2.4-shadow-portfolio",
        strategy_version="dynamic-intraday-v2.4-rc1",
        config_hash=proposal["candidateConfigHash"],
    )
    ready_analysis = _publish_analysis(
        workflow,
        analysis_id="analysis-v2.4-ready",
        decision="PROMOTION_READY",
        experiment_id="paper-v2.4-shadow",
        portfolio_id="paper-v2.4-shadow-portfolio",
        strategy_version="dynamic-intraday-v2.4-rc1",
        config_hash=proposal["candidateConfigHash"],
        gates={
            "net-return": {
                "category": "PERFORMANCE",
                "threshold": {"minimum": 0.0},
                "actual": 0.012,
                "operator": ">",
                "passed": True,
                "required": True,
            },
            "advisory-concentration": {
                "threshold": 0.25,
                "actual": 0.30,
                "passed": False,
                "required": False,
            },
        },
    )
    before_observation = _sha256(observation_db)
    before_paper = _sha256(paper_db)

    promotion_path = workflow.prepare_paper_promotion(
        promotion_id="promotion-v2.4",
        validation_path=validation_path,
        analysis_path=ready_analysis,
        new_portfolio_id="paper-v2.4-main",
        new_experiment_id="paper-v2.4-observation",
        initial_cash=Decimal("1000000"),
        paper_execute_requested=True,
        drain_experiment_ids=("paper-v2.3-source",),
        created_at=NOW,
    )

    promotion = _json(promotion_path)
    assert promotion["state"] == "PREPARED"
    assert promotion["target"] == "PAPER"
    assert promotion["activationMode"] == "NEXT_RESTART"
    assert promotion["parentVersion"] == "dynamic-intraday-v2.3"
    assert promotion["candidateVersion"] == "dynamic-intraday-v2.4-rc1"
    assert promotion["sourceExperimentId"] == "paper-v2.4-shadow"
    assert promotion["sourcePortfolioId"] == "paper-v2.4-shadow-portfolio"
    assert promotion["analysisId"] == "analysis-v2.4-ready"
    assert promotion["initialCash"] == "1000000"
    assert promotion["paperExecuteRequested"] is True
    assert promotion["drainExperimentIds"] == [
        "paper-v2.3-source",
        "paper-v2.4-shadow",
    ]
    assert {item["name"] for item in promotion["gateResults"]} == {
        "advisory-concentration",
        "net-return",
    }
    net_gate = next(item for item in promotion["gateResults"] if item["name"] == "net-return")
    assert net_gate["category"] == "PERFORMANCE"
    assert net_gate["operator"] == ">"
    assert _sha256(observation_db) == before_observation
    assert _sha256(paper_db) == before_paper

    # Generated timestamps are retained from the first successful attempt.
    assert (
        workflow.validate_candidate(
            validation_id="validation-v2.4",
            proposal_path=proposal_path,
            validated_at=NOW + timedelta(hours=1),
        )
        == validation_path
    )
    with sqlite3.connect(paper_db) as connection:
        connection.execute("INSERT INTO paper_portfolio VALUES (?)", ("paper-v2.4-main",))
    _insert_observation(
        observation_db,
        experiment_id="paper-v2.4-observation",
        portfolio_id="paper-v2.4-main",
        strategy_version="dynamic-intraday-v2.4-rc1",
        config_hash=proposal["candidateConfigHash"],
    )
    assert (
        workflow.prepare_paper_promotion(
            promotion_id="promotion-v2.4",
            validation_path=validation_path,
            analysis_path=ready_analysis,
            new_portfolio_id="paper-v2.4-main",
            new_experiment_id="paper-v2.4-observation",
            initial_cash=Decimal("1000000"),
            paper_execute_requested=True,
            drain_experiment_ids=("paper-v2.3-source",),
            created_at=NOW + timedelta(hours=1),
        )
        == promotion_path
    )


def test_v24_knobs_round_trip_through_typed_proposal_and_validation(tmp_path: Path) -> None:
    workflow, _, _, analysis_path = _workflow_fixture(tmp_path)
    patch: dict[str, object] = {
        "extreme_score_penalty_threshold": 0.80,
        "extreme_score_maximum_penalty": 0.25,
        "expected_return_calibration_mode": "SCORE_LINEAR",
        "expected_return_1h_intercept": -0.005,
        "expected_return_1h_score_slope": 0.010,
        "expected_return_4h_intercept": -0.008,
        "expected_return_4h_score_slope": 0.016,
        "minimum_fee_adjusted_entry_return": 0.0001,
        "minimum_expected_hold_return": 0.0001,
        "minimum_fee_adjusted_replacement_advantage": 0.001,
        "selection_concentration_lookback": timedelta(hours=24),
        "maximum_selection_concentration": 0.25,
        "selection_concentration_minimum_cohorts": 24,
        "rolling_asset_performance_lookback": timedelta(hours=72),
        "rolling_asset_minimum_sells": 3,
        "maximum_rolling_asset_realized_loss_fraction": Decimal("0.005"),
        "derivatives_overlay_mode": "SHADOW",
        "derivatives_feature_version": "btc-squeeze-v1-review",
        "derivatives_maximum_age": timedelta(minutes=9),
        "maximum_market_data_age": timedelta(minutes=20),
    }

    proposal_path = workflow.propose_candidate(
        proposal_id="proposal-v2.4-knobs",
        analysis_path=analysis_path,
        parent_version="dynamic-intraday-v2.3",
        candidate_version="dynamic-intraday-v2.4-typed",
        patch=patch,
        created_at=NOW,
    )
    proposal = _json(proposal_path)
    audit = {item["field"]: item for item in proposal["patch"]}
    assert set(audit) == set(patch)
    assert audit["extreme_score_penalty_threshold"]["declaredType"] == "optional[float]"
    assert audit["selection_concentration_lookback"]["declaredType"] == (
        "optional[durationSeconds]"
    )
    assert audit["maximum_rolling_asset_realized_loss_fraction"]["declaredType"] == (
        "optional[decimal]"
    )
    assert audit["derivatives_maximum_age"]["declaredType"] == "durationSeconds"
    assert audit["maximum_market_data_age"]["declaredType"] == "optional[durationSeconds]"

    candidate_path = Path(proposal["candidateConfigPath"])
    candidate = StrategyRegistry(candidate_path.parent).load("dynamic-intraday-v2.4-typed")
    for name, expected in patch.items():
        assert getattr(candidate, name) == expected

    validation_path = workflow.validate_candidate(
        validation_id="validation-v2.4-knobs",
        proposal_path=proposal_path,
        validated_at=NOW,
    )
    validation = _json(validation_path)
    assert validation["candidateConfigHash"] == proposal["candidateConfigHash"]
    assert validation["verifiedPatch"] == proposal["patch"]


@pytest.mark.parametrize(
    "patch",
    [
        {
            "extreme_score_penalty_threshold": 1.0,
            "extreme_score_maximum_penalty": 0.25,
        },
        {
            "expected_return_calibration_mode": "SCORE_LINEAR",
            "expected_return_1h_score_slope": -0.01,
            "expected_return_4h_score_slope": 0.016,
        },
        {
            "selection_concentration_lookback": timedelta(hours=24),
            "maximum_selection_concentration": 1.1,
        },
        {
            "rolling_asset_performance_lookback": timedelta(hours=72),
            "maximum_rolling_asset_realized_loss_fraction": Decimal("1.1"),
        },
        {"minimum_fee_adjusted_replacement_advantage": -0.001},
        {"derivatives_overlay_mode": "ACTIVE"},
        {"derivatives_maximum_age": timedelta(0)},
        {"maximum_market_data_age": timedelta(0)},
    ],
)
def test_v24_patch_semantics_fail_closed_before_candidate_publication(
    tmp_path: Path, patch: dict[str, object]
) -> None:
    workflow, _, _, analysis_path = _workflow_fixture(tmp_path)

    with pytest.raises(StrategyReviewError, match="invalid candidate policy"):
        workflow.propose_candidate(
            proposal_id="proposal-invalid-v2.4",
            analysis_path=analysis_path,
            parent_version="dynamic-intraday-v2.3",
            candidate_version="dynamic-intraday-v2.4-invalid",
            patch=patch,
            created_at=NOW,
        )

    assert not (tmp_path / "review" / "candidates").exists()


@pytest.mark.parametrize("decision", ["COLLECTING", "PROMOTION_READY"])
def test_proposal_requires_rejected_stored_analysis(tmp_path: Path, decision: str) -> None:
    workflow, _, _, analysis_path = _workflow_fixture(tmp_path)
    analysis = _json(analysis_path)
    analysis["decision"] = decision
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    with pytest.raises(StrategyReviewError, match="requires.*REJECTED"):
        workflow.propose_candidate(
            proposal_id="proposal-v2.4",
            analysis_path=analysis_path,
            parent_version="dynamic-intraday-v2.3",
            candidate_version="dynamic-intraday-v2.4-rc1",
            patch={"turnover_sell_weight": Decimal("0.75")},
        )

    assert not (tmp_path / "review" / "candidates").exists()


def test_proposal_rejects_mismatched_source_and_untyped_or_disallowed_patch(
    tmp_path: Path,
) -> None:
    workflow, _, _, analysis_path = _workflow_fixture(tmp_path)
    analysis = _json(analysis_path)
    analysis["experiment"]["portfolioId"] = "some-other-portfolio"
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
    with pytest.raises(StrategyReviewError, match="identity mismatch"):
        _propose(workflow, analysis_path)

    _write_parent_analysis(analysis_path)
    with pytest.raises(StrategyReviewError, match="declared Python type"):
        _propose(workflow, analysis_path, patch={"turnover_sell_weight": "0.75"})
    with pytest.raises(StrategyReviewError, match="not patchable"):
        _propose(workflow, analysis_path, patch={"exchange_fee_rate": Decimal("0.001")})
    with pytest.raises(StrategyReviewError, match="must differ"):
        _propose(workflow, analysis_path, patch={"turnover_sell_weight": Decimal("0.50")})


def test_candidate_version_and_artifacts_are_never_reused(tmp_path: Path) -> None:
    workflow, _, _, analysis_path = _workflow_fixture(tmp_path)
    first = _propose(workflow, analysis_path)
    assert first.is_file()
    assert _propose(workflow, analysis_path) == first
    assert (
        workflow.propose_candidate(
            proposal_id="proposal-v2.4",
            analysis_path=analysis_path,
            parent_version="dynamic-intraday-v2.3",
            candidate_version="dynamic-intraday-v2.4-rc1",
            patch={"turnover_sell_weight": Decimal("0.75")},
            created_at=NOW + timedelta(days=1),
        )
        == first
    )

    with pytest.raises(StrategyReviewError, match="already exists"):
        _propose(workflow, analysis_path, proposal_id="a-different-proposal")
    with pytest.raises(StrategyReviewError, match="already exists"):
        workflow.propose_candidate(
            proposal_id="proposal-v2.4",
            analysis_path=analysis_path,
            parent_version="dynamic-intraday-v2.3",
            candidate_version="dynamic-intraday-v2.5-rc1",
            patch={"turnover_sell_weight": Decimal("0.75")},
        )


@pytest.mark.parametrize(
    "patch",
    [
        {"maximum_positions": 4},
        {
            "maximum_positions": 3,
            "maximum_asset_weight": Decimal("0.40"),
            "invested_fraction": Decimal("0.91"),
        },
        {"maximum_positions": 3, "maximum_asset_weight": Decimal("0.41")},
        {"maximum_daily_turnover_fraction": Decimal("6.1")},
        {"bullish_daily_turnover_fraction": Decimal("8.1")},
        {"maximum_daily_fee_fraction": Decimal("0.006")},
        {"maximum_daily_realized_loss_fraction": Decimal("0.021")},
        {"turnover_sell_weight": Decimal("0.49")},
    ],
)
def test_proposal_enforces_conservative_automatic_paper_envelope(
    tmp_path: Path, patch: dict[str, object]
) -> None:
    workflow, _, _, analysis_path = _workflow_fixture(tmp_path)

    with pytest.raises(StrategyReviewError, match="Paper safety envelope"):
        _propose(workflow, analysis_path, patch=patch)


def test_validation_detects_candidate_or_analysis_tampering(tmp_path: Path) -> None:
    workflow, _, _, analysis_path = _workflow_fixture(tmp_path)
    proposal_path = _propose(workflow, analysis_path)
    proposal = _json(proposal_path)
    candidate_path = Path(proposal["candidateConfigPath"])
    candidate_path.write_text(
        candidate_path.read_text(encoding="utf-8").replace('"0.75"', '"0.70"'),
        encoding="utf-8",
    )
    with pytest.raises(StrategyReviewError, match="candidateConfigHash verification failed"):
        workflow.validate_candidate(validation_id="validation-v2.4", proposal_path=proposal_path)

    candidate_path.write_text(
        candidate_path.read_text(encoding="utf-8").replace('"0.70"', '"0.75"'),
        encoding="utf-8",
    )
    analysis = _json(analysis_path)
    analysis["analysisCutoff"] = "2026-08-23T00:00:00Z"
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")
    with pytest.raises(StrategyReviewError, match="analysis hash verification failed"):
        workflow.validate_candidate(validation_id="validation-v2.4", proposal_path=proposal_path)


def test_promotion_requires_ready_candidate_analysis_and_all_required_gates(
    tmp_path: Path,
) -> None:
    workflow, observation_db, _, analysis_path = _workflow_fixture(tmp_path)
    proposal_path = _propose(workflow, analysis_path)
    proposal = _json(proposal_path)
    validation_path = workflow.validate_candidate(
        validation_id="validation-v2.4", proposal_path=proposal_path
    )
    _insert_observation(
        observation_db,
        experiment_id="paper-v2.4-shadow",
        portfolio_id="paper-v2.4-shadow-portfolio",
        strategy_version="dynamic-intraday-v2.4-rc1",
        config_hash=proposal["candidateConfigHash"],
    )
    ready_analysis = _publish_analysis(
        workflow,
        analysis_id="analysis-v2.4-ready",
        decision="PROMOTION_READY",
        experiment_id="paper-v2.4-shadow",
        portfolio_id="paper-v2.4-shadow-portfolio",
        strategy_version="dynamic-intraday-v2.4-rc1",
        config_hash=proposal["candidateConfigHash"],
        gates={
            "profit-factor": {
                "threshold": 1.2,
                "actual": 0.9,
                "passed": False,
                "required": True,
            }
        },
    )

    with pytest.raises(StrategyReviewError, match="required promotion gates failed"):
        _promote(workflow, validation_path, ready_analysis)
    assert not (tmp_path / "review" / "promotions").exists()


@pytest.mark.parametrize("duplicate", ["portfolio", "experiment"])
def test_promotion_rejects_preexisting_target_ids_read_only(tmp_path: Path, duplicate: str) -> None:
    workflow, observation_db, paper_db, analysis_path = _workflow_fixture(tmp_path)
    proposal_path = _propose(workflow, analysis_path)
    proposal = _json(proposal_path)
    validation_path = workflow.validate_candidate(
        validation_id="validation-v2.4", proposal_path=proposal_path
    )
    _insert_observation(
        observation_db,
        experiment_id="paper-v2.4-shadow",
        portfolio_id="paper-v2.4-shadow-portfolio",
        strategy_version="dynamic-intraday-v2.4-rc1",
        config_hash=proposal["candidateConfigHash"],
    )
    ready_analysis = _publish_analysis(
        workflow,
        analysis_id="analysis-v2.4-ready",
        decision="PROMOTION_READY",
        experiment_id="paper-v2.4-shadow",
        portfolio_id="paper-v2.4-shadow-portfolio",
        strategy_version="dynamic-intraday-v2.4-rc1",
        config_hash=proposal["candidateConfigHash"],
        gates={
            "net-return": {
                "threshold": 0.0,
                "actual": 0.01,
                "passed": True,
                "required": True,
            }
        },
    )
    if duplicate == "portfolio":
        with sqlite3.connect(paper_db) as connection:
            connection.execute("INSERT INTO paper_portfolio VALUES (?)", ("paper-v2.4-main",))
    else:
        _insert_observation(
            observation_db,
            experiment_id="paper-v2.4-observation",
            portfolio_id="unused",
            strategy_version="unused",
            config_hash="unused",
        )
    before_observation = _sha256(observation_db)
    before_paper = _sha256(paper_db)

    with pytest.raises(StrategyReviewError, match="already exists"):
        _promote(workflow, validation_path, ready_analysis)
    assert _sha256(observation_db) == before_observation
    assert _sha256(paper_db) == before_paper


def _workflow_fixture(
    tmp_path: Path,
) -> tuple[StrategyReviewWorkflow, Path, Path, Path]:
    registry = tmp_path / "registry"
    registry.mkdir()
    parent_source = CONFIG_ROOT / "dynamic-intraday-v2.3.toml"
    (registry / parent_source.name).write_bytes(parent_source.read_bytes())
    observation_db = tmp_path / "observation.sqlite3"
    paper_db = tmp_path / "paper.sqlite3"
    with sqlite3.connect(observation_db) as connection:
        connection.execute(
            "CREATE TABLE observation_experiment ("
            "experiment_id TEXT PRIMARY KEY, portfolio_id TEXT NOT NULL, "
            "strategy_version TEXT NOT NULL, config_hash TEXT NOT NULL)"
        )
    with sqlite3.connect(paper_db) as connection:
        connection.execute("CREATE TABLE paper_portfolio (portfolio_id TEXT PRIMARY KEY)")
    parent = StrategyRegistry(registry).load("dynamic-intraday-v2.3")
    _insert_observation(
        observation_db,
        experiment_id="paper-v2.3-source",
        portfolio_id="paper-v2.3-main",
        strategy_version=parent.strategy_version,
        config_hash=strategy_config_hash(parent),
    )
    workflow = StrategyReviewWorkflow(
        registry_root=registry,
        review_root=tmp_path / "review",
        observation_database=observation_db,
        paper_database=paper_db,
    )
    analysis_path = _publish_analysis(
        workflow,
        analysis_id="analysis-v2.3-rejected",
        decision="REJECTED",
        experiment_id="paper-v2.3-source",
        portfolio_id="paper-v2.3-main",
        strategy_version="dynamic-intraday-v2.3",
        config_hash=strategy_config_hash(parent),
        gates={
            "selected-spread-1h": {
                "threshold": 0.002,
                "actual": -0.003,
                "passed": False,
                "required": True,
            }
        },
    )
    return workflow, observation_db, paper_db, analysis_path


def _write_parent_analysis(path: Path, *, config_hash: str | None = None) -> None:
    if config_hash is None:
        parent = StrategyRegistry(CONFIG_ROOT).load("dynamic-intraday-v2.3")
        config_hash = strategy_config_hash(parent)
    _write_analysis(
        path,
        analysis_id="analysis-v2.3-rejected",
        decision="REJECTED",
        experiment_id="paper-v2.3-source",
        portfolio_id="paper-v2.3-main",
        strategy_version="dynamic-intraday-v2.3",
        config_hash=config_hash,
        gates={
            "selected-spread-1h": {
                "threshold": 0.002,
                "actual": -0.003,
                "passed": False,
                "required": True,
            }
        },
    )


def _write_analysis(
    path: Path,
    *,
    analysis_id: str,
    decision: str,
    experiment_id: str,
    portfolio_id: str,
    strategy_version: str,
    config_hash: str,
    gates: dict[str, dict[str, object]],
) -> None:
    path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "analysisId": analysis_id,
                "experiment": {
                    "experimentId": experiment_id,
                    "portfolioId": portfolio_id,
                    "strategyVersion": strategy_version,
                    "configHash": config_hash,
                },
                "analysisCutoff": "2026-08-22T00:00:00Z",
                "cutoffDecision": {"decisionId": "decision-at-cutoff"},
                "gatePolicyVersion": "crypto-paper-promotion-v1",
                "decision": decision,
                "gates": gates,
                "metrics": {"ignoredByWorkflow": True},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _publish_analysis(
    workflow: StrategyReviewWorkflow,
    *,
    analysis_id: str,
    decision: str,
    experiment_id: str,
    portfolio_id: str,
    strategy_version: str,
    config_hash: str,
    gates: dict[str, dict[str, object]],
) -> Path:
    report = {
        "schemaVersion": 1,
        "analysisId": analysis_id,
        "experiment": {
            "experimentId": experiment_id,
            "portfolioId": portfolio_id,
            "strategyVersion": strategy_version,
            "configHash": config_hash,
        },
        "analysisCutoff": "2026-08-22T00:00:00Z",
        "cutoffDecision": {"decisionId": "decision-at-cutoff"},
        "gatePolicyVersion": "crypto-paper-promotion-v1",
        "decision": decision,
        "gates": gates,
        "metrics": {"ignoredByWorkflow": True},
    }
    return workflow.publish_analysis(report)


def _insert_observation(
    path: Path,
    *,
    experiment_id: str,
    portfolio_id: str,
    strategy_version: str,
    config_hash: str,
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO observation_experiment VALUES (?, ?, ?, ?)",
            (experiment_id, portfolio_id, strategy_version, config_hash),
        )


def _propose(
    workflow: StrategyReviewWorkflow,
    analysis_path: Path,
    *,
    proposal_id: str = "proposal-v2.4",
    patch: dict[str, object] | None = None,
) -> Path:
    return workflow.propose_candidate(
        proposal_id=proposal_id,
        analysis_path=analysis_path,
        parent_version="dynamic-intraday-v2.3",
        candidate_version="dynamic-intraday-v2.4-rc1",
        patch=patch or {"turnover_sell_weight": Decimal("0.75")},
        created_at=NOW,
    )


def _promote(workflow: StrategyReviewWorkflow, validation_path: Path, analysis_path: Path) -> Path:
    return workflow.prepare_paper_promotion(
        promotion_id="promotion-v2.4",
        validation_path=validation_path,
        analysis_path=analysis_path,
        new_portfolio_id="paper-v2.4-main",
        new_experiment_id="paper-v2.4-observation",
        initial_cash=Decimal("1000000"),
        paper_execute_requested=True,
        created_at=NOW,
    )


def _json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
