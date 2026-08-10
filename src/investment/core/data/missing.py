"""Explicit missing-data behavior; absence is never silently converted to zero."""

from enum import StrEnum

import polars as pl


class MissingDataPolicy(StrEnum):
    NO_FILL = "NO_FILL"
    DROP = "DROP"
    FORWARD_FILL = "FORWARD_FILL"
    ZERO = "ZERO"


def apply_missing_policy(
    frame: pl.DataFrame, columns: list[str], policy: MissingDataPolicy
) -> pl.DataFrame:
    missing = set(columns).difference(frame.columns)
    if missing:
        raise ValueError(f"missing columns for missing-data policy: {sorted(missing)}")
    if policy is MissingDataPolicy.NO_FILL:
        return frame.clone()
    if policy is MissingDataPolicy.DROP:
        return frame.drop_nulls(subset=columns)
    if policy is MissingDataPolicy.FORWARD_FILL:
        return frame.with_columns(pl.col(columns).forward_fill())
    return frame.with_columns(pl.col(columns).fill_null(0))
