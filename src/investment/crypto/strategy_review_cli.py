"""Command-line adapter for deterministic strategy review automation."""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast, get_args, get_type_hints

from investment.crypto.application.dynamic_paper_rebalance import DynamicUniversePolicy
from investment.crypto.research.strategy_review import (
    ReviewStage,
    StrategyReviewAnalyzer,
)
from investment.crypto.research.strategy_review_policy import thresholds_for_strategy_version
from investment.crypto.strategy_registry import StrategyRegistry
from investment.crypto.strategy_review_workflow import StrategyReviewWorkflow
from investment.interfaces.api.fastapi.settings import Settings


def configure_strategy_review_parser(subcommands: Any) -> None:
    parser = subcommands.add_parser(
        "strategy-review",
        help="analyze evidence and prepare immutable Paper strategy artifacts",
    )
    actions = parser.add_subparsers(dest="strategy_review_action", required=True)

    analyze = actions.add_parser("analyze", help="run the read-only promotion gates")
    analyze.add_argument("--experiment", dest="experiment_id")
    analyze.add_argument("--portfolio", dest="portfolio_id")
    analyze.add_argument(
        "--strategy-version",
        help=(
            "bind derivatives provenance gates to this immutable policy; required for a "
            "non-default shadow feature version such as V2.5"
        ),
    )
    analyze.add_argument(
        "--review-stage",
        choices=("PAPER", "SHADOW_TO_PAPER"),
        help=(
            "override the review stage; a configured Shadow policy defaults to "
            "SHADOW_TO_PAPER"
        ),
    )

    propose = actions.add_parser("propose", help="create a shadow-only candidate snapshot")
    propose.add_argument("--analysis", required=True, type=Path)
    propose.add_argument("--parent", dest="parent_version")
    propose.add_argument("--candidate", dest="candidate_version", required=True)
    propose.add_argument("--proposal-id")
    propose.add_argument(
        "--patch-json",
        required=True,
        help="JSON object; decimals are strings, durations are integer seconds",
    )

    validate = actions.add_parser("validate", help="reproduce and hash-check a proposal")
    validate.add_argument("--proposal", required=True, type=Path)
    validate.add_argument("--validation-id")

    promote = actions.add_parser(
        "promote-paper",
        help="prepare a Paper activation manifest without changing the running assignment",
    )
    promote.add_argument("--validation", required=True, type=Path)
    promote.add_argument("--analysis", required=True, type=Path)
    promote.add_argument("--new-portfolio-id", required=True)
    promote.add_argument("--new-experiment-id", required=True)
    promote.add_argument("--initial-cash", default="1000000")
    promote.add_argument("--promotion-id")
    promote.add_argument("--drain-experiment-id", action="append", default=[])
    promote.add_argument(
        "--request-paper-execution",
        action="store_true",
        help="record an explicit Paper-only execution request in the prepared manifest",
    )


def run_strategy_review_command(arguments: argparse.Namespace) -> None:
    settings = Settings()
    workflow = StrategyReviewWorkflow(
        registry_root=settings.crypto_strategy_config_root,
        review_root=settings.crypto_strategy_review_root,
        observation_database=settings.crypto_observation_database,
        paper_database=settings.crypto_paper_database,
        database_url=settings.database_url,
        database_schema=settings.database_schema,
    )
    action = str(arguments.strategy_review_action)
    if action == "analyze":
        experiment_id = arguments.experiment_id or settings.runtime_observation_experiment_id
        if not experiment_id:
            raise SystemExit(
                "--experiment or INVESTMENT_RUNTIME_OBSERVATION_EXPERIMENT_ID is required"
            )
        thresholds = None
        review_stage = cast(ReviewStage, arguments.review_stage or "PAPER")
        if arguments.strategy_version:
            policy = StrategyRegistry(settings.crypto_strategy_config_root).load(
                str(arguments.strategy_version)
            )
            thresholds = thresholds_for_strategy_version(
                policy.strategy_version,
                policy.derivatives_feature_version,
            )
            if policy.strategy_version in {
                "dynamic-intraday-v2.5",
                "dynamic-intraday-v2.6",
            }:
                if arguments.review_stage not in {None, "SHADOW_TO_PAPER"}:
                    raise SystemExit(
                        f"{policy.strategy_version} can only be reviewed at "
                        "SHADOW_TO_PAPER stage"
                    )
                review_stage = "SHADOW_TO_PAPER"
            elif arguments.review_stage is None and policy.derivatives_overlay_mode == "SHADOW":
                review_stage = "SHADOW_TO_PAPER"
        report = StrategyReviewAnalyzer(
            settings.crypto_observation_database,
            settings.crypto_paper_database,
            thresholds,
            database_url=settings.database_url,
            database_schema=settings.database_schema,
        ).analyze(
            experiment_id,
            portfolio_id=arguments.portfolio_id,
            review_stage=review_stage,
        )
        path = workflow.publish_analysis(report)
        _print_payload({"artifactPath": str(path), "report": report})
        return

    if action == "propose":
        analysis = _load_object(arguments.analysis)
        parent_version = arguments.parent_version or _analysis_strategy_version(analysis)
        candidate_version = str(arguments.candidate_version)
        patch = _coerce_patch(
            StrategyRegistry(settings.crypto_strategy_config_root).load(parent_version),
            _load_json_object(arguments.patch_json, "--patch-json"),
        )
        proposal_id = arguments.proposal_id or _derived_id(
            "proposal", candidate_version, _required_text(analysis, "analysisId")
        )
        path = workflow.propose_candidate(
            proposal_id=proposal_id,
            analysis_path=arguments.analysis,
            parent_version=parent_version,
            candidate_version=candidate_version,
            patch=patch,
        )
        _print_payload({"artifactPath": str(path), "proposal": _load_object(path)})
        return

    if action == "validate":
        proposal = _load_object(arguments.proposal)
        validation_id = arguments.validation_id or _derived_id(
            "validation",
            _required_text(proposal, "candidateVersion"),
            _required_text(proposal, "proposalId"),
        )
        path = workflow.validate_candidate(
            validation_id=validation_id,
            proposal_path=arguments.proposal,
        )
        _print_payload({"artifactPath": str(path), "validation": _load_object(path)})
        return

    if action == "promote-paper":
        analysis = _load_object(arguments.analysis)
        validation = _load_object(arguments.validation)
        candidate_version = _required_text(validation, "candidateVersion")
        promotion_id = arguments.promotion_id or _derived_id(
            "promotion", candidate_version, _required_text(analysis, "analysisId")
        )
        try:
            initial_cash = Decimal(str(arguments.initial_cash))
        except InvalidOperation as error:
            raise SystemExit("--initial-cash must be a decimal number") from error
        path = workflow.prepare_paper_promotion(
            promotion_id=promotion_id,
            validation_path=arguments.validation,
            analysis_path=arguments.analysis,
            new_portfolio_id=str(arguments.new_portfolio_id),
            new_experiment_id=str(arguments.new_experiment_id),
            initial_cash=initial_cash,
            paper_execute_requested=bool(arguments.request_paper_execution),
            drain_experiment_ids=tuple(arguments.drain_experiment_id),
        )
        _print_payload({"artifactPath": str(path), "promotion": _load_object(path)})
        return

    raise SystemExit(f"unknown strategy-review action: {action}")


def _coerce_patch(
    parent: DynamicUniversePolicy, raw_patch: Mapping[str, object]
) -> dict[str, object]:
    annotations = get_type_hints(DynamicUniversePolicy)
    values: dict[str, object] = {}
    for name, raw in raw_patch.items():
        if name == "strategy_version" or name not in annotations:
            values[name] = raw
            continue
        annotation = annotations[name]
        arguments = get_args(annotation)
        optional = type(None) in arguments
        if raw is None:
            if not optional:
                raise SystemExit(f"patch field {name} is not optional")
            values[name] = None
            continue
        if optional:
            annotation = next(item for item in arguments if item is not type(None))
        if annotation is Decimal:
            try:
                decimal_value = Decimal(str(raw))
            except InvalidOperation as error:
                raise SystemExit(f"patch field {name} must be a decimal") from error
            if not decimal_value.is_finite():
                raise SystemExit(f"patch field {name} must be finite")
            values[name] = decimal_value
        elif annotation is timedelta:
            if type(raw) is not int:
                raise SystemExit(f"patch field {name} must be integer seconds")
            values[name] = timedelta(seconds=raw)
        elif get_args(annotation) == (str, ...):
            if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
                raise SystemExit(f"patch field {name} must be a string array")
            values[name] = tuple(raw)
        elif annotation is int:
            if type(raw) is not int:
                raise SystemExit(f"patch field {name} must be an integer")
            values[name] = raw
        elif annotation is float:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise SystemExit(f"patch field {name} must be a number")
            float_value = float(raw)
            if not math.isfinite(float_value):
                raise SystemExit(f"patch field {name} must be finite")
            values[name] = float_value
        elif annotation is str:
            if not isinstance(raw, str):
                raise SystemExit(f"patch field {name} must be a string")
            values[name] = raw
        else:
            values[name] = raw
    if all(getattr(parent, name, object()) == value for name, value in values.items()):
        raise SystemExit("candidate patch does not change the parent policy")
    return values


def _analysis_strategy_version(analysis: Mapping[str, object]) -> str:
    experiment = analysis.get("experiment")
    if not isinstance(experiment, dict):
        raise SystemExit("analysis artifact is missing experiment identity")
    return _required_text(experiment, "strategyVersion")


def _load_json_object(value: str, label: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise SystemExit(f"{label} must be valid JSON") from error
    if not isinstance(parsed, dict):
        raise SystemExit(f"{label} must contain a JSON object")
    return parsed


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read JSON artifact {path}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"JSON artifact must contain an object: {path}")
    return value


def _required_text(value: Mapping[str, object], name: str) -> str:
    result = value.get(name)
    if not isinstance(result, str) or not result:
        raise SystemExit(f"artifact field {name} must be a non-empty string")
    return result


def _derived_id(prefix: str, version: str, source_id: str) -> str:
    suffix = source_id.rsplit("-", 1)[-1][:16]
    return f"{prefix}-{version}-{suffix}"[:160]


def _print_payload(payload: Mapping[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str))
