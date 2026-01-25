"""
Tenant Risk Scoring (SOC2-friendly).

Aggregates signals into a tenant risk profile:
- Return rate trend
- Reversal rate trend
- Funding block frequency
- Settlement mismatch frequency
- Suspicious patterns (reservation churn, status regressions)

Outputs:
- TenantRiskProfileGenerated event
- psp tenant-risk --tenant <id>

This becomes your "ops dashboard feed" without building a dashboard.

CRITICAL: This is read-only analysis. It NEVER modifies state.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4


class RiskLevel(Enum):
    """Overall tenant risk level."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TrendDirection(Enum):
    """Direction of a metric trend."""
    IMPROVING = "improving"
    STABLE = "stable"
    DEGRADING = "degrading"
    CRITICAL_DEGRADATION = "critical_degradation"


@dataclass(frozen=True)
class RiskSignal:
    """
    A single risk signal contributing to tenant risk.

    Each signal has a weight and contributes to the overall score.
    """
    name: str
    value: Any
    weight: float  # 0-1, contribution to risk score
    category: str  # return, funding, settlement, pattern
    description: str
    trend: Optional[TrendDirection] = None
    threshold_exceeded: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "value": self.value if not isinstance(self.value, Decimal) else str(self.value),
            "weight": self.weight,
            "category": self.category,
            "description": self.description,
            "trend": self.trend.value if self.trend else None,
            "threshold_exceeded": self.threshold_exceeded,
        }


@dataclass
class TenantMetrics:
    """
    Raw metrics for tenant risk calculation.

    This is the input to the risk profiler, typically
    extracted from the event store.
    """
    tenant_id: UUID
    evaluation_time: datetime

    # Return metrics
    return_count_30d: int = 0
    return_count_90d: int = 0
    return_amount_30d: Decimal = Decimal("0")
    return_amount_90d: Decimal = Decimal("0")
    payment_count_30d: int = 0
    payment_count_90d: int = 0
    return_rate_30d: float = 0.0
    return_rate_90d: float = 0.0
    return_rate_trend: float = 0.0  # Positive = increasing (bad)

    # Reversal metrics
    reversal_count_30d: int = 0
    reversal_count_90d: int = 0
    reversal_rate_30d: float = 0.0
    reversal_rate_90d: float = 0.0
    reversal_rate_trend: float = 0.0

    # Funding metrics
    funding_block_count_30d: int = 0
    funding_block_count_90d: int = 0
    funding_block_rate_30d: float = 0.0
    payroll_count_30d: int = 0

    # Settlement metrics
    settlement_mismatch_count_30d: int = 0
    settlement_mismatch_count_90d: int = 0
    avg_settlement_delay_30d: float = 0.0
    p95_settlement_delay_30d: float = 0.0

    # Pattern metrics (suspicious behaviors)
    reservation_churn_count_30d: int = 0  # Reservations cancelled then recreated
    status_regression_count_30d: int = 0  # Status went backwards
    late_modification_count_30d: int = 0  # Modifications after commit
    duplicate_payment_attempts_30d: int = 0

    # Volume context
    total_payment_volume_30d: Decimal = Decimal("0")
    total_payment_volume_90d: Decimal = Decimal("0")
    avg_payment_amount: Decimal = Decimal("0")

    # Account age
    tenant_age_days: int = 0
    is_new_tenant: bool = False  # < 30 days


@dataclass
class TenantRiskProfile:
    """
    Complete tenant risk profile.

    This is the output of tenant risk analysis, suitable
    for ops dashboards and alerting.
    """
    profile_id: UUID
    tenant_id: UUID
    generated_at: datetime
    evaluation_period_days: int

    # Overall assessment
    risk_level: RiskLevel
    risk_score: float  # 0-1
    risk_trend: TrendDirection

    # Component scores (0-1 each)
    return_risk_score: float = 0.0
    reversal_risk_score: float = 0.0
    funding_risk_score: float = 0.0
    settlement_risk_score: float = 0.0
    pattern_risk_score: float = 0.0

    # Contributing signals
    signals: list[RiskSignal] = field(default_factory=list)

    # Recommendations
    recommendations: list[str] = field(default_factory=list)

    # Flags
    requires_review: bool = False
    requires_immediate_action: bool = False

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "profile_id": str(self.profile_id),
            "tenant_id": str(self.tenant_id),
            "generated_at": self.generated_at.isoformat(),
            "evaluation_period_days": self.evaluation_period_days,
            "assessment": {
                "risk_level": self.risk_level.value,
                "risk_score": round(self.risk_score, 4),
                "risk_trend": self.risk_trend.value,
            },
            "component_scores": {
                "return_risk": round(self.return_risk_score, 4),
                "reversal_risk": round(self.reversal_risk_score, 4),
                "funding_risk": round(self.funding_risk_score, 4),
                "settlement_risk": round(self.settlement_risk_score, 4),
                "pattern_risk": round(self.pattern_risk_score, 4),
            },
            "signals": [s.to_dict() for s in self.signals],
            "recommendations": self.recommendations,
            "flags": {
                "requires_review": self.requires_review,
                "requires_immediate_action": self.requires_immediate_action,
            },
        }

    def to_markdown(self) -> str:
        """Generate markdown summary."""
        emoji = {
            RiskLevel.LOW: "🟢",
            RiskLevel.MEDIUM: "🟡",
            RiskLevel.HIGH: "🟠",
            RiskLevel.CRITICAL: "🔴",
        }

        lines = [
            f"# Tenant Risk Profile",
            f"",
            f"**Tenant:** {self.tenant_id}",
            f"**Generated:** {self.generated_at.isoformat()}",
            f"**Period:** Last {self.evaluation_period_days} days",
            f"",
            f"## Overall Assessment",
            f"",
            f"{emoji[self.risk_level]} **Risk Level:** {self.risk_level.value.upper()}",
            f"",
            f"**Risk Score:** {self.risk_score:.0%}",
            f"",
            f"**Trend:** {self.risk_trend.value}",
            f"",
            f"## Component Scores",
            f"",
            f"| Component | Score | Level |",
            f"|-----------|-------|-------|",
            f"| Return Risk | {self.return_risk_score:.0%} | {self._score_level(self.return_risk_score)} |",
            f"| Reversal Risk | {self.reversal_risk_score:.0%} | {self._score_level(self.reversal_risk_score)} |",
            f"| Funding Risk | {self.funding_risk_score:.0%} | {self._score_level(self.funding_risk_score)} |",
            f"| Settlement Risk | {self.settlement_risk_score:.0%} | {self._score_level(self.settlement_risk_score)} |",
            f"| Pattern Risk | {self.pattern_risk_score:.0%} | {self._score_level(self.pattern_risk_score)} |",
            f"",
        ]

        if self.requires_immediate_action:
            lines.extend([
                f"## ⚠️ IMMEDIATE ACTION REQUIRED",
                f"",
                f"This tenant requires immediate review due to critical risk indicators.",
                f"",
            ])

        if self.signals:
            # Group signals by threshold exceeded
            critical_signals = [s for s in self.signals if s.threshold_exceeded]
            other_signals = [s for s in self.signals if not s.threshold_exceeded]

            if critical_signals:
                lines.extend([
                    f"## Critical Signals",
                    f"",
                ])
                for s in critical_signals:
                    trend_str = f" ({s.trend.value})" if s.trend else ""
                    lines.append(f"- **{s.name}**: {s.value}{trend_str} - {s.description}")
                lines.append("")

            if other_signals:
                lines.extend([
                    f"## Other Signals",
                    f"",
                ])
                for s in other_signals[:5]:  # Limit to top 5
                    lines.append(f"- {s.name}: {s.value} - {s.description}")
                lines.append("")

        if self.recommendations:
            lines.extend([
                f"## Recommendations",
                f"",
            ])
            for r in self.recommendations:
                lines.append(f"- {r}")
            lines.append("")

        return "\n".join(lines)

    def _score_level(self, score: float) -> str:
        """Convert score to level string."""
        if score >= 0.7:
            return "🔴 High"
        elif score >= 0.4:
            return "🟡 Medium"
        else:
            return "🟢 Low"


class TenantRiskProfiler:
    """
    Generate tenant risk profiles from metrics.

    This is a PURE FUNCTION - takes metrics and produces
    a risk profile without modifying any state.
    """

    def __init__(
        self,
        # Return thresholds
        return_rate_warning: float = 0.02,
        return_rate_critical: float = 0.05,
        # Reversal thresholds
        reversal_rate_warning: float = 0.01,
        reversal_rate_critical: float = 0.03,
        # Funding thresholds
        funding_block_warning: int = 1,
        funding_block_critical: int = 3,
        # Settlement thresholds
        settlement_mismatch_warning: int = 2,
        settlement_mismatch_critical: int = 5,
        settlement_delay_warning: float = 3.0,
        # Pattern thresholds
        suspicious_pattern_warning: int = 2,
        suspicious_pattern_critical: int = 5,
    ):
        self.return_rate_warning = return_rate_warning
        self.return_rate_critical = return_rate_critical
        self.reversal_rate_warning = reversal_rate_warning
        self.reversal_rate_critical = reversal_rate_critical
        self.funding_block_warning = funding_block_warning
        self.funding_block_critical = funding_block_critical
        self.settlement_mismatch_warning = settlement_mismatch_warning
        self.settlement_mismatch_critical = settlement_mismatch_critical
        self.settlement_delay_warning = settlement_delay_warning
        self.suspicious_pattern_warning = suspicious_pattern_warning
        self.suspicious_pattern_critical = suspicious_pattern_critical

    def profile(
        self,
        metrics: TenantMetrics,
        evaluation_period_days: int = 30,
    ) -> TenantRiskProfile:
        """
        Generate a risk profile from tenant metrics.

        Args:
            metrics: Raw tenant metrics
            evaluation_period_days: Days of data being evaluated

        Returns:
            Complete TenantRiskProfile
        """
        signals: list[RiskSignal] = []
        recommendations: list[str] = []

        # Calculate component scores
        return_score, return_signals = self._score_returns(metrics)
        reversal_score, reversal_signals = self._score_reversals(metrics)
        funding_score, funding_signals = self._score_funding(metrics)
        settlement_score, settlement_signals = self._score_settlement(metrics)
        pattern_score, pattern_signals = self._score_patterns(metrics)

        signals.extend(return_signals)
        signals.extend(reversal_signals)
        signals.extend(funding_signals)
        signals.extend(settlement_signals)
        signals.extend(pattern_signals)

        # Calculate overall score (weighted average)
        weights = {
            "return": 0.30,
            "reversal": 0.15,
            "funding": 0.25,
            "settlement": 0.15,
            "pattern": 0.15,
        }

        overall_score = (
            return_score * weights["return"] +
            reversal_score * weights["reversal"] +
            funding_score * weights["funding"] +
            settlement_score * weights["settlement"] +
            pattern_score * weights["pattern"]
        )

        # Determine risk level
        if overall_score >= 0.7:
            risk_level = RiskLevel.CRITICAL
        elif overall_score >= 0.5:
            risk_level = RiskLevel.HIGH
        elif overall_score >= 0.25:
            risk_level = RiskLevel.MEDIUM
        else:
            risk_level = RiskLevel.LOW

        # Determine trend
        trend = self._determine_trend(metrics)

        # Generate recommendations
        recommendations = self._generate_recommendations(
            metrics, return_score, reversal_score, funding_score,
            settlement_score, pattern_score
        )

        # Determine flags
        requires_review = risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL)
        requires_immediate_action = (
            risk_level == RiskLevel.CRITICAL or
            pattern_score >= 0.7 or
            any(s.threshold_exceeded and s.category == "pattern" for s in signals)
        )

        return TenantRiskProfile(
            profile_id=uuid4(),
            tenant_id=metrics.tenant_id,
            generated_at=datetime.utcnow(),
            evaluation_period_days=evaluation_period_days,
            risk_level=risk_level,
            risk_score=overall_score,
            risk_trend=trend,
            return_risk_score=return_score,
            reversal_risk_score=reversal_score,
            funding_risk_score=funding_score,
            settlement_risk_score=settlement_score,
            pattern_risk_score=pattern_score,
            signals=signals,
            recommendations=recommendations,
            requires_review=requires_review,
            requires_immediate_action=requires_immediate_action,
        )

    def _score_returns(
        self,
        metrics: TenantMetrics,
    ) -> tuple[float, list[RiskSignal]]:
        """Score return-related risk."""
        signals = []
        score = 0.0

        # Return rate
        if metrics.return_rate_30d >= self.return_rate_critical:
            score += 0.6
            signals.append(RiskSignal(
                name="return_rate",
                value=f"{metrics.return_rate_30d:.1%}",
                weight=0.6,
                category="return",
                description=f"Return rate exceeds critical threshold ({self.return_rate_critical:.1%})",
                threshold_exceeded=True,
            ))
        elif metrics.return_rate_30d >= self.return_rate_warning:
            score += 0.3
            signals.append(RiskSignal(
                name="return_rate",
                value=f"{metrics.return_rate_30d:.1%}",
                weight=0.3,
                category="return",
                description=f"Return rate exceeds warning threshold ({self.return_rate_warning:.1%})",
                threshold_exceeded=False,
            ))

        # Return rate trend
        if metrics.return_rate_trend > 0.5:  # More than 50% increase
            score += 0.3
            signals.append(RiskSignal(
                name="return_rate_trend",
                value=f"+{metrics.return_rate_trend:.0%}",
                weight=0.3,
                category="return",
                description="Return rate significantly increasing",
                trend=TrendDirection.CRITICAL_DEGRADATION,
                threshold_exceeded=True,
            ))
        elif metrics.return_rate_trend > 0.2:
            score += 0.15
            signals.append(RiskSignal(
                name="return_rate_trend",
                value=f"+{metrics.return_rate_trend:.0%}",
                weight=0.15,
                category="return",
                description="Return rate increasing",
                trend=TrendDirection.DEGRADING,
                threshold_exceeded=False,
            ))

        # Return count (absolute)
        if metrics.return_count_30d >= 10:
            score += 0.1
            signals.append(RiskSignal(
                name="return_count",
                value=metrics.return_count_30d,
                weight=0.1,
                category="return",
                description=f"{metrics.return_count_30d} returns in 30 days",
                threshold_exceeded=False,
            ))

        return min(score, 1.0), signals

    def _score_reversals(
        self,
        metrics: TenantMetrics,
    ) -> tuple[float, list[RiskSignal]]:
        """Score reversal-related risk."""
        signals = []
        score = 0.0

        if metrics.reversal_rate_30d >= self.reversal_rate_critical:
            score += 0.7
            signals.append(RiskSignal(
                name="reversal_rate",
                value=f"{metrics.reversal_rate_30d:.1%}",
                weight=0.7,
                category="reversal",
                description=f"Reversal rate exceeds critical threshold ({self.reversal_rate_critical:.1%})",
                threshold_exceeded=True,
            ))
        elif metrics.reversal_rate_30d >= self.reversal_rate_warning:
            score += 0.4
            signals.append(RiskSignal(
                name="reversal_rate",
                value=f"{metrics.reversal_rate_30d:.1%}",
                weight=0.4,
                category="reversal",
                description=f"Reversal rate elevated",
                threshold_exceeded=False,
            ))

        if metrics.reversal_rate_trend > 0.3:
            score += 0.3
            signals.append(RiskSignal(
                name="reversal_trend",
                value=f"+{metrics.reversal_rate_trend:.0%}",
                weight=0.3,
                category="reversal",
                description="Reversal rate increasing",
                trend=TrendDirection.DEGRADING,
                threshold_exceeded=False,
            ))

        return min(score, 1.0), signals

    def _score_funding(
        self,
        metrics: TenantMetrics,
    ) -> tuple[float, list[RiskSignal]]:
        """Score funding-related risk."""
        signals = []
        score = 0.0

        if metrics.funding_block_count_30d >= self.funding_block_critical:
            score += 0.7
            signals.append(RiskSignal(
                name="funding_blocks",
                value=metrics.funding_block_count_30d,
                weight=0.7,
                category="funding",
                description=f"Multiple funding blocks ({metrics.funding_block_count_30d}) in 30 days",
                threshold_exceeded=True,
            ))
        elif metrics.funding_block_count_30d >= self.funding_block_warning:
            score += 0.4
            signals.append(RiskSignal(
                name="funding_blocks",
                value=metrics.funding_block_count_30d,
                weight=0.4,
                category="funding",
                description=f"Recent funding block(s)",
                threshold_exceeded=False,
            ))

        # Funding block rate
        if metrics.payroll_count_30d > 0:
            block_rate = metrics.funding_block_count_30d / metrics.payroll_count_30d
            if block_rate >= 0.2:
                score += 0.3
                signals.append(RiskSignal(
                    name="funding_block_rate",
                    value=f"{block_rate:.0%}",
                    weight=0.3,
                    category="funding",
                    description="High percentage of payrolls blocked",
                    threshold_exceeded=True,
                ))

        return min(score, 1.0), signals

    def _score_settlement(
        self,
        metrics: TenantMetrics,
    ) -> tuple[float, list[RiskSignal]]:
        """Score settlement-related risk."""
        signals = []
        score = 0.0

        if metrics.settlement_mismatch_count_30d >= self.settlement_mismatch_critical:
            score += 0.6
            signals.append(RiskSignal(
                name="settlement_mismatches",
                value=metrics.settlement_mismatch_count_30d,
                weight=0.6,
                category="settlement",
                description="Multiple settlement mismatches",
                threshold_exceeded=True,
            ))
        elif metrics.settlement_mismatch_count_30d >= self.settlement_mismatch_warning:
            score += 0.3
            signals.append(RiskSignal(
                name="settlement_mismatches",
                value=metrics.settlement_mismatch_count_30d,
                weight=0.3,
                category="settlement",
                description="Recent settlement mismatches",
                threshold_exceeded=False,
            ))

        if metrics.p95_settlement_delay_30d > self.settlement_delay_warning:
            score += 0.4
            signals.append(RiskSignal(
                name="settlement_delay",
                value=f"{metrics.p95_settlement_delay_30d:.1f} days (p95)",
                weight=0.4,
                category="settlement",
                description="High settlement delays",
                threshold_exceeded=True,
            ))

        return min(score, 1.0), signals

    def _score_patterns(
        self,
        metrics: TenantMetrics,
    ) -> tuple[float, list[RiskSignal]]:
        """Score suspicious pattern risk."""
        signals = []
        score = 0.0

        suspicious_count = (
            metrics.reservation_churn_count_30d +
            metrics.status_regression_count_30d +
            metrics.late_modification_count_30d +
            metrics.duplicate_payment_attempts_30d
        )

        if suspicious_count >= self.suspicious_pattern_critical:
            score += 0.8
            signals.append(RiskSignal(
                name="suspicious_patterns",
                value=suspicious_count,
                weight=0.8,
                category="pattern",
                description="Multiple suspicious behavioral patterns detected",
                threshold_exceeded=True,
            ))
        elif suspicious_count >= self.suspicious_pattern_warning:
            score += 0.4
            signals.append(RiskSignal(
                name="suspicious_patterns",
                value=suspicious_count,
                weight=0.4,
                category="pattern",
                description="Some suspicious patterns detected",
                threshold_exceeded=False,
            ))

        # Individual pattern signals
        if metrics.reservation_churn_count_30d >= 2:
            score += 0.2
            signals.append(RiskSignal(
                name="reservation_churn",
                value=metrics.reservation_churn_count_30d,
                weight=0.2,
                category="pattern",
                description="Reservation churn detected (cancel then recreate)",
                threshold_exceeded=metrics.reservation_churn_count_30d >= 3,
            ))

        if metrics.status_regression_count_30d >= 1:
            score += 0.15
            signals.append(RiskSignal(
                name="status_regression",
                value=metrics.status_regression_count_30d,
                weight=0.15,
                category="pattern",
                description="Status regressions detected (status went backwards)",
                threshold_exceeded=metrics.status_regression_count_30d >= 2,
            ))

        # New tenant flag
        if metrics.is_new_tenant:
            score += 0.1
            signals.append(RiskSignal(
                name="new_tenant",
                value=f"{metrics.tenant_age_days} days",
                weight=0.1,
                category="pattern",
                description="New tenant - limited history for risk assessment",
                threshold_exceeded=False,
            ))

        return min(score, 1.0), signals

    def _determine_trend(self, metrics: TenantMetrics) -> TrendDirection:
        """Determine overall risk trend from metrics."""
        # Average the trends
        trends = [
            metrics.return_rate_trend,
            metrics.reversal_rate_trend,
        ]

        avg_trend = sum(trends) / len(trends) if trends else 0

        if avg_trend > 0.3:
            return TrendDirection.CRITICAL_DEGRADATION
        elif avg_trend > 0.1:
            return TrendDirection.DEGRADING
        elif avg_trend < -0.1:
            return TrendDirection.IMPROVING
        else:
            return TrendDirection.STABLE

    def _generate_recommendations(
        self,
        metrics: TenantMetrics,
        return_score: float,
        reversal_score: float,
        funding_score: float,
        settlement_score: float,
        pattern_score: float,
    ) -> list[str]:
        """Generate actionable recommendations."""
        recommendations = []

        if return_score >= 0.5:
            recommendations.append(
                "Review return patterns and consider requiring prenotes for new payees"
            )

        if reversal_score >= 0.5:
            recommendations.append(
                "Investigate reversal causes - may indicate process issues or disputes"
            )

        if funding_score >= 0.5:
            recommendations.append(
                "Consider switching to prefunded model or increasing reserve requirements"
            )

        if settlement_score >= 0.5:
            recommendations.append(
                "Review settlement reconciliation process and provider performance"
            )

        if pattern_score >= 0.5:
            recommendations.append(
                "Investigate suspicious patterns - possible fraud or system misuse"
            )

        if metrics.is_new_tenant:
            recommendations.append(
                "Monitor closely as new tenant - limited history increases uncertainty"
            )

        if not recommendations:
            recommendations.append("No immediate actions required - continue normal monitoring")

        return recommendations


def create_risk_profile_event(profile: TenantRiskProfile) -> dict:
    """
    Create a TenantRiskProfileGenerated event.

    This event enables downstream systems to react
    (alerts, dashboards, etc.).
    """
    return {
        "event_type": "TenantRiskProfileGenerated",
        "event_id": str(uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "payload": {
            "profile_id": str(profile.profile_id),
            "tenant_id": str(profile.tenant_id),
            "risk_level": profile.risk_level.value,
            "risk_score": profile.risk_score,
            "risk_trend": profile.risk_trend.value,
            "requires_review": profile.requires_review,
            "requires_immediate_action": profile.requires_immediate_action,
            "signal_count": len(profile.signals),
            "critical_signals": len([s for s in profile.signals if s.threshold_exceeded]),
        },
    }
