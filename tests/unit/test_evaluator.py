import math

import polars as pl

from investment.core.research.evaluator import FeatureEvaluator


def test_quantile_metrics_and_information_coefficients() -> None:
    frame = pl.DataFrame(
        {
            "signal": [1.0, 2.0, 3.0, 4.0],
            "forward_return_2d": [-0.2, -0.1, 0.1, 0.2],
            "forward_mdd_2d": [-0.3, -0.2, -0.1, -0.05],
        }
    )
    result = FeatureEvaluator().evaluate(
        frame, "signal", "forward_return_2d", quantiles=2, mdd_label="forward_mdd_2d"
    )
    assert result.count == 4
    assert math.isclose(result.pearson_ic or 0, 0.9899494936611665)
    assert result.spearman_rank_ic == 1.0
    assert result.quantiles[0].hit_rate == 0.0
    assert result.quantiles[1].hit_rate == 1.0
    assert result.quantiles[0].mean_forward_mdd == -0.25
