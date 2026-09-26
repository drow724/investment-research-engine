"""Auditable research signals materialized from frozen candidate cohorts.

This module is deliberately outside the paper-trading decision path.  It may read
frozen V2.9 observations, but it must never change a strategy policy or portfolio.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass

import polars as pl


@dataclass(frozen=True, slots=True)
class SignalDefinition:
    signal_id: str
    signal_family: str
    signal_version: str
    universe: str
    required_features: tuple[str, ...]
    lookback_horizon: str
    intended_prediction_horizons: tuple[int, ...]
    description: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


SignalBuilder = Callable[[pl.DataFrame], pl.Expr]


class SignalRegistry:
    """Small in-process registry; definitions are immutable and IDs are unique."""

    def __init__(self) -> None:
        self._definitions: dict[str, SignalDefinition] = {}
        self._builders: dict[str, SignalBuilder] = {}

    def register(self, definition: SignalDefinition, builder: SignalBuilder) -> None:
        if definition.signal_id in self._definitions:
            raise ValueError(f"duplicate signal id: {definition.signal_id}")
        self._definitions[definition.signal_id] = definition
        self._builders[definition.signal_id] = builder

    def definitions(self) -> tuple[SignalDefinition, ...]:
        return tuple(self._definitions[key] for key in sorted(self._definitions))

    def materialize(self, snapshots: pl.DataFrame) -> pl.DataFrame:
        """Return long-form signal values and within-cohort ranks.

        The research universe is exactly the V2.9 scored cohort (normally the 20
        most liquid eligible markets).  BTC and universe-relative reference values
        are derived from the same point-in-time snapshot, before filtering to that
        scored cohort.
        """

        required = {
            "snapshot_id",
            "decision_time",
            "market",
            "eligible",
            "score",
            "raw_score",
            "momentum_1h",
            "momentum_4h",
            "momentum_24h",
            "volatility",
            "liquidity",
        }
        missing = required.difference(snapshots.columns)
        if missing:
            raise ValueError(f"missing frozen snapshot columns: {sorted(missing)}")

        frame = snapshots.with_columns(
            pl.col("decision_time").cast(pl.Datetime("us", "UTC")),
            pl.when(pl.col("market") == "BTCKRW")
            .then(pl.col("momentum_1h"))
            .otherwise(None)
            .max()
            .over("decision_time")
            .alias("btc_momentum_1h"),
            pl.col("momentum_1h")
            .filter((pl.col("eligible") == 1) & pl.col("score").is_not_null())
            .median()
            .over("decision_time")
            .alias("cohort_median_momentum_1h"),
        ).filter((pl.col("eligible") == 1) & pl.col("score").is_not_null())

        rows: list[pl.DataFrame] = []
        identity = ["snapshot_id", "decision_time", "market"]
        for definition in self.definitions():
            unavailable = set(definition.required_features).difference(frame.columns)
            if unavailable:
                raise ValueError(
                    f"signal {definition.signal_id} lacks features: {sorted(unavailable)}"
                )
            materialized = (
                frame.select(
                    *identity,
                    self._builders[definition.signal_id](frame).alias("value"),
                )
                .drop_nulls("value")
                .with_columns(
                    pl.lit(definition.signal_id).alias("signal_id"),
                    pl.col("value")
                    .rank(method="average", descending=True)
                    .over("decision_time")
                    .alias("rank"),
                    pl.len().over("decision_time").alias("cohort_size"),
                )
            )
            rows.append(materialized)
        if not rows:
            return pl.DataFrame()
        return pl.concat(rows, how="vertical").sort(
            ["signal_id", "decision_time", "rank", "market"]
        )


def v29_momentum_signal_registry() -> SignalRegistry:
    """Frozen M0 plus simple, pre-declared M1-M3 research hypotheses."""

    horizons = (15, 60, 240, 720)
    registry = SignalRegistry()
    registry.register(
        SignalDefinition(
            "m0_v29_score",
            "MOMENTUM_BASELINE",
            "v1",
            "v29_scored_liquidity_top20",
            ("score",),
            "1h/4h/24h plus 24h volatility ranks",
            horizons,
            "Exact frozen V2.9 post-penalty score; control signal.",
        ),
        lambda _: pl.col("score"),
    )
    registry.register(
        SignalDefinition(
            "m1_btc_relative_1h",
            "RELATIVE_MOMENTUM",
            "v1",
            "v29_scored_liquidity_top20",
            ("momentum_1h", "btc_momentum_1h"),
            "1h",
            horizons,
            "Asset 1h return minus point-in-time BTCKRW 1h return.",
        ),
        lambda _: pl.col("momentum_1h") - pl.col("btc_momentum_1h"),
    )
    registry.register(
        SignalDefinition(
            "m1_universe_relative_1h",
            "RELATIVE_MOMENTUM",
            "v1",
            "v29_scored_liquidity_top20",
            ("momentum_1h", "cohort_median_momentum_1h"),
            "1h",
            horizons,
            "Asset 1h return minus the same scored cohort's median 1h return.",
        ),
        lambda _: pl.col("momentum_1h") - pl.col("cohort_median_momentum_1h"),
    )
    registry.register(
        SignalDefinition(
            "m2_acceleration_1h_vs_4h",
            "MOMENTUM_ACCELERATION",
            "v1",
            "v29_scored_liquidity_top20",
            ("momentum_1h", "momentum_4h"),
            "1h versus 4h",
            horizons,
            "Recent 1h return minus one quarter of the 4h return; no fitted weights.",
        ),
        lambda _: pl.col("momentum_1h") - pl.col("momentum_4h") / 4.0,
    )
    registry.register(
        SignalDefinition(
            "m3_risk_adjusted_4h",
            "RISK_ADJUSTED_MOMENTUM",
            "v1",
            "v29_scored_liquidity_top20",
            ("momentum_4h", "volatility"),
            "4h return / 4h realized volatility",
            horizons,
            "4h return divided by 15m volatility scaled by sqrt(16).",
        ),
        lambda _: pl.when(pl.col("volatility") > 0)
        .then(pl.col("momentum_4h") / (pl.col("volatility") * 4.0))
        .otherwise(None),
    )
    return registry
