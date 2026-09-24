"""Read-only V2.8 postmortem research.

Run this *inside* the Compose container so SQLite WAL coordination stays in the
Docker VM::

    docker compose exec -T investment-engine python - < research/v28_postmortem.py

It writes an immutable, timestamped JSON/Markdown report under
``/app/experiments/research``.  It never writes to the source databases and is
not imported by the trading runtime.
"""

# ruff: noqa: E501

from __future__ import annotations

import json
import math
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
from sklearn.inspection import permutation_importance
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.tree import DecisionTreeClassifier, export_text

OBSERVATION_DB = "data/observations/crypto-forward.sqlite3"
PAPER_DB = "data/paper/crypto-trading.sqlite3"
OUTPUT_ROOT = Path("experiments/research")
EXPERIMENT_PREFIX = "paper-v2.8-ablation-20260909-"
ROUND_TRIP_COST = 0.002
HORIZONS = (15, 30, 60, 120, 240)
PRIMARY_HORIZON = 240
FEATURES = ("score", "momentum_1h", "momentum_4h", "momentum_24h", "volatility")


@dataclass(frozen=True)
class Summary:
    count: int
    mean_gross: float | None
    mean_after_cost: float | None
    win_rate: float | None
    payoff_ratio: float | None
    expectancy: float | None
    mfe: float | None
    mae: float | None


def read_only_connection(path: str) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def finite(value: Any) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def completed(rows: Iterable[sqlite3.Row], horizon: int) -> list[sqlite3.Row]:
    return [row for row in rows if finite(row[f"return_{horizon}"]) is not None]


def summarise(rows: Iterable[sqlite3.Row], horizon: int = PRIMARY_HORIZON) -> Summary:
    data = completed(rows, horizon)
    values = [float(row[f"return_{horizon}"]) for row in data]
    if not values:
        return Summary(0, None, None, None, None, None, None, None)
    gains = [value for value in values if value > 0]
    losses = [-value for value in values if value < 0]
    payoff = mean(gains) / mean(losses) if gains and losses and mean(losses) else None
    return Summary(
        count=len(values),
        mean_gross=mean(values),
        mean_after_cost=mean(value - ROUND_TRIP_COST for value in values),
        win_rate=sum(value > 0 for value in values) / len(values),
        payoff_ratio=payoff,
        expectancy=mean(values),
        mfe=mean(
            float(row[f"mfe_{horizon}"])
            for row in data
            if finite(row[f"mfe_{horizon}"]) is not None
        ),
        mae=mean(
            float(row[f"mae_{horizon}"])
            for row in data
            if finite(row[f"mae_{horizon}"]) is not None
        ),
    )


def bootstrap_decision_ci(
    rows: Sequence[sqlite3.Row], horizon: int, seed: int = 7
) -> list[float | None]:
    """Cluster bootstrap by decision, not by correlated candidate row."""
    groups: dict[str, list[float]] = defaultdict(list)
    for row in completed(rows, horizon):
        groups[str(row["decision_id"])].append(float(row[f"return_{horizon}"]))
    if len(groups) < 20:
        return [None, None]
    group_means = np.asarray([np.mean(values) for values in groups.values()])
    generator = np.random.default_rng(seed)
    sampled = np.asarray(
        [
            np.mean(generator.choice(group_means, size=len(group_means), replace=True))
            for _ in range(100)
        ]
    )
    return [float(np.quantile(sampled, 0.025)), float(np.quantile(sampled, 0.975))]


def load_snapshots(
    connection: sqlite3.Connection,
    experiment_id: str,
    *,
    selected_only: bool = False,
) -> list[sqlite3.Row]:
    """Load only one experiment, then use primary-key outcome lookups in chunks.

    The outcome table contains millions of rows.  A single aggregation join can
    make SQLite scan far more data than this research query needs.
    """
    snapshots = list(
        connection.execute(
            """SELECT * FROM decision_snapshot WHERE experiment_id=?
               AND (?=0 OR selected=1)
               ORDER BY decision_time, asset""",
            (experiment_id, int(selected_only)),
        )
    )
    payloads = {str(row["snapshot_id"]): dict(row) for row in snapshots}
    for payload in payloads.values():
        for horizon in HORIZONS:
            payload[f"return_{horizon}"] = None
            payload[f"mfe_{horizon}"] = None
            payload[f"mae_{horizon}"] = None
    snapshot_ids = list(payloads)
    for start in range(0, len(snapshot_ids), 400):
        chunk = snapshot_ids[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        outcomes = connection.execute(
            f"""SELECT snapshot_id, horizon_minutes, forward_return, mfe, mae
                FROM decision_outcome_minute
                WHERE snapshot_id IN ({placeholders})
                  AND horizon_minutes IN ({",".join("?" for _ in HORIZONS)})
                  AND status='COMPLETED'""",
            (*chunk, *HORIZONS),
        )
        for outcome in outcomes:
            payload = payloads[str(outcome["snapshot_id"])]
            horizon = int(outcome["horizon_minutes"])
            payload[f"return_{horizon}"] = outcome["forward_return"]
            payload[f"mfe_{horizon}"] = outcome["mfe"]
            payload[f"mae_{horizon}"] = outcome["mae"]
    return list(payloads.values())  # type: ignore[return-value]


def reasons(row: sqlite3.Row) -> set[str]:
    try:
        return set(json.loads(str(row["candidate_reasons_json"])))
    except (json.JSONDecodeError, TypeError):
        return set()


def reason_groups(rows: Sequence[sqlite3.Row]) -> dict[str, dict[str, Any]]:
    candidate_rows = [row for row in rows if row["rank"] is not None]
    all_reasons = Counter(reason for row in candidate_rows for reason in reasons(row))
    result: dict[str, dict[str, Any]] = {}
    for reason, count in all_reasons.most_common():
        rejected = [row for row in candidate_rows if reason in reasons(row)]
        accepted = [row for row in candidate_rows if reason not in reasons(row)]
        result[reason] = {
            "rejected": asdict(summarise(rejected)),
            "not_rejected": asdict(summarise(accepted)),
            "rejected_ci_95": bootstrap_decision_ci(rejected, PRIMARY_HORIZON),
            "not_rejected_ci_95": bootstrap_decision_ci(accepted, PRIMARY_HORIZON),
            "rows": count,
        }
    return result


def funnel(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    """Approximate sequential funnel from immutable per-candidate reasons.

    Filters are evaluated concurrently by the live engine, so this is deliberately
    labelled as a reconstruction rather than a causal replay.
    """
    stages: tuple[tuple[str, set[str]], ...] = (
        ("Market opportunity", set()),
        ("Liquidity candidate", set()),
        (
            "Momentum / volatility pass",
            {
                "NEW_ENTRY_SCORE_BELOW_HURDLE",
                "NEW_ENTRY_BLOCKED_BY_FALLING_KNIFE_GUARD",
                "NEW_ENTRY_BLOCKED_BY_SHORT_TERM_SPIKE_GUARD",
                "NEW_ENTRY_BLOCKED_BY_4H_FALLING_KNIFE_GUARD",
                "NEW_ENTRY_BLOCKED_BY_4H_SPIKE_GUARD",
                "NEW_ENTRY_BLOCKED_BY_24H_TREND_GUARD",
                "NEW_ENTRY_BLOCKED_BY_24H_SPIKE_GUARD",
                "NEW_ENTRY_BLOCKED_BY_VOLATILITY_GUARD",
            },
        ),
        ("Confirmation pass", {"NEW_ENTRY_WAITING_FOR_CONFIRMATIONS"}),
        (
            "BTC / squeeze / crowding pass",
            {
                "NEW_ENTRY_BLOCKED_BY_DERIVATIVES_DATA_UNAVAILABLE",
                "NEW_ENTRY_BLOCKED_BY_DERIVATIVES_RISK",
                "NEW_ENTRY_BLOCKED_BY_CROWDING_DATA_UNAVAILABLE",
                "NEW_ENTRY_BLOCKED_BY_LONG_CROWDING_UNWIND",
                "NEW_ENTRY_BLOCKED_BY_LONG_CROWDING_AND_BEARISH_TRIGGER",
            },
        ),
        ("Cost calibration pass", {"NEW_ENTRY_FEE_ADJUSTED_RETURN_INSUFFICIENT"}),
        ("Cooldown pass", {"NEW_ENTRY_BLOCKED_BY_REENTRY_COOLDOWN"}),
        (
            "Risk / concentration pass",
            {
                "NEW_ENTRY_BLOCKED_BY_SELECTION_CONCENTRATION",
                "NEW_ENTRY_BLOCKED_BY_ROLLING_ASSET_LOSS",
            },
        ),
    )
    market = [row for row in rows if finite(row["reference_price"]) is not None]
    candidates = [row for row in market if row["rank"] is not None]
    active = candidates
    output = [
        {
            "stage": stages[0][0],
            "rows": len(market),
            "acceptance_rate": 1.0,
            "metrics": asdict(summarise(market)),
        },
        {
            "stage": stages[1][0],
            "rows": len(candidates),
            "acceptance_rate": len(candidates) / len(market) if market else None,
            "metrics": asdict(summarise(candidates)),
        },
    ]
    for stage, blocked in stages[2:]:
        active = [row for row in active if not (reasons(row) & blocked)]
        output.append(
            {
                "stage": stage,
                "rows": len(active),
                "acceptance_rate": len(active) / len(candidates) if candidates else None,
                "metrics": asdict(summarise(active)),
            }
        )
    selected = [row for row in rows if int(row["selected"]) == 1]
    output.append(
        {
            "stage": "Selected target (includes retained holdings)",
            "rows": len(selected),
            "acceptance_rate": len(selected) / len(candidates) if candidates else None,
            "metrics": asdict(summarise(selected)),
        }
    )
    return output


def quantile_analysis(rows: Sequence[sqlite3.Row]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    usable = completed([row for row in rows if row["rank"] is not None], PRIMARY_HORIZON)
    for feature in FEATURES:
        available = sorted(
            ((finite(row[feature]), row) for row in usable if finite(row[feature]) is not None),
            key=lambda item: float(item[0]),
        )
        if len(available) < 100:
            continue
        chunks = np.array_split(np.asarray([row for _, row in available], dtype=object), 5)
        result[feature] = []
        for index, chunk in enumerate(chunks, start=1):
            group = list(chunk)
            summary = asdict(summarise(group))
            summary.update(
                {
                    "quintile": index,
                    "minimum": finite(group[0][feature]),
                    "maximum": finite(group[-1][feature]),
                    "ci_95": bootstrap_decision_ci(group, PRIMARY_HORIZON, seed=index),
                }
            )
            result[feature].append(summary)
    return result


def boundary_analysis(rows: Sequence[sqlite3.Row]) -> dict[str, dict[str, Any]]:
    boundaries = {
        "score_hurdle_0.60": ("score", 0.60, 0.03),
        "momentum_1h_floor_-0.007": ("momentum_1h", -0.007, 0.002),
        "momentum_1h_ceiling_0.005": ("momentum_1h", 0.005, 0.002),
        "momentum_4h_floor_-0.03": ("momentum_4h", -0.03, 0.005),
        "momentum_4h_ceiling_0.005": ("momentum_4h", 0.005, 0.003),
    }
    candidates = [row for row in rows if row["rank"] is not None]
    result: dict[str, dict[str, Any]] = {}
    for label, (feature, threshold, width) in boundaries.items():
        below = [
            row
            for row in candidates
            if (value := finite(row[feature])) is not None
            and threshold - width <= value < threshold
        ]
        above = [
            row
            for row in candidates
            if (value := finite(row[feature])) is not None
            and threshold <= value <= threshold + width
        ]
        result[label] = {
            "feature": feature,
            "threshold": threshold,
            "below": asdict(summarise(below)),
            "above": asdict(summarise(above)),
            "below_ci_95": bootstrap_decision_ci(below, PRIMARY_HORIZON, seed=31),
            "above_ci_95": bootstrap_decision_ci(above, PRIMARY_HORIZON, seed=32),
        }
    return result


def btc_regime_analysis(rows: Sequence[sqlite3.Row]) -> dict[str, dict[str, Any]]:
    by_decision: dict[str, sqlite3.Row] = {}
    for row in rows:
        if row["asset"] == "BTC":
            by_decision[str(row["decision_id"])] = row
    selected = [row for row in rows if int(row["selected"]) == 1 and row["asset"] != "BTC"]
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in selected:
        btc = by_decision.get(str(row["decision_id"]))
        if btc is None:
            continue
        m1, m4 = finite(btc["momentum_1h"]), finite(btc["momentum_4h"])
        if m1 is None or m4 is None:
            continue
        label = (
            "BTC_UP_1H_4H"
            if m1 > 0 and m4 > 0
            else "BTC_DOWN_1H_4H"
            if m1 < 0 and m4 < 0
            else "BTC_MIXED"
        )
        groups[label].append(row)
    return {
        label: {
            "metrics": asdict(summarise(group)),
            "ci_95": bootstrap_decision_ci(group, PRIMARY_HORIZON, seed=index),
        }
        for index, (label, group) in enumerate(sorted(groups.items()), start=41)
    }


def ml_probe(rows: Sequence[sqlite3.Row]) -> dict[str, Any]:
    """Strict chronological, shallow model probe; not a trading model."""
    data = [
        row
        for row in rows
        if row["rank"] is not None
        and finite(row[f"return_{PRIMARY_HORIZON}"]) is not None
        and all(finite(row[feature]) is not None for feature in FEATURES)
    ]
    decisions = sorted({str(row["decision_id"]) for row in data})
    if len(decisions) < 80:
        return {"status": "INSUFFICIENT_DECISIONS"}
    split = int(len(decisions) * 0.70)
    train_ids, test_ids = set(decisions[:split]), set(decisions[split:])
    train, test = (
        [row for row in data if str(row["decision_id"]) in train_ids],
        [row for row in data if str(row["decision_id"]) in test_ids],
    )
    x_train = np.asarray([[float(row[feature]) for feature in FEATURES] for row in train])
    x_test = np.asarray([[float(row[feature]) for feature in FEATURES] for row in test])
    y_train = np.asarray(
        [float(row[f"return_{PRIMARY_HORIZON}"]) > ROUND_TRIP_COST for row in train]
    )
    y_test = np.asarray([float(row[f"return_{PRIMARY_HORIZON}"]) > ROUND_TRIP_COST for row in test])
    if len(set(y_train)) < 2 or len(set(y_test)) < 2:
        return {"status": "INSUFFICIENT_TARGET_VARIATION"}
    model = DecisionTreeClassifier(
        max_depth=2, min_samples_leaf=200, class_weight="balanced", random_state=7
    )
    model.fit(x_train, y_train)
    probabilities = model.predict_proba(x_test)[:, 1]
    permutation = permutation_importance(
        model, x_test, y_test, n_repeats=10, random_state=7, scoring="roc_auc"
    )
    return {
        "status": "EXPLORATORY_ONLY",
        "train_decisions": len(train_ids),
        "test_decisions": len(test_ids),
        "train_rows": len(train),
        "test_rows": len(test),
        "test_positive_rate": float(np.mean(y_test)),
        "test_auc": float(roc_auc_score(y_test, probabilities)),
        "test_average_precision": float(average_precision_score(y_test, probabilities)),
        "permutation_importance_test_auc": {
            feature: float(value)
            for feature, value in zip(FEATURES, permutation.importances_mean, strict=True)
        },
        "tree": export_text(model, feature_names=list(FEATURES)),
        "warning": "One chronological split over about one week is hypothesis generation only; it is not OOS validation.",
    }


def paper_execution_summary(connection: sqlite3.Connection, portfolio_id: str) -> dict[str, Any]:
    rows = list(
        connection.execute(
            """SELECT * FROM paper_execution WHERE portfolio_id=? ORDER BY executed_at""",
            (portfolio_id,),
        )
    )
    notional = [float(row["quantity"]) * float(row["price"]) for row in rows]
    sells = [row for row in rows if row["side"] == "SELL"]
    return {
        "executions": len(rows),
        "round_trips": len(sells),
        "turnover": sum(notional),
        "fees": sum(float(row["fee"]) for row in rows),
        "realized_pnl": sum(float(row["realized_pnl"]) for row in sells),
        "fill_versions": dict(Counter(str(row["execution_model_version"]) for row in rows)),
        "fee_rates": sorted({str(row["fee_rate"]) for row in rows}),
        "slippage_rates": sorted({str(row["slippage_rate"]) for row in rows}),
    }


def exit_diagnosis(
    paper: sqlite3.Connection,
    portfolio_id: str,
    selected_rows: Sequence[sqlite3.Row],
) -> dict[str, Any]:
    """Classify completed control trades from their frozen entry price paths.

    This maps accounting fills to the latest selected snapshot before the buy.
    It is a diagnostic, not a causal exit counterfactual or intrabar replay.
    """
    executions = list(
        paper.execute(
            "SELECT * FROM paper_execution WHERE portfolio_id=? ORDER BY executed_at",
            (portfolio_id,),
        )
    )
    selections: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in selected_rows:
        selections[str(row["asset"])].append(row)
    open_buys: dict[str, sqlite3.Row] = {}
    groups: dict[str, list[float]] = defaultdict(list)
    mapped = 0
    sells = 0
    for execution in executions:
        asset = str(execution["pair"]).replace("KRW", "")
        if execution["side"] == "BUY":
            open_buys[asset] = execution
            continue
        sells += 1
        entry = open_buys.pop(asset, None)
        if entry is None:
            continue
        snapshots = [
            row
            for row in selections[asset]
            if str(row["decision_time"]) <= str(entry["executed_at"])
        ]
        if not snapshots:
            continue
        snapshot = snapshots[-1]
        path = {horizon: finite(snapshot[f"return_{horizon}"]) for horizon in HORIZONS}
        mfe = finite(snapshot[f"mfe_{PRIMARY_HORIZON}"])
        realized = float(execution["realized_pnl"])
        if path[15] is not None and path[15] <= -ROUND_TRIP_COST:
            label = "A_IMMEDIATE_ADVERSE"
        elif (
            mfe is not None and mfe >= ROUND_TRIP_COST and path[240] is not None and path[240] <= 0
        ):
            label = "C_REVERSAL_AFTER_MFE"
        elif mfe is not None and mfe >= ROUND_TRIP_COST and realized <= 0:
            label = "B_PROFIT_NOT_CAPTURED_OR_TIMING"
        elif (
            path[240] is not None
            and abs(path[240]) < ROUND_TRIP_COST
            and (mfe is None or mfe < ROUND_TRIP_COST)
        ):
            label = "D_FLAT_COST_DOMINATED"
        else:
            label = "OTHER_OR_UNRESOLVED"
        groups[label].append(realized)
        mapped += 1
    return {
        "mapped_sells": mapped,
        "total_sells": sells,
        "by_type": {
            label: {"count": len(values), "realized_pnl": sum(values)}
            for label, values in sorted(groups.items())
        },
        "caveat": "Latest selected snapshot before a buy; cannot prove an exit counterfactual.",
    }


def version_execution_summary(
    observation: sqlite3.Connection, paper: sqlite3.Connection
) -> list[dict[str, Any]]:
    experiments = list(
        observation.execute(
            """SELECT experiment_id, portfolio_id, strategy_version, started_at, planned_end_at, status,
                      starting_equity FROM observation_experiment ORDER BY started_at"""
        )
    )
    output = []
    for item in experiments:
        row = dict(item)
        row.update(paper_execution_summary(paper, str(item["portfolio_id"])))
        output.append(row)
    return output


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# V2.8 Alpha Postmortem",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Scope and guardrails",
        "",
        "- Read-only analysis from SQLite inside the Docker VM.",
        "- Forward outcomes use records evaluated after the frozen decision timestamp.",
        "- Candidate rows from a single production-control lane are used for funnel/feature work to avoid treating duplicated ablation rows as independent samples.",
        "- All candidate confidence intervals cluster-bootstrap by decision timestamp; no random train/test split is used.",
        "- The ML result is an exploratory chronological holdout probe, not an alpha claim.",
        "",
        "## V2.8 lane execution",
        "",
        "| lane | net return | realized PnL | round trips | fees | turnover |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for lane, value in report["lanes"].items():
        execution = value["execution"]
        lines.append(
            f"| {lane} | {value['net_return_pct']:.3f}% | {execution['realized_pnl']:.0f} | "
            f"{execution['round_trips']} | {execution['fees']:.0f} | {execution['turnover']:.0f} |"
        )
    lines.extend(
        [
            "",
            "## Funnel (production-control; reconstruction)",
            "",
            "| stage | rows | 4h gross | 4h after 20bp | win rate |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["funnel"]:
        metrics = row["metrics"]
        lines.append(
            f"| {row['stage']} | {row['rows']} | {pct(metrics['mean_gross'])} | "
            f"{pct(metrics['mean_after_cost'])} | {pct(metrics['win_rate'])} |"
        )
    lines.extend(["", "## Limitations", "", *[f"- {item}" for item in report["limitations"]], ""])
    return "\n".join(lines)


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.3f}%"


def main() -> None:
    observation = read_only_connection(OBSERVATION_DB)
    paper = read_only_connection(PAPER_DB)
    experiments = list(
        observation.execute(
            """SELECT * FROM observation_experiment WHERE experiment_id LIKE ? ORDER BY experiment_id""",
            (f"{EXPERIMENT_PREFIX}%",),
        )
    )
    if len(experiments) != 4:
        raise RuntimeError(f"expected four V2.8 experiments, found {len(experiments)}")
    lanes: dict[str, Any] = {}
    all_rows: dict[str, list[sqlite3.Row]] = {}
    for experiment in experiments:
        name = str(experiment["experiment_id"]).removeprefix(EXPERIMENT_PREFIX)
        rows = load_snapshots(
            observation,
            str(experiment["experiment_id"]),
            selected_only=name != "production-control",
        )
        if name == "production-control":
            all_rows[name] = rows
        snapshot_rows = int(
            observation.execute(
                "SELECT COUNT(*) FROM decision_snapshot WHERE experiment_id=?",
                (experiment["experiment_id"],),
            ).fetchone()[0]
        )
        execution = paper_execution_summary(paper, str(experiment["portfolio_id"]))
        lanes[name] = {
            "experiment": dict(experiment),
            "snapshot_rows": snapshot_rows,
            "outcome_quality": {
                str(horizon): {
                    "completed": len(completed(rows, horizon)),
                    "summary": asdict(summarise(rows, horizon)),
                }
                for horizon in HORIZONS
            },
            "selected": asdict(summarise([row for row in rows if int(row["selected"]) == 1])),
            "execution": execution,
            "net_return_pct": execution["realized_pnl"]
            / float(experiment["starting_equity"])
            * 100,
        }
    control_rows = all_rows["production-control"]
    control_portfolio = next(
        item for item in experiments if str(item["experiment_id"]).endswith("production-control")
    )["portfolio_id"]
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "cost_assumption": {
            "round_trip_cost": ROUND_TRIP_COST,
            "explanation": "Research counterfactual deducts 20bp once. Actual fills retain their recorded fee/slippage values separately.",
        },
        "lanes": lanes,
        "funnel": funnel(control_rows),
        "rejection_reason_comparison": reason_groups(control_rows),
        "feature_quintiles_4h": quantile_analysis(control_rows),
        "threshold_boundaries_4h": boundary_analysis(control_rows),
        "btc_regimes_selected_4h": btc_regime_analysis(control_rows),
        "ml_probe_4h_after_cost": ml_probe(control_rows),
        "production_control_exit_diagnosis": exit_diagnosis(
            paper, str(control_portfolio), control_rows
        ),
        "version_execution_summary": version_execution_summary(observation, paper),
        "limitations": [
            "V2.8 has approximately one week of one market path; candidate rows within a decision are correlated.",
            "The database contains forward MFE/MAE but not intrabar path timing, order-book depth, quote-level fills, or a position-level equity curve. Exact exit attribution, slippage attribution, daily Sharpe, and drawdown by rule are therefore not identifiable.",
            "The four V2.8 lanes share the same candidate market observations. They must not be pooled as independent samples.",
            "Derivative fields are stored as per-decision context, but the observed V2.8 period may contain no actionable squeeze confirmation; this cannot validate a derivative gate.",
        ],
    }
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    json_path = OUTPUT_ROOT / f"v28-postmortem-{stamp}.json"
    markdown_path = OUTPUT_ROOT / f"v28-postmortem-{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n")
    markdown_path.write_text(markdown(report) + "\n")
    print(json.dumps({"json": str(json_path), "markdown": str(markdown_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
