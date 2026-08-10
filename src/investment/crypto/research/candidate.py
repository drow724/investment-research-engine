"""Replaceable champion/challenger evaluation policy."""

from dataclasses import dataclass

from investment.crypto.backtest.models import PerformanceMetrics
from investment.crypto.domain.research import CandidateEvaluation


@dataclass(frozen=True, slots=True)
class ConservativeCandidateEvaluationPolicy:
    version: str = "candidate-policy-v1"

    def evaluate(
        self,
        champion: PerformanceMetrics,
        challenger: PerformanceMetrics,
        *,
        validation_successful: bool,
    ) -> CandidateEvaluation:
        reasons = []
        if not validation_successful:
            reasons.append("VALIDATION_FAILED")
        if champion.sharpe_ratio is not None and (
            challenger.sharpe_ratio is None or challenger.sharpe_ratio < champion.sharpe_ratio
        ):
            reasons.append("SHARPE_BELOW_CHAMPION")
        if challenger.maximum_drawdown < champion.maximum_drawdown:
            reasons.append("DRAWDOWN_WORSE_THAN_CHAMPION")
        if challenger.fee_adjusted_return <= 0:
            reasons.append("FEE_ADJUSTED_RETURN_NOT_POSITIVE")
        return CandidateEvaluation(not reasons, self.version, tuple(reasons))
