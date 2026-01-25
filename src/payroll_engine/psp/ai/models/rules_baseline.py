"""
Rules-Based Baseline Models for PSP AI Advisory.

Ship this first. It provides:
- Zero external dependencies
- Instant explainability
- Safe fallback behavior
- Great training data generator for ML models later

These rules encode domain expertise from:
- ACH return code specifications (via return_codes.py reference table)
- Common payroll failure patterns
- Industry best practices

IMPORTANT: Rules-based models are NOT probabilistic.
Confidence scores have CEILINGS to prevent overconfidence.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from payroll_engine.psp.ai.features import ReturnFeatures, FundingRiskFeatures
from payroll_engine.psp.ai.base import ContributingFactor
from payroll_engine.psp.ai.return_codes import (
    get_return_code_info,
    get_ambiguity_confidence_penalty,
    ReturnCodeInfo,
)


# =============================================================================
# Confidence Calibration
# =============================================================================
# Rules-based models should NOT act too sure.
# These ceilings prevent overconfidence.

# Maximum confidence for rules-based models (reserve >0.9 for trained ML)
RULES_MAX_CONFIDENCE = 0.85

# Confidence by number of independent indicators
SINGLE_INDICATOR_CEILING = 0.60
TWO_INDICATOR_CEILING = 0.70
MULTI_INDICATOR_CEILING = 0.80


def apply_confidence_ceiling(
    raw_confidence: float,
    num_indicators: int,
    ambiguity_penalty: float = 0.0,
    code_ceiling: float = 1.0,
) -> float:
    """
    Apply confidence ceilings to prevent overconfidence.

    Args:
        raw_confidence: The raw calculated confidence
        num_indicators: Number of independent indicators that agree
        ambiguity_penalty: Penalty from ambiguous return codes
        code_ceiling: Maximum from return code reference

    Returns:
        Calibrated confidence with ceilings applied
    """
    # Apply indicator-based ceiling
    if num_indicators <= 1:
        ceiling = SINGLE_INDICATOR_CEILING
    elif num_indicators == 2:
        ceiling = TWO_INDICATOR_CEILING
    else:
        ceiling = MULTI_INDICATOR_CEILING

    # Apply return code specific ceiling
    ceiling = min(ceiling, code_ceiling)

    # Apply global rules-based ceiling
    ceiling = min(ceiling, RULES_MAX_CONFIDENCE)

    # Apply ambiguity penalty
    ceiling = ceiling - ambiguity_penalty

    # Apply ceiling to raw confidence
    return min(raw_confidence, ceiling)


@dataclass
class RulesBaselineReturnModel:
    """
    Rules-based model for return root-cause analysis.

    This model uses deterministic rules based on:
    - Return code reference table (data-driven, not hard-coded)
    - Account age patterns
    - Historical return patterns
    - Provider reliability

    It produces explainable decisions with CALIBRATED confidence scores.

    Confidence ceilings:
    - Single indicator: max 0.60
    - Two indicators: max 0.70
    - Multiple indicators: max 0.80
    - High-ambiguity codes: additional penalty
    - Rules-based maximum: 0.85 (reserve >0.9 for ML)
    """
    model_name: str = "rules_baseline"
    model_version: str = "1.1.0"  # Bumped for confidence calibration

    def predict(
        self,
        features: ReturnFeatures,
        feature_completeness: float = 1.0,
    ) -> tuple[str, str, str, float, list[ContributingFactor], int]:
        """
        Predict error origin, liability party, and recovery path.

        Args:
            features: Extracted return features
            feature_completeness: How complete the features are (0-1)

        Returns:
            Tuple of (error_origin, liability_party, recovery_path,
                     confidence, factors, num_indicators)
        """
        factors: list[ContributingFactor] = []
        scores: dict[str, float] = {
            "employee": 0.0,
            "employer": 0.0,
            "provider": 0.0,
            "psp": 0.0,
            "unknown": 0.0,
        }
        num_indicators = 0  # Track independent signals

        # Get return code info from reference table
        return_code = features.return_code.upper()
        code_info = get_return_code_info(return_code)

        # =================================================================
        # Rule 1: Return code classification (from reference table)
        # =================================================================
        fault_prior = code_info.fault_prior

        if fault_prior == "employee":
            scores["employee"] += 0.5
        elif fault_prior == "employer":
            scores["employer"] += 0.5
        elif fault_prior == "provider":
            scores["provider"] += 0.5
        elif fault_prior == "psp":
            scores["psp"] += 0.5
        elif fault_prior == "mixed":
            scores["employee"] += 0.25
            scores["employer"] += 0.25
        else:  # unknown
            scores["unknown"] += 0.3

        factors.append(ContributingFactor(
            name="return_code",
            value=return_code,
            weight=0.5,
            direction="increases_risk",
            explanation=f"{code_info.description} (prior: {fault_prior}, ambiguity: {code_info.ambiguity})"
        ))
        num_indicators += 1

        # =================================================================
        # Rule 2: New account indicator (independent signal)
        # =================================================================
        if features.payee_is_new_account and fault_prior in ("employee", "mixed"):
            scores["employee"] += 0.25
            factors.append(ContributingFactor(
                name="new_account",
                value=f"{features.payee_account_age_days} days old",
                weight=0.25,
                direction="increases_risk",
                explanation="Newly added account increases likelihood of bad account info"
            ))
            num_indicators += 1

        # =================================================================
        # Rule 3: Repeat offender pattern (independent signal)
        # =================================================================
        if features.payee_prior_returns_30d >= 2:
            scores["employee"] += 0.2
            factors.append(ContributingFactor(
                name="repeat_returns",
                value=features.payee_prior_returns_30d,
                weight=0.2,
                direction="increases_risk",
                explanation=f"Payee has {features.payee_prior_returns_30d} returns in last 30 days"
            ))
            num_indicators += 1

        # =================================================================
        # Rule 4: High tenant return rate (weak signal)
        # =================================================================
        if features.tenant_return_rate_90d > 0.05:
            scores["employer"] += 0.1
            factors.append(ContributingFactor(
                name="high_tenant_return_rate",
                value=f"{features.tenant_return_rate_90d:.1%}",
                weight=0.1,
                direction="increases_risk",
                explanation="Tenant has elevated return rate suggesting process issues"
            ))
            # Not counted as independent - correlated with tenant

        # =================================================================
        # Rule 5: Provider reliability (weak signal)
        # =================================================================
        if features.provider_return_rate_90d > 0.03:
            scores["provider"] += 0.1
            factors.append(ContributingFactor(
                name="provider_return_rate",
                value=f"{features.provider_return_rate_90d:.1%}",
                weight=0.1,
                direction="increases_risk",
                explanation="Provider has elevated return rate"
            ))
            # Not counted as independent - correlated with provider

        # =================================================================
        # Rule 6: Funding blocks correlation (weak signal)
        # =================================================================
        if features.tenant_funding_blocks_90d > 2:
            scores["employer"] += 0.1
            factors.append(ContributingFactor(
                name="funding_blocks",
                value=features.tenant_funding_blocks_90d,
                weight=0.1,
                direction="increases_risk",
                explanation=f"Tenant has {features.tenant_funding_blocks_90d} funding blocks"
            ))

        # =================================================================
        # Determine winner
        # =================================================================
        total = sum(scores.values()) or 1.0
        normalized = {k: v / total for k, v in scores.items()}
        error_origin = max(normalized, key=normalized.get)  # type: ignore
        raw_confidence = normalized[error_origin]

        # =================================================================
        # Apply confidence calibration
        # =================================================================
        ambiguity_penalty = get_ambiguity_confidence_penalty(code_info.ambiguity)

        # Degrade confidence for incomplete features
        completeness_penalty = (1.0 - feature_completeness) * 0.2

        confidence = apply_confidence_ceiling(
            raw_confidence=raw_confidence,
            num_indicators=num_indicators,
            ambiguity_penalty=ambiguity_penalty + completeness_penalty,
            code_ceiling=code_info.confidence_ceiling,
        )

        # Add calibration factor to explanation
        if ambiguity_penalty > 0:
            factors.append(ContributingFactor(
                name="ambiguity_adjustment",
                value=f"-{ambiguity_penalty:.0%}",
                weight=ambiguity_penalty,
                direction="decreases_risk",
                explanation=f"Return code has {code_info.ambiguity} ambiguity - confidence reduced"
            ))

        if feature_completeness < 1.0:
            factors.append(ContributingFactor(
                name="incomplete_features",
                value=f"{feature_completeness:.0%} complete",
                weight=completeness_penalty,
                direction="decreases_risk",
                explanation="Some features unavailable - confidence reduced"
            ))

        # =================================================================
        # Map to liability and recovery
        # =================================================================
        liability_party = self._map_liability(error_origin, return_code, code_info)
        recovery_path = self._determine_recovery_path(
            error_origin, liability_party, features.amount, confidence, code_info
        )

        return error_origin, liability_party, recovery_path, confidence, factors, num_indicators

    def _map_liability(
        self,
        error_origin: str,
        return_code: str,
        code_info: ReturnCodeInfo,
    ) -> str:
        """Map error origin to liable party."""
        # High-ambiguity codes should suggest investigation
        if code_info.ambiguity == "high":
            return "unknown"

        if error_origin == "employee":
            # Employee fault usually means employer liability
            # Unless it's a dispute code
            if return_code in {"R05", "R10", "R23", "R29"}:
                return "unknown"  # Needs investigation
            return "employer"

        if error_origin == "employer":
            return "employer"

        if error_origin == "provider":
            return "provider"

        if error_origin == "psp":
            return "psp"

        return "unknown"

    def _determine_recovery_path(
        self,
        error_origin: str,
        liability_party: str,
        amount: Decimal,
        confidence: float,
        code_info: ReturnCodeInfo,
    ) -> str:
        """Determine recovery path based on liability."""
        # High-ambiguity codes always need investigation
        if code_info.ambiguity == "high":
            return "investigate"

        # Low confidence = investigate
        if confidence < 0.5:
            return "investigate"

        # High-value returns need investigation
        if amount > Decimal("10000"):
            return "investigate"

        # Unknown liability = investigate
        if liability_party == "unknown":
            return "investigate"

        # Standard paths
        if liability_party == "employer":
            return "offset"
        if liability_party == "employee":
            return "clawback"
        if liability_party in {"provider", "psp"}:
            return "write_off"

        return "investigate"


@dataclass
class RulesBaselineFundingRiskModel:
    """
    Rules-based model for funding risk prediction.

    This model uses deterministic rules based on:
    - Payroll spike detection
    - Historical funding block patterns
    - Settlement delay risks
    - Available balance headroom

    Output clearly separates:
    - risk_score: Objective risk measurement
    - risk_band: Risk category
    - contributing_factors: What's driving risk
    - suggestions: OPTIONAL actions (clearly marked as suggestions)
    """
    model_name: str = "rules_baseline"
    model_version: str = "1.1.0"

    def predict(
        self,
        features: FundingRiskFeatures,
        feature_completeness: float = 1.0,
    ) -> tuple[float, str, Decimal, list[ContributingFactor], list[str], int]:
        """
        Predict funding risk.

        Args:
            features: Extracted funding risk features
            feature_completeness: How complete the features are (0-1)

        Returns:
            Tuple of (risk_score, risk_band, suggested_buffer,
                     factors, suggestions, num_indicators)

        Note: suggestions are CLEARLY OPTIONAL and do not auto-apply.
        """
        factors: list[ContributingFactor] = []
        suggestions: list[str] = []  # Separate from risk assessment
        risk_score = 0.0
        num_indicators = 0

        # =================================================================
        # Rule 1: Payroll spike detection
        # =================================================================
        if features.spike_ratio > 2.0:
            risk_score += 0.3
            factors.append(ContributingFactor(
                name="payroll_spike",
                value=f"{features.spike_ratio:.1f}x average",
                weight=0.3,
                direction="increases_risk",
                explanation=f"Payroll is {features.spike_ratio:.1f}x the 90-day average"
            ))
            suggestions.append("Consider verifying this payroll amount is correct")
            num_indicators += 1
        elif features.spike_ratio > 1.5:
            risk_score += 0.15
            factors.append(ContributingFactor(
                name="payroll_spike",
                value=f"{features.spike_ratio:.1f}x average",
                weight=0.15,
                direction="increases_risk",
                explanation=f"Payroll is {features.spike_ratio:.1f}x the 90-day average"
            ))
            num_indicators += 1

        # =================================================================
        # Rule 2: Recent funding blocks
        # =================================================================
        if features.funding_blocks_30d > 0:
            risk_score += 0.25
            factors.append(ContributingFactor(
                name="recent_funding_blocks",
                value=features.funding_blocks_30d,
                weight=0.25,
                direction="increases_risk",
                explanation=f"Had {features.funding_blocks_30d} funding block(s) in last 30 days"
            ))
            suggestions.append("Consider increasing prefund buffer")
            num_indicators += 1

        if features.historical_block_rate > 0.1:
            risk_score += 0.15
            factors.append(ContributingFactor(
                name="historical_block_rate",
                value=f"{features.historical_block_rate:.0%}",
                weight=0.15,
                direction="increases_risk",
                explanation=f"Historical block rate is {features.historical_block_rate:.0%}"
            ))

        # =================================================================
        # Rule 3: Settlement delay risk
        # =================================================================
        if features.pending_settlements_amount > features.payroll_amount * Decimal("0.5"):
            risk_score += 0.2
            factors.append(ContributingFactor(
                name="pending_settlements",
                value=f"${features.pending_settlements_amount:,.2f}",
                weight=0.2,
                direction="increases_risk",
                explanation="Large pending settlements may not clear in time"
            ))
            suggestions.append("Consider waiting for pending settlements to clear")
            num_indicators += 1

        if features.p95_settlement_delay_days > 3:
            risk_score += 0.1
            factors.append(ContributingFactor(
                name="settlement_delay",
                value=f"{features.p95_settlement_delay_days:.1f} days (p95)",
                weight=0.1,
                direction="increases_risk",
                explanation="Settlement delays occasionally exceed 3 days"
            ))

        # =================================================================
        # Rule 4: Balance headroom
        # =================================================================
        if features.funding_headroom < Decimal("0"):
            risk_score += 0.35
            factors.append(ContributingFactor(
                name="negative_headroom",
                value=f"${features.funding_headroom:,.2f}",
                weight=0.35,
                direction="increases_risk",
                explanation="Insufficient funds even with expected settlements"
            ))
            suggestions.append("Consider requesting additional funding before commit")
            num_indicators += 1
        elif features.funding_headroom < features.payroll_amount * Decimal("0.1"):
            risk_score += 0.15
            factors.append(ContributingFactor(
                name="low_headroom",
                value=f"${features.funding_headroom:,.2f}",
                weight=0.15,
                direction="increases_risk",
                explanation="Funding headroom is less than 10% of payroll"
            ))
            num_indicators += 1

        # =================================================================
        # Rule 5: Funding model consideration
        # =================================================================
        if features.funding_model == "just_in_time" and not features.has_backup_funding:
            risk_score += 0.1
            factors.append(ContributingFactor(
                name="funding_model_risk",
                value="JIT without backup",
                weight=0.1,
                direction="increases_risk",
                explanation="Just-in-time funding without backup increases risk"
            ))
            suggestions.append("Consider switching to prefunded model for this batch")

        # =================================================================
        # Apply feature completeness penalty
        # =================================================================
        if feature_completeness < 1.0:
            # Incomplete features = assume some risk we can't see
            risk_score += 0.1 * (1.0 - feature_completeness)
            factors.append(ContributingFactor(
                name="incomplete_features",
                value=f"{feature_completeness:.0%} complete",
                weight=0.1,
                direction="increases_risk",
                explanation="Some features unavailable - risk may be understated"
            ))

        # =================================================================
        # Cap risk score at 1.0
        # =================================================================
        risk_score = min(risk_score, 1.0)

        # =================================================================
        # Determine risk band
        # =================================================================
        if risk_score >= 0.7:
            risk_band = "critical"
            suggestions.insert(0, "CRITICAL: Manual review strongly recommended")
        elif risk_score >= 0.4:
            risk_band = "high"
            suggestions.insert(0, "Consider delaying until risk factors resolved")
        elif risk_score >= 0.2:
            risk_band = "medium"
        else:
            risk_band = "low"

        # =================================================================
        # Calculate suggested reserve buffer
        # =================================================================
        buffer_multiplier = 1.0 + risk_score
        suggested_buffer = features.payroll_amount * Decimal(str(0.1 * buffer_multiplier))

        if features.funding_blocks_30d > 0:
            suggested_buffer = max(
                suggested_buffer,
                features.payroll_amount * Decimal("0.2")
            )

        return risk_score, risk_band, suggested_buffer, factors, suggestions, num_indicators
