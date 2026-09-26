"""Immutable candidate, validation, and Paper-promotion review artifacts.

This module deliberately stops at a ``NEXT_RESTART`` manifest.  It never edits
runtime configuration, assigns an active strategy, or writes to either Paper
or observation SQLite databases.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast, get_args, get_type_hints

import psycopg

from investment.crypto.application.dynamic_paper_rebalance import DynamicUniversePolicy
from investment.crypto.observation.service import strategy_config_hash
from investment.crypto.strategy_registry import StrategyConfigError, StrategyRegistry, write_policy
from investment.database.postgres import postgres_connection

REVIEW_ARTIFACT_SCHEMA_VERSION = 1
SUPPORTED_ANALYSIS_SCHEMA_VERSIONS = frozenset({1, 2, 3, 4, 5})
DEFAULT_REVIEW_ROOT = Path("experiments/strategy-review")
MAXIMUM_ARTIFACT_BYTES = 2 * 1024 * 1024

# Cost assumptions describe the venue rather than a strategy hypothesis.  The
# strategy version cannot be patched because it is supplied as a separate,
# validated identity.  Every other listed field is an intentional review knob.
PATCHABLE_POLICY_FIELDS = frozenset(
    {
        "minimum_history_bars",
        "liquidity_lookback_bars",
        "maximum_candidates",
        "maximum_positions",
        "invested_fraction",
        "maximum_asset_weight",
        "minimum_rebalance_fraction",
        "entry_score_hurdle",
        "hold_score_hurdle",
        "exit_score_hurdle",
        "maximum_hold_rank",
        "required_entry_confirmations",
        "reentry_cooldown",
        "minimum_replacement_score_advantage",
        "maximum_daily_turnover_fraction",
        "turnover_sell_weight",
        "turnover_budget_mode",
        "bullish_daily_turnover_fraction",
        "bullish_btc_minimum_momentum_4h",
        "bullish_btc_minimum_momentum_24h",
        "bullish_market_breadth_minimum",
        "bullish_market_breadth_minimum_candidates",
        "maximum_daily_fee_fraction",
        "maximum_daily_realized_loss_fraction",
        "scoring_method",
        "score_scale",
        "momentum_1h_weight",
        "momentum_4h_weight",
        "momentum_24h_weight",
        "volatility_weight",
        "minimum_entry_momentum_1h",
        "maximum_entry_momentum_1h",
        "minimum_entry_momentum_4h",
        "maximum_entry_momentum_4h",
        "minimum_entry_momentum_24h",
        "maximum_entry_momentum_24h",
        "maximum_entry_volatility",
        "maximum_entry_volatility_quantile",
        "minimum_hold_momentum_1h",
        "minimum_hold_momentum_24h",
        "maximum_hold_volatility",
        "maximum_holding_period",
        "excluded_base_assets",
        "maximum_market_data_age",
        "extreme_score_penalty_threshold",
        "extreme_score_maximum_penalty",
        "expected_return_calibration_mode",
        "expected_return_1h_intercept",
        "expected_return_1h_score_slope",
        "expected_return_4h_intercept",
        "expected_return_4h_score_slope",
        "minimum_fee_adjusted_entry_return",
        "minimum_expected_hold_return",
        "minimum_fee_adjusted_replacement_advantage",
        "selection_concentration_lookback",
        "maximum_selection_concentration",
        "selection_concentration_minimum_cohorts",
        "rolling_asset_performance_lookback",
        "rolling_asset_minimum_sells",
        "maximum_rolling_asset_realized_loss_fraction",
        "derivatives_overlay_mode",
        "derivatives_feature_version",
        "derivatives_maximum_age",
        "derivatives_minimum_funding_rate",
        "derivatives_minimum_basis_input_rate",
        "derivatives_minimum_global_long_short_ratio",
        "crowding_overlay_mode",
        "crowding_feature_version",
        "crowding_maximum_age",
        "crowding_maximum_long_score",
        "crowding_maximum_bearish_unwind_score",
    }
)

_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")


class StrategyReviewError(ValueError):
    """A review request or one of its immutable inputs failed verification."""


class StrategyReviewWorkflow:
    """Prepare review artifacts without changing active runtime state.

    The public methods form a deliberately narrow API which a CLI can wrap:
    ``propose_candidate`` -> ``validate_candidate`` -> ``prepare_paper_promotion``.
    """

    def __init__(
        self,
        *,
        registry_root: str | Path,
        review_root: str | Path = DEFAULT_REVIEW_ROOT,
        observation_database: str | Path,
        paper_database: str | Path,
        database_url: str | None = None,
        database_schema: str = "investment",
    ) -> None:
        self.registry = StrategyRegistry(registry_root)
        self.registry_root = Path(registry_root).resolve()
        self.review_root = Path(review_root).resolve()
        self.observation_database = Path(observation_database).resolve()
        self.paper_database = Path(paper_database).resolve()
        self.database_url = database_url
        self.database_schema = database_schema
        self.analyses_root = self.review_root / "analyses"
        self.candidates_root = self.review_root / "candidates"
        self.proposals_root = self.review_root / "proposals"
        self.validations_root = self.review_root / "validations"
        self.promotions_root = self.review_root / "promotions"

    def publish_analysis(self, report: Mapping[str, object]) -> Path:
        """Validate and atomically retain one canonical analyzer report."""

        copied = _json_copy(report)
        if not isinstance(copied, dict):
            raise StrategyReviewError("analysis report must be a JSON object")
        _validate_analysis_document(copied)
        analysis_id = _required_string(copied, "analysisId")
        _require_id(analysis_id, "analysisId")
        return _publish_json(self.analyses_root / f"{analysis_id}.json", copied)

    def propose_candidate(
        self,
        *,
        proposal_id: str,
        analysis_path: str | Path,
        parent_version: str,
        candidate_version: str,
        patch: Mapping[str, object],
        created_at: datetime | None = None,
    ) -> Path:
        """Create a candidate from a stored ``REJECTED`` parent analysis."""

        _require_id(proposal_id, "proposal_id")
        _require_id(candidate_version, "candidate_version")
        analysis_source, analysis, analysis_hash = _load_analysis(analysis_path, self.analyses_root)
        if analysis["decision"] != "REJECTED":
            raise StrategyReviewError(
                "candidate proposal requires an analysis decision of REJECTED"
            )

        parent = self.registry.load(parent_version)
        parent_path = self._registry_path(parent_version)
        parent_artifact_hash = _sha256_file(parent_path)
        parent_config_hash = strategy_config_hash(parent)
        experiment = analysis["experiment"]
        if experiment["strategyVersion"] != parent_version:
            raise StrategyReviewError("analysis strategy version does not match parent")
        if experiment["configHash"] != parent_config_hash:
            raise StrategyReviewError("analysis config hash does not match parent")
        self._verify_observation_experiment(experiment)

        if candidate_version in self.registry.versions():
            raise StrategyReviewError(
                f"candidate strategy version is already published: {candidate_version}"
            )
        candidate_path = self.candidates_root / f"{candidate_version}.toml"
        proposal_path = self.proposals_root / f"{proposal_id}.json"
        candidate_existed = candidate_path.exists()
        if candidate_existed and not proposal_path.exists():
            raise StrategyReviewError(
                f"candidate strategy snapshot already exists: {candidate_path}"
            )

        typed_patch = _validate_patch(parent, patch)
        try:
            candidate = replace(
                parent,
                **cast(Any, {"strategy_version": candidate_version, **typed_patch}),
            )
        except (StrategyConfigError, TypeError, ValueError) as exc:
            raise StrategyReviewError(f"invalid candidate policy: {exc}") from exc

        changed_fields = _changed_policy_fields(parent, candidate)
        if not changed_fields:
            raise StrategyReviewError("candidate policy must differ from its parent")
        if changed_fields != set(typed_patch):
            raise StrategyReviewError("patch contains a field whose value did not change")
        _require_paper_safety(candidate)
        try:
            # Serialization performs the same complete semantic validation as
            # a later registry load before anything is published.
            write_policy(candidate_path, candidate)
        except (StrategyConfigError, TypeError, ValueError) as exc:
            raise StrategyReviewError(f"invalid candidate policy: {exc}") from exc

        candidate_config_hash = strategy_config_hash(candidate)
        candidate_artifact_hash = _sha256_file(candidate_path)
        artifact = {
            "schemaVersion": REVIEW_ARTIFACT_SCHEMA_VERSION,
            "proposalId": proposal_id,
            "state": "PROPOSED",
            "candidateVersion": candidate_version,
            "candidateConfigPath": str(candidate_path),
            "candidateConfigHash": candidate_config_hash,
            "candidateArtifactHash": candidate_artifact_hash,
            "parentVersion": parent_version,
            "parentConfigPath": str(parent_path),
            "parentConfigHash": parent_config_hash,
            "parentArtifactHash": parent_artifact_hash,
            "sourceExperimentId": experiment["experimentId"],
            "sourceExperimentConfigHash": experiment["configHash"],
            "sourcePortfolioId": experiment["portfolioId"],
            "analysisId": analysis["analysisId"],
            "analysisPath": str(analysis_source),
            "analysisHash": analysis_hash,
            "analysisCutoff": analysis["analysisCutoff"],
            "gatePolicyVersion": analysis["gatePolicyVersion"],
            "patch": _patch_audit(parent, candidate, changed_fields),
            "createdAt": _utc_timestamp(created_at),
        }
        try:
            return _publish_json(proposal_path, artifact, stable_fields=("createdAt",))
        except Exception:
            # The proposal and candidate are one logical publication.  A
            # candidate without its provenance is not useful and has not yet
            # been exposed through a returned success result.
            if not candidate_existed:
                candidate_path.unlink(missing_ok=True)
            raise

    def validate_candidate(
        self,
        *,
        validation_id: str,
        proposal_path: str | Path,
        validated_at: datetime | None = None,
    ) -> Path:
        """Reproduce and hash-check a proposed candidate, then freeze validation."""

        _require_id(validation_id, "validation_id")
        source = _require_artifact_path(proposal_path, self.proposals_root, "proposal")
        proposal = _load_json(source)
        verified = self._verify_proposal(source, proposal)
        validation_path = self.validations_root / f"{validation_id}.json"

        artifact = {
            "schemaVersion": REVIEW_ARTIFACT_SCHEMA_VERSION,
            "validationId": validation_id,
            "state": "VALIDATED",
            "proposalId": proposal["proposalId"],
            "proposalPath": str(source),
            "proposalHash": _sha256_file(source),
            "candidateVersion": proposal["candidateVersion"],
            "candidateConfigPath": proposal["candidateConfigPath"],
            "candidateConfigHash": proposal["candidateConfigHash"],
            "candidateArtifactHash": proposal["candidateArtifactHash"],
            "parentVersion": proposal["parentVersion"],
            "parentConfigPath": proposal["parentConfigPath"],
            "parentConfigHash": proposal["parentConfigHash"],
            "parentArtifactHash": proposal["parentArtifactHash"],
            "sourceExperimentId": proposal["sourceExperimentId"],
            "sourceExperimentConfigHash": proposal["sourceExperimentConfigHash"],
            "sourcePortfolioId": proposal["sourcePortfolioId"],
            "analysisId": proposal["analysisId"],
            "analysisPath": proposal["analysisPath"],
            "analysisHash": proposal["analysisHash"],
            "analysisCutoff": proposal["analysisCutoff"],
            "gatePolicyVersion": proposal["gatePolicyVersion"],
            "verifiedPatch": proposal["patch"],
            "verifiedHashes": verified,
            "validatedAt": _utc_timestamp(validated_at),
        }
        return _publish_json(validation_path, artifact, stable_fields=("validatedAt",))

    def prepare_paper_promotion(
        self,
        *,
        promotion_id: str,
        validation_path: str | Path,
        analysis_path: str | Path,
        new_portfolio_id: str,
        new_experiment_id: str,
        initial_cash: Decimal,
        paper_execute_requested: bool,
        drain_experiment_ids: Sequence[str] = (),
        created_at: datetime | None = None,
    ) -> Path:
        """Freeze a Paper-only activation request for a subsequent restart."""

        _require_id(promotion_id, "promotion_id")
        _require_id(new_portfolio_id, "new_portfolio_id")
        _require_id(new_experiment_id, "new_experiment_id")
        if type(paper_execute_requested) is not bool:
            raise StrategyReviewError("paper_execute_requested must be a boolean")
        if not isinstance(initial_cash, Decimal) or not initial_cash.is_finite():
            raise StrategyReviewError("initial_cash must be a finite Decimal")
        if initial_cash <= 0:
            raise StrategyReviewError("initial_cash must be greater than zero")

        validation_source = _require_artifact_path(
            validation_path, self.validations_root, "validation"
        )
        validation = _load_json(validation_source)
        self._verify_validation(validation_source, validation)

        analysis_source, analysis, analysis_hash = _load_analysis(analysis_path, self.analyses_root)
        if analysis["decision"] not in {"PROMOTION_READY", "SHADOW_READY_FOR_PAPER"}:
            raise StrategyReviewError(
                "Paper promotion requires PROMOTION_READY or SHADOW_READY_FOR_PAPER"
            )
        gate_results = _normalized_gates(analysis["gates"])
        required_gates = [gate for gate in gate_results if gate["required"]]
        if not required_gates:
            raise StrategyReviewError("promotion analysis must contain required gates")
        failed = [gate["name"] for gate in required_gates if not gate["passed"]]
        if failed:
            raise StrategyReviewError(
                "required promotion gates failed: " + ", ".join(sorted(failed))
            )

        candidate_path = Path(_required_string(validation, "candidateConfigPath")).resolve()
        candidate = self._load_candidate(candidate_path)
        candidate_version = _required_string(validation, "candidateVersion")
        candidate_hash = strategy_config_hash(candidate)
        if candidate.strategy_version != candidate_version:
            raise StrategyReviewError("candidate strategy version changed after validation")
        if candidate_hash != _required_string(validation, "candidateConfigHash"):
            raise StrategyReviewError("candidate config hash changed after validation")
        experiment = analysis["experiment"]
        if experiment["strategyVersion"] != candidate_version:
            raise StrategyReviewError("promotion analysis strategy does not match candidate")
        if experiment["configHash"] != candidate_hash:
            raise StrategyReviewError("promotion analysis config hash does not match candidate")
        self._verify_observation_experiment(experiment)

        drain_ids: list[str] = []
        for value in (*drain_experiment_ids, experiment["experimentId"]):
            _require_id(value, "drain_experiment_id")
            if value not in drain_ids:
                drain_ids.append(value)
        if new_experiment_id in drain_ids:
            raise StrategyReviewError("new experiment cannot also be a drain experiment")

        promotion_path = self.promotions_root / f"{promotion_id}.json"
        artifact = {
            "schemaVersion": REVIEW_ARTIFACT_SCHEMA_VERSION,
            "promotionId": promotion_id,
            "state": "PREPARED",
            "target": "PAPER",
            "activationMode": "NEXT_RESTART",
            "candidateVersion": candidate_version,
            "candidateConfigPath": str(candidate_path),
            "candidateConfigHash": candidate_hash,
            "parentVersion": _required_string(validation, "parentVersion"),
            "parentConfigHash": _required_string(validation, "parentConfigHash"),
            "sourceExperimentId": experiment["experimentId"],
            "sourceExperimentConfigHash": experiment["configHash"],
            "sourcePortfolioId": experiment["portfolioId"],
            "analysisId": analysis["analysisId"],
            "analysisPath": str(analysis_source),
            "analysisHash": analysis_hash,
            "analysisCutoff": analysis["analysisCutoff"],
            "gatePolicyVersion": analysis["gatePolicyVersion"],
            "gateResults": gate_results,
            "validatedAt": _required_string(validation, "validatedAt"),
            "createdAt": _utc_timestamp(created_at),
            "newPortfolioId": new_portfolio_id,
            "newExperimentId": new_experiment_id,
            "initialCash": str(initial_cash),
            "paperExecuteRequested": paper_execute_requested,
            "drainExperimentIds": drain_ids,
            "validationId": _required_string(validation, "validationId"),
            "validationPath": str(validation_source),
            "validationHash": _sha256_file(validation_source),
        }
        if promotion_path.exists():
            # A retry remains successful even if a separate, later activation
            # has already created the requested IDs in SQLite.
            return _publish_json(promotion_path, artifact, stable_fields=("createdAt",))
        for experiment_id in drain_ids:
            if (
                self._read_one(
                    self.observation_database,
                    "SELECT 1 FROM observation_experiment WHERE experiment_id = ?",
                    (experiment_id,),
                )
                is None
            ):
                raise StrategyReviewError(
                    f"drain observation experiment does not exist: {experiment_id}"
                )
        self._require_absent_id(
            self.paper_database,
            table="paper_portfolio",
            column="portfolio_id",
            value=new_portfolio_id,
            label="new Paper portfolio",
        )
        self._require_absent_id(
            self.observation_database,
            table="observation_experiment",
            column="experiment_id",
            value=new_experiment_id,
            label="new observation experiment",
        )
        return _publish_json(promotion_path, artifact, stable_fields=("createdAt",))

    def _verify_proposal(self, source: Path, proposal: dict[str, Any]) -> dict[str, str]:
        _require_schema(proposal, "proposal")
        if proposal.get("state") != "PROPOSED":
            raise StrategyReviewError("proposal state must be PROPOSED")
        _require_id(_required_string(proposal, "proposalId"), "proposalId")
        expected_source = self.proposals_root / f"{proposal['proposalId']}.json"
        if source != expected_source:
            raise StrategyReviewError("proposal filename does not match proposalId")

        parent_path = Path(_required_string(proposal, "parentConfigPath")).resolve()
        if parent_path.parent != self.registry_root:
            raise StrategyReviewError("proposal parent path is outside the strategy registry")
        parent = self.registry.load(_required_string(proposal, "parentVersion"))
        candidate_path = Path(_required_string(proposal, "candidateConfigPath")).resolve()
        candidate = self._load_candidate(candidate_path)
        if candidate.strategy_version != _required_string(proposal, "candidateVersion"):
            raise StrategyReviewError("candidate strategy version does not match proposal")
        if candidate.strategy_version in self.registry.versions():
            raise StrategyReviewError("candidate strategy version is already published")

        hashes = {
            "proposalHash": _sha256_file(source),
            "parentConfigHash": strategy_config_hash(parent),
            "parentArtifactHash": _sha256_file(parent_path),
            "candidateConfigHash": strategy_config_hash(candidate),
            "candidateArtifactHash": _sha256_file(candidate_path),
        }
        for name in (
            "parentConfigHash",
            "parentArtifactHash",
            "candidateConfigHash",
            "candidateArtifactHash",
        ):
            if hashes[name] != _required_string(proposal, name):
                raise StrategyReviewError(f"proposal {name} verification failed")

        changed = _changed_policy_fields(parent, candidate)
        if not changed:
            raise StrategyReviewError("candidate policy no longer differs from parent")
        disallowed = changed - PATCHABLE_POLICY_FIELDS
        if disallowed:
            raise StrategyReviewError(
                "candidate contains non-patchable changes: " + ", ".join(sorted(disallowed))
            )
        _require_paper_safety(candidate)
        expected_patch = _patch_audit(parent, candidate, changed)
        if proposal.get("patch") != expected_patch:
            raise StrategyReviewError("candidate patch provenance verification failed")

        analysis_path = Path(_required_string(proposal, "analysisPath")).resolve()
        analysis_source, analysis, analysis_hash = _load_analysis(analysis_path, self.analyses_root)
        if analysis_source != analysis_path:
            raise StrategyReviewError("proposal analysis path is not canonical")
        if analysis_hash != _required_string(proposal, "analysisHash"):
            raise StrategyReviewError("proposal analysis hash verification failed")
        if analysis["decision"] != "REJECTED":
            raise StrategyReviewError("proposal source analysis is no longer REJECTED")
        experiment = analysis["experiment"]
        expected_identity = (
            _required_string(proposal, "sourceExperimentId"),
            _required_string(proposal, "sourcePortfolioId"),
            _required_string(proposal, "parentVersion"),
            _required_string(proposal, "sourceExperimentConfigHash"),
        )
        actual_identity = (
            experiment["experimentId"],
            experiment["portfolioId"],
            experiment["strategyVersion"],
            experiment["configHash"],
        )
        if actual_identity != expected_identity:
            raise StrategyReviewError("proposal source experiment provenance changed")
        if analysis["analysisId"] != _required_string(proposal, "analysisId"):
            raise StrategyReviewError("proposal analysisId verification failed")
        if analysis["analysisCutoff"] != _required_string(proposal, "analysisCutoff"):
            raise StrategyReviewError("proposal analysis cutoff verification failed")
        if analysis["gatePolicyVersion"] != _required_string(proposal, "gatePolicyVersion"):
            raise StrategyReviewError("proposal gate policy verification failed")
        self._verify_observation_experiment(experiment)
        _require_utc_iso(proposal, "createdAt")
        hashes["analysisHash"] = analysis_hash
        return hashes

    def _verify_validation(self, source: Path, validation: dict[str, Any]) -> None:
        _require_schema(validation, "validation")
        if validation.get("state") != "VALIDATED":
            raise StrategyReviewError("validation state must be VALIDATED")
        validation_id = _required_string(validation, "validationId")
        _require_id(validation_id, "validationId")
        if source != self.validations_root / f"{validation_id}.json":
            raise StrategyReviewError("validation filename does not match validationId")
        proposal_path = _require_artifact_path(
            _required_string(validation, "proposalPath"), self.proposals_root, "proposal"
        )
        if _sha256_file(proposal_path) != _required_string(validation, "proposalHash"):
            raise StrategyReviewError("validation proposal hash verification failed")
        proposal = _load_json(proposal_path)
        hashes = self._verify_proposal(proposal_path, proposal)
        for name in (
            "candidateConfigHash",
            "candidateArtifactHash",
            "parentConfigHash",
            "parentArtifactHash",
            "analysisHash",
        ):
            if _required_string(validation, name) != hashes[name]:
                raise StrategyReviewError(f"validation {name} verification failed")
        if validation.get("verifiedHashes") != hashes:
            raise StrategyReviewError("validation verifiedHashes provenance verification failed")
        for name in (
            "proposalId",
            "candidateVersion",
            "candidateConfigPath",
            "parentVersion",
            "parentConfigPath",
            "sourceExperimentId",
            "sourceExperimentConfigHash",
            "sourcePortfolioId",
            "analysisId",
            "analysisPath",
            "analysisCutoff",
            "gatePolicyVersion",
        ):
            if validation.get(name) != proposal.get(name):
                raise StrategyReviewError(f"validation {name} provenance verification failed")
        if validation.get("verifiedPatch") != proposal.get("patch"):
            raise StrategyReviewError("validation patch provenance verification failed")
        _require_utc_iso(validation, "validatedAt")

    def _registry_path(self, strategy_version: str) -> Path:
        path = (self.registry_root / f"{strategy_version}.toml").resolve()
        if path.parent != self.registry_root:
            raise StrategyReviewError("parent strategy path escaped registry root")
        return path

    def _load_candidate(self, path: Path) -> DynamicUniversePolicy:
        if path.parent != self.candidates_root:
            raise StrategyReviewError("candidate path is outside the candidate artifact root")
        try:
            return StrategyRegistry(self.candidates_root).load(path.stem)
        except StrategyConfigError as exc:
            raise StrategyReviewError(f"invalid candidate snapshot: {exc}") from exc

    def _verify_observation_experiment(self, experiment: Mapping[str, str]) -> None:
        row = self._read_one(
            self.observation_database,
            "SELECT experiment_id, portfolio_id, strategy_version, config_hash "
            "FROM observation_experiment WHERE experiment_id = ?",
            (experiment["experimentId"],),
        )
        if row is None:
            raise StrategyReviewError(
                f"source observation experiment does not exist: {experiment['experimentId']}"
            )
        actual = tuple(
            str(row[name])
            for name in ("experiment_id", "portfolio_id", "strategy_version", "config_hash")
        )
        expected = (
            experiment["experimentId"],
            experiment["portfolioId"],
            experiment["strategyVersion"],
            experiment["configHash"],
        )
        if actual != expected:
            raise StrategyReviewError("source observation experiment identity mismatch")

    def _require_absent_id(
        self,
        database: Path,
        *,
        table: str,
        column: str,
        value: str,
        label: str,
    ) -> None:
        # table/column are internal constants, never caller-controlled values.
        row = self._read_one(
            database,
            f"SELECT 1 FROM {table} WHERE {column} = ?",
            (value,),
        )
        if row is not None:
            raise StrategyReviewError(f"{label} id already exists: {value}")

    def _read_one(
        self,
        database: Path,
        statement: str,
        parameters: tuple[object, ...],
    ) -> Any:
        return _read_one(
            database,
            statement,
            parameters,
            database_url=self.database_url,
            database_schema=self.database_schema,
        )


def _load_analysis(path: str | Path, analyses_root: Path) -> tuple[Path, dict[str, Any], str]:
    source = _require_artifact_path(path, analyses_root, "analysis")
    analysis = _load_json(source)
    _validate_analysis_document(analysis)
    if source != analyses_root / f"{analysis['analysisId']}.json":
        raise StrategyReviewError("analysis filename does not match analysisId")
    return source, analysis, _sha256_file(source)


def _validate_analysis_document(analysis: dict[str, Any]) -> None:
    _require_analysis_schema(analysis)
    analysis_id = _required_string(analysis, "analysisId")
    _require_id(analysis_id, "analysisId")
    decision = _required_string(analysis, "decision")
    if decision not in {
        "COLLECTING",
        "REJECTED",
        "PROMOTION_READY",
        "SHADOW_READY_FOR_PAPER",
    }:
        raise StrategyReviewError(f"unsupported analysis decision: {decision}")
    cutoff = analysis.get("analysisCutoff")
    cutoff_decision = analysis.get("cutoffDecision")
    if decision == "COLLECTING" and cutoff is None and cutoff_decision is None:
        pass
    else:
        _require_utc_iso(analysis, "analysisCutoff")
        if not isinstance(cutoff_decision, dict):
            raise StrategyReviewError("analysis cutoffDecision must be an object")
    _required_string(analysis, "gatePolicyVersion")
    experiment = analysis.get("experiment")
    if not isinstance(experiment, dict):
        raise StrategyReviewError("analysis experiment must be an object")
    for name in ("experimentId", "portfolioId", "strategyVersion", "configHash"):
        _required_string(experiment, name)
    if "gates" not in analysis:
        raise StrategyReviewError("analysis is missing gates")
    _normalized_gates(analysis["gates"])
    promotion_eligible = analysis.get("promotionEligible")
    if promotion_eligible is not None:
        if type(promotion_eligible) is not bool:
            raise StrategyReviewError("analysis promotionEligible must be a boolean")
        if promotion_eligible != (decision == "PROMOTION_READY"):
            raise StrategyReviewError(
                "analysis promotionEligible is inconsistent with its decision"
            )
    paper_activation_eligible = analysis.get("paperActivationEligible")
    if paper_activation_eligible is not None:
        if type(paper_activation_eligible) is not bool:
            raise StrategyReviewError("analysis paperActivationEligible must be a boolean")
        expected = decision in {"PROMOTION_READY", "SHADOW_READY_FOR_PAPER"}
        if paper_activation_eligible != expected:
            raise StrategyReviewError(
                "analysis paperActivationEligible is inconsistent with its decision"
            )
    review_stage = analysis.get("reviewStage")
    if review_stage is not None and review_stage not in {"PAPER", "SHADOW_TO_PAPER"}:
        raise StrategyReviewError("analysis reviewStage is unsupported")
    if decision == "SHADOW_READY_FOR_PAPER" and review_stage != "SHADOW_TO_PAPER":
        raise StrategyReviewError("SHADOW_READY_FOR_PAPER requires reviewStage SHADOW_TO_PAPER")


def _normalized_gates(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        raise StrategyReviewError("analysis gates must be an object")
    raw_gates = [(str(name), gate) for name, gate in value.items()]
    gates: list[dict[str, Any]] = []
    names: set[str] = set()
    for keyed_name, raw_gate in raw_gates:
        if not isinstance(raw_gate, dict):
            raise StrategyReviewError("each analysis gate must be an object")
        name = keyed_name
        _require_id(name, "gate name")
        if name in names:
            raise StrategyReviewError(f"duplicate analysis gate: {name}")
        names.add(name)
        if "threshold" not in raw_gate or "actual" not in raw_gate:
            raise StrategyReviewError(f"analysis gate {name} must include threshold and actual")
        passed = raw_gate.get("passed")
        required = raw_gate.get("required")
        if (passed is not None and type(passed) is not bool) or type(required) is not bool:
            raise StrategyReviewError(
                f"analysis gate {name} passed must be boolean/null and required must be boolean"
            )
        if "name" in raw_gate and raw_gate["name"] != name:
            raise StrategyReviewError(f"analysis gate {name} has a conflicting name")
        gate = _json_copy(raw_gate)
        if not isinstance(gate, dict):  # Defensive: raw_gate was already an object.
            raise StrategyReviewError(f"analysis gate {name} is not a JSON object")
        gate["name"] = name
        gates.append(gate)
    return sorted(gates, key=lambda gate: gate["name"])


def _validate_patch(
    parent: DynamicUniversePolicy, patch: Mapping[str, object]
) -> dict[str, object]:
    if not patch:
        raise StrategyReviewError("candidate patch must not be empty")
    unknown = set(patch) - PATCHABLE_POLICY_FIELDS
    if unknown:
        raise StrategyReviewError("policy fields are not patchable: " + ", ".join(sorted(unknown)))
    annotations = get_type_hints(DynamicUniversePolicy)
    typed: dict[str, object] = {}
    for name, value in patch.items():
        _require_typed_value(name, value, annotations[name])
        typed[name] = value
    # ``parent`` is used intentionally to make a future field/type mismatch a
    # proposal-time failure rather than a replace() surprise.
    policy_names = {field.name for field in fields(parent)}
    if not set(typed) <= policy_names:
        raise StrategyReviewError("patch references fields absent from parent policy")
    return typed


def _require_paper_safety(policy: DynamicUniversePolicy) -> None:
    """Keep automatically proposed Paper strategies inside a hard envelope."""

    maximums: tuple[tuple[str, int | Decimal, int | Decimal], ...] = (
        ("maximum_positions", policy.maximum_positions, 3),
        ("invested_fraction", policy.invested_fraction, Decimal("0.90")),
        ("maximum_asset_weight", policy.maximum_asset_weight, Decimal("0.40")),
        (
            "maximum_daily_turnover_fraction",
            policy.maximum_daily_turnover_fraction,
            Decimal("6"),
        ),
        ("maximum_daily_fee_fraction", policy.maximum_daily_fee_fraction, Decimal("0.005")),
        (
            "maximum_daily_realized_loss_fraction",
            policy.maximum_daily_realized_loss_fraction,
            Decimal("0.02"),
        ),
    )
    violations = [name for name, actual, maximum in maximums if actual > maximum]
    if (
        policy.bullish_daily_turnover_fraction is not None
        and policy.bullish_daily_turnover_fraction > Decimal("8")
    ):
        violations.append("bullish_daily_turnover_fraction")
    if policy.turnover_sell_weight < Decimal("0.5"):
        violations.append("turnover_sell_weight")
    if violations:
        raise StrategyReviewError(
            "candidate exceeds the automatic Paper safety envelope: "
            + ", ".join(sorted(violations))
        )


def _require_typed_value(name: str, value: object, annotation: Any) -> None:
    arguments = get_args(annotation)
    if type(None) in arguments:
        if value is None:
            return
        non_null = [item for item in arguments if item is not type(None)]
        if len(non_null) != 1:
            raise StrategyReviewError(f"unsupported patch type for {name}")
        annotation = non_null[0]
    if annotation is Decimal:
        valid = isinstance(value, Decimal) and value.is_finite()
    elif annotation is timedelta:
        valid = isinstance(value, timedelta)
    elif annotation is int:
        valid = type(value) is int
    elif annotation is float:
        valid = type(value) is float and math.isfinite(value)
    elif annotation is str:
        valid = isinstance(value, str)
    elif get_args(annotation) == (str, ...):
        valid = isinstance(value, tuple) and all(isinstance(item, str) for item in value)
    else:
        valid = False
    if not valid:
        raise StrategyReviewError(
            f"patch field {name} must use its declared Python type {annotation}"
        )


def _changed_policy_fields(
    parent: DynamicUniversePolicy, candidate: DynamicUniversePolicy
) -> set[str]:
    return {
        field.name
        for field in fields(parent)
        if field.name != "strategy_version"
        and getattr(parent, field.name) != getattr(candidate, field.name)
    }


def _patch_audit(
    parent: DynamicUniversePolicy,
    candidate: DynamicUniversePolicy,
    names: set[str],
) -> list[dict[str, object]]:
    return [
        {
            "field": name,
            "declaredType": _declared_type_name(get_type_hints(DynamicUniversePolicy)[name]),
            "oldValue": _policy_value(getattr(parent, name)),
            "newValue": _policy_value(getattr(candidate, name)),
        }
        for name in sorted(names)
    ]


def _declared_type_name(annotation: Any) -> str:
    arguments = get_args(annotation)
    optional = type(None) in arguments
    if optional:
        annotation = next(item for item in arguments if item is not type(None))
    if annotation is Decimal:
        value = "decimal"
    elif annotation is timedelta:
        value = "durationSeconds"
    elif annotation is int:
        value = "integer"
    elif annotation is float:
        value = "float"
    elif annotation is str:
        value = "string"
    elif get_args(annotation) == (str, ...):
        value = "stringArray"
    else:
        value = str(annotation)
    return f"optional[{value}]" if optional else value


def _policy_value(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, timedelta):
        if value.microseconds:
            raise StrategyReviewError("duration patch values require whole-second precision")
        return value.days * 86_400 + value.seconds
    if isinstance(value, tuple):
        return list(value)
    return value


def _require_schema(value: Mapping[str, object], label: str) -> None:
    if value.get("schemaVersion") != REVIEW_ARTIFACT_SCHEMA_VERSION:
        raise StrategyReviewError(
            f"{label} schemaVersion must equal {REVIEW_ARTIFACT_SCHEMA_VERSION}"
        )


def _require_analysis_schema(value: Mapping[str, object]) -> None:
    if value.get("schemaVersion") not in SUPPORTED_ANALYSIS_SCHEMA_VERSIONS:
        supported = ", ".join(str(item) for item in sorted(SUPPORTED_ANALYSIS_SCHEMA_VERSIONS))
        raise StrategyReviewError(f"analysis schemaVersion must be one of: {supported}")


def _required_string(value: Mapping[str, object], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item:
        raise StrategyReviewError(f"{name} must be a non-empty string")
    return item


def _require_utc_iso(value: Mapping[str, object], name: str) -> str:
    item = _required_string(value, name)
    try:
        parsed = datetime.fromisoformat(item.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StrategyReviewError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise StrategyReviewError(f"{name} must be a UTC timestamp")
    return item


def _require_id(value: str, label: str) -> None:
    if _ID_PATTERN.fullmatch(value) is None:
        raise StrategyReviewError(f"invalid {label}: {value!r}")


def _require_artifact_path(path: str | Path, root: Path, label: str) -> Path:
    source = Path(path).resolve()
    if source.parent != root:
        raise StrategyReviewError(f"{label} path is outside its artifact root")
    return source


def _load_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise StrategyReviewError(f"symbolic-link artifacts are not allowed: {path}")
    if not path.is_file():
        raise StrategyReviewError(f"artifact does not exist: {path}")
    payload = path.read_bytes()
    if len(payload) > MAXIMUM_ARTIFACT_BYTES:
        raise StrategyReviewError(f"artifact exceeds safe size limit: {path}")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StrategyReviewError(f"invalid JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise StrategyReviewError(f"JSON artifact must contain an object: {path}")
    return value


def _publish_json(
    path: Path,
    value: Mapping[str, object],
    *,
    stable_fields: tuple[str, ...] = (),
) -> Path:
    if path.is_symlink():
        raise StrategyReviewError(f"symbolic-link artifacts are not allowed: {path}")
    if path.exists():
        existing = _load_json(path)
        expected = dict(value)
        for field in stable_fields:
            if field in existing:
                _require_utc_iso(existing, field)
                expected[field] = existing[field]
        if _canonical_json(existing) == _canonical_json(expected):
            return path
        raise StrategyReviewError(f"immutable artifact already exists with other content: {path}")
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise StrategyReviewError(f"immutable artifact already exists: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StrategyReviewError("artifact contains non-canonical JSON values") from exc


def _sha256_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise StrategyReviewError(f"cannot hash non-regular artifact: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_one(
    database: Path,
    statement: str,
    parameters: tuple[object, ...],
    *,
    database_url: str | None = None,
    database_schema: str = "investment",
) -> Any:
    if database_url:
        try:
            with postgres_connection(
                database_url,
                database_schema,
                read_only=True,
            ) as connection:
                return connection.execute(statement, parameters).fetchone()
        except psycopg.Error as exc:
            raise StrategyReviewError(f"read-only PostgreSQL verification failed: {exc}") from exc
    if database.is_symlink() or not database.is_file():
        raise StrategyReviewError(f"SQLite database does not exist: {database}")
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            return cast(sqlite3.Row | None, connection.execute(statement, parameters).fetchone())
    except sqlite3.Error as exc:
        raise StrategyReviewError(f"read-only SQLite verification failed: {exc}") from exc


def _utc_timestamp(value: datetime | None) -> str:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise StrategyReviewError("artifact timestamp must be timezone-aware")
    return current.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_copy(value: object) -> object:
    try:
        return json.loads(json.dumps(value, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise StrategyReviewError("gate threshold/actual must be finite JSON values") from exc
