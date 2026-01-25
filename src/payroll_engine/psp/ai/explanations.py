"""
Explanation utilities for PSP AI Advisory.

All AI advisories must be explainable. This module provides
utilities for generating human-readable explanations from
model outputs and contributing factors.
"""

from dataclasses import dataclass
from typing import Sequence

from payroll_engine.psp.ai.base import (
    Advisory,
    ReturnAdvisory,
    FundingRiskAdvisory,
    ContributingFactor,
)


@dataclass(frozen=True)
class ExplanationFormat:
    """Configuration for explanation formatting."""
    include_confidence: bool = True
    include_factors: bool = True
    max_factors: int = 3
    include_recommendations: bool = True
    include_context_notes: bool = True
    verbose: bool = False


def format_advisory_explanation(
    advisory: Advisory,
    format_config: ExplanationFormat = ExplanationFormat(),
) -> str:
    """
    Format an advisory into a human-readable explanation.

    Args:
        advisory: The advisory to explain
        format_config: Formatting options

    Returns:
        Formatted explanation string
    """
    if isinstance(advisory, ReturnAdvisory):
        return format_return_explanation(advisory, format_config)
    elif isinstance(advisory, FundingRiskAdvisory):
        return format_funding_risk_explanation(advisory, format_config)
    else:
        return advisory.explanation


def format_return_explanation(
    advisory: ReturnAdvisory,
    format_config: ExplanationFormat = ExplanationFormat(),
) -> str:
    """Format return advisory explanation."""
    lines = []

    # Summary
    confidence_pct = f"{advisory.confidence:.0%}"
    lines.append(
        f"Return Analysis: {advisory.return_code} on payment {advisory.payment_id}"
    )
    lines.append("")

    # Verdict
    lines.append(f"Suggested error origin: {advisory.suggested_error_origin.upper()}")
    lines.append(f"Suggested liable party: {advisory.suggested_liability_party.upper()}")
    lines.append(f"Suggested recovery path: {advisory.suggested_recovery_path.upper()}")

    if format_config.include_confidence:
        lines.append(f"Confidence: {confidence_pct}")

    lines.append("")

    # Factors
    if format_config.include_factors and advisory.contributing_factors:
        lines.append("Contributing Factors:")
        factors = sorted(
            advisory.contributing_factors,
            key=lambda f: f.weight,
            reverse=True
        )[:format_config.max_factors]

        for factor in factors:
            weight_pct = f"{factor.weight:.0%}"
            lines.append(f"  [{weight_pct}] {factor.name}: {factor.explanation}")

        lines.append("")

    # Model info
    if format_config.verbose:
        lines.append(f"Model: {advisory.model_name} v{advisory.model_version}")
        lines.append(f"Feature schema: {advisory.feature_schema_hash}")
        lines.append(f"Generated: {advisory.generated_at.isoformat()}")

    return "\n".join(lines)


def format_funding_risk_explanation(
    advisory: FundingRiskAdvisory,
    format_config: ExplanationFormat = ExplanationFormat(),
) -> str:
    """Format funding risk advisory explanation."""
    lines = []

    # Summary
    batch_info = f"batch {advisory.payroll_batch_id}" if advisory.payroll_batch_id else "general assessment"
    lines.append(f"Funding Risk Analysis: {batch_info}")
    lines.append("")

    # Verdict
    risk_emoji = {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🟢",
    }.get(advisory.risk_band, "⚪")

    lines.append(f"Risk Level: {risk_emoji} {advisory.risk_band.upper()}")
    lines.append(f"Risk Score: {advisory.risk_score:.0%}")
    lines.append(f"Predicted Amount: ${advisory.predicted_amount:,.2f}")
    lines.append(f"Suggested Buffer: ${advisory.suggested_reserve_buffer:,.2f}")
    lines.append("")

    # Factors
    if format_config.include_factors and advisory.contributing_factors:
        lines.append("Risk Factors:")
        factors = sorted(
            advisory.contributing_factors,
            key=lambda f: f.weight,
            reverse=True
        )[:format_config.max_factors]

        for factor in factors:
            weight_pct = f"{factor.weight:.0%}"
            direction = "↑" if factor.direction == "increases_risk" else "↓"
            lines.append(f"  {direction} [{weight_pct}] {factor.explanation}")

        lines.append("")

    # Model info
    if format_config.verbose:
        lines.append(f"Model: {advisory.model_name} v{advisory.model_version}")
        lines.append(f"Feature schema: {advisory.feature_schema_hash}")
        lines.append(f"Generated: {advisory.generated_at.isoformat()}")

    return "\n".join(lines)


def summarize_factors(
    factors: Sequence[ContributingFactor],
    max_factors: int = 3,
) -> str:
    """
    Create a brief summary of contributing factors.

    Args:
        factors: List of factors
        max_factors: Maximum factors to include

    Returns:
        Brief summary string
    """
    if not factors:
        return "No significant factors identified."

    sorted_factors = sorted(factors, key=lambda f: f.weight, reverse=True)
    top_factors = sorted_factors[:max_factors]

    summaries = [f.name for f in top_factors]
    return f"Key factors: {', '.join(summaries)}"


def explain_confidence(confidence: float) -> str:
    """
    Explain what a confidence score means.

    Args:
        confidence: Confidence score (0.0 to 1.0)

    Returns:
        Human-readable explanation
    """
    if confidence >= 0.95:
        return "Very high confidence - recommendation is strongly supported by evidence"
    elif confidence >= 0.85:
        return "High confidence - recommendation is well-supported"
    elif confidence >= 0.70:
        return "Moderate confidence - recommendation is likely but should be reviewed"
    elif confidence >= 0.50:
        return "Low confidence - recommendation is uncertain, manual review recommended"
    else:
        return "Very low confidence - insufficient evidence, manual investigation required"


def generate_audit_trail(advisory: Advisory) -> dict:
    """
    Generate an audit trail entry for an advisory.

    This can be stored alongside the advisory event for compliance.

    Args:
        advisory: The advisory

    Returns:
        Audit trail dictionary
    """
    return {
        "advisory_id": str(advisory.advisory_id),
        "advisory_type": type(advisory).__name__,
        "tenant_id": str(advisory.tenant_id),
        "generated_at": advisory.generated_at.isoformat(),
        "model": {
            "name": advisory.model_name,
            "version": advisory.model_version,
            "feature_schema_hash": advisory.feature_schema_hash,
        },
        "decision": {
            "confidence": advisory.confidence,
            "confidence_explanation": explain_confidence(advisory.confidence),
        },
        "factors": [
            {
                "name": f.name,
                "value": str(f.value),
                "weight": f.weight,
                "direction": f.direction,
                "explanation": f.explanation,
            }
            for f in advisory.contributing_factors
        ],
        "explanation": advisory.explanation,
    }
