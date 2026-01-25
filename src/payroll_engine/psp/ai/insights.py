"""
AI Advisory Insights & Learning Loop.

Generates actionable insights from advisory decision records WITHOUT ML.
This creates huge ops leverage and generates training data for future ML.

Key reports:
- Top return codes where humans override AI
- Tenants with rising funding risk trend
- Provider-specific return anomalies
- Confidence calibration drift

CRITICAL: This is read-only analysis. It NEVER modifies state.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4
import json


class InsightSeverity(Enum):
    """Severity of an insight."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class InsightCategory(Enum):
    """Category of insight."""
    OVERRIDE_PATTERN = "override_pattern"
    CONFIDENCE_DRIFT = "confidence_drift"
    TENANT_TREND = "tenant_trend"
    PROVIDER_ANOMALY = "provider_anomaly"
    MODEL_ACCURACY = "model_accuracy"


@dataclass(frozen=True)
class Insight:
    """A single actionable insight from advisory data."""
    insight_id: UUID
    category: InsightCategory
    severity: InsightSeverity
    title: str
    summary: str
    evidence: dict[str, Any]
    recommendation: str
    generated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "insight_id": str(self.insight_id),
            "category": self.category.value,
            "severity": self.severity.value,
            "title": self.title,
            "summary": self.summary,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "generated_at": self.generated_at.isoformat(),
        }


@dataclass
class AdvisoryReport:
    """
    Weekly/daily advisory insights report.

    This is the output of `psp ai-report --since 7d`.
    """
    report_id: UUID
    generated_at: datetime
    period_start: datetime
    period_end: datetime

    # Summary stats
    total_advisories: int = 0
    total_decisions: int = 0
    accepted_count: int = 0
    overridden_count: int = 0
    auto_applied_count: int = 0
    pending_count: int = 0

    # Calculated metrics
    overall_accuracy: float = 0.0
    high_confidence_accuracy: float = 0.0  # >85% confidence advisories

    # Insights discovered
    insights: list[Insight] = field(default_factory=list)

    # Breakdowns
    by_return_code: dict[str, dict] = field(default_factory=dict)
    by_tenant: dict[str, dict] = field(default_factory=dict)
    by_provider: dict[str, dict] = field(default_factory=dict)
    by_model_version: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "report_id": str(self.report_id),
            "generated_at": self.generated_at.isoformat(),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "summary": {
                "total_advisories": self.total_advisories,
                "total_decisions": self.total_decisions,
                "accepted_count": self.accepted_count,
                "overridden_count": self.overridden_count,
                "auto_applied_count": self.auto_applied_count,
                "pending_count": self.pending_count,
                "overall_accuracy": round(self.overall_accuracy, 4),
                "high_confidence_accuracy": round(self.high_confidence_accuracy, 4),
            },
            "insights": [i.to_dict() for i in self.insights],
            "breakdowns": {
                "by_return_code": self.by_return_code,
                "by_tenant": self.by_tenant,
                "by_provider": self.by_provider,
                "by_model_version": self.by_model_version,
            },
        }

    def to_markdown(self) -> str:
        """Generate markdown report."""
        lines = [
            f"# AI Advisory Report",
            f"",
            f"**Period:** {self.period_start.date()} to {self.period_end.date()}",
            f"**Generated:** {self.generated_at.isoformat()}",
            f"",
            f"## Summary",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Advisories | {self.total_advisories} |",
            f"| Decisions Made | {self.total_decisions} |",
            f"| Accepted | {self.accepted_count} ({self._pct(self.accepted_count, self.total_decisions)}) |",
            f"| Overridden | {self.overridden_count} ({self._pct(self.overridden_count, self.total_decisions)}) |",
            f"| Auto-Applied | {self.auto_applied_count} ({self._pct(self.auto_applied_count, self.total_decisions)}) |",
            f"| Pending | {self.pending_count} |",
            f"| **Overall Accuracy** | **{self.overall_accuracy:.1%}** |",
            f"| High-Confidence Accuracy | {self.high_confidence_accuracy:.1%} |",
            f"",
        ]

        if self.insights:
            lines.extend([
                f"## Insights ({len(self.insights)})",
                f"",
            ])

            # Group by severity
            critical = [i for i in self.insights if i.severity == InsightSeverity.CRITICAL]
            warning = [i for i in self.insights if i.severity == InsightSeverity.WARNING]
            info = [i for i in self.insights if i.severity == InsightSeverity.INFO]

            for severity_name, insight_list in [
                ("🔴 Critical", critical),
                ("🟡 Warning", warning),
                ("🔵 Info", info),
            ]:
                if insight_list:
                    lines.append(f"### {severity_name}")
                    lines.append("")
                    for insight in insight_list:
                        lines.extend([
                            f"#### {insight.title}",
                            f"",
                            f"{insight.summary}",
                            f"",
                            f"**Recommendation:** {insight.recommendation}",
                            f"",
                        ])

        if self.by_return_code:
            lines.extend([
                f"## Override Rates by Return Code",
                f"",
                f"| Code | Total | Overridden | Override Rate |",
                f"|------|-------|------------|---------------|",
            ])
            sorted_codes = sorted(
                self.by_return_code.items(),
                key=lambda x: x[1].get("override_rate", 0),
                reverse=True
            )
            for code, stats in sorted_codes[:10]:
                lines.append(
                    f"| {code} | {stats['total']} | {stats['overridden']} | "
                    f"{stats.get('override_rate', 0):.1%} |"
                )
            lines.append("")

        return "\n".join(lines)

    def _pct(self, num: int, denom: int) -> str:
        """Format percentage."""
        if denom == 0:
            return "0%"
        return f"{100 * num / denom:.1f}%"


class InsightGenerator:
    """
    Generate insights from advisory decision records.

    This is a pure analysis class - it reads from a decision store
    and generates insights without modifying any state.
    """

    def __init__(
        self,
        high_confidence_threshold: float = 0.85,
        min_sample_size: int = 10,
        override_rate_warning_threshold: float = 0.20,
        override_rate_critical_threshold: float = 0.40,
    ):
        self.high_confidence_threshold = high_confidence_threshold
        self.min_sample_size = min_sample_size
        self.override_rate_warning = override_rate_warning_threshold
        self.override_rate_critical = override_rate_critical_threshold

    def generate_report(
        self,
        decisions: list[dict],  # List of AdvisoryDecisionRecord.to_dict()
        period_start: datetime,
        period_end: datetime,
    ) -> AdvisoryReport:
        """
        Generate a complete advisory report from decision records.

        Args:
            decisions: List of decision records as dictionaries
            period_start: Start of reporting period
            period_end: End of reporting period

        Returns:
            Complete AdvisoryReport with insights
        """
        report = AdvisoryReport(
            report_id=uuid4(),
            generated_at=datetime.utcnow(),
            period_start=period_start,
            period_end=period_end,
        )

        if not decisions:
            return report

        # Calculate basic stats
        report.total_advisories = len(decisions)

        for d in decisions:
            outcome = d.get("outcome", "pending")
            if outcome == "pending":
                report.pending_count += 1
            elif outcome == "accepted":
                report.accepted_count += 1
                report.total_decisions += 1
            elif outcome == "overridden":
                report.overridden_count += 1
                report.total_decisions += 1
            elif outcome == "auto_applied":
                report.auto_applied_count += 1
                report.total_decisions += 1

        # Calculate accuracy
        if report.total_decisions > 0:
            correct = report.accepted_count + report.auto_applied_count
            report.overall_accuracy = correct / report.total_decisions

        # High-confidence accuracy
        high_conf = [d for d in decisions if d.get("confidence", 0) >= self.high_confidence_threshold]
        high_conf_decided = [d for d in high_conf if d.get("outcome") != "pending"]
        if high_conf_decided:
            high_conf_correct = sum(
                1 for d in high_conf_decided
                if d.get("outcome") in ("accepted", "auto_applied")
            )
            report.high_confidence_accuracy = high_conf_correct / len(high_conf_decided)

        # Generate breakdowns
        report.by_return_code = self._breakdown_by_return_code(decisions)
        report.by_tenant = self._breakdown_by_tenant(decisions)
        report.by_model_version = self._breakdown_by_model_version(decisions)

        # Generate insights
        report.insights = self._generate_insights(decisions, report)

        return report

    def _breakdown_by_return_code(self, decisions: list[dict]) -> dict[str, dict]:
        """Break down stats by return code."""
        by_code: dict[str, dict] = {}

        for d in decisions:
            # Extract return code from suggested_outcome
            suggested = d.get("suggested_outcome", {})
            # For return advisories, there might be no explicit return_code in suggested_outcome
            # Try to get it from the advisory type context
            code = suggested.get("return_code", "UNKNOWN")
            if code == "UNKNOWN" and d.get("advisory_type") == "return":
                # The return code should be in the features or context
                code = "RETURN"  # Generic marker

            if code not in by_code:
                by_code[code] = {
                    "total": 0,
                    "accepted": 0,
                    "overridden": 0,
                    "auto_applied": 0,
                    "override_rate": 0.0,
                    "avg_confidence": 0.0,
                    "confidence_sum": 0.0,
                }

            stats = by_code[code]
            stats["total"] += 1
            stats["confidence_sum"] += d.get("confidence", 0)

            outcome = d.get("outcome", "pending")
            if outcome == "accepted":
                stats["accepted"] += 1
            elif outcome == "overridden":
                stats["overridden"] += 1
            elif outcome == "auto_applied":
                stats["auto_applied"] += 1

        # Calculate rates
        for code, stats in by_code.items():
            decided = stats["accepted"] + stats["overridden"] + stats["auto_applied"]
            if decided > 0:
                stats["override_rate"] = stats["overridden"] / decided
            if stats["total"] > 0:
                stats["avg_confidence"] = stats["confidence_sum"] / stats["total"]
            del stats["confidence_sum"]

        return by_code

    def _breakdown_by_tenant(self, decisions: list[dict]) -> dict[str, dict]:
        """Break down stats by tenant."""
        by_tenant: dict[str, dict] = {}

        for d in decisions:
            tenant_id = d.get("tenant_id", "unknown")

            if tenant_id not in by_tenant:
                by_tenant[tenant_id] = {
                    "total": 0,
                    "overridden": 0,
                    "override_rate": 0.0,
                    "return_advisories": 0,
                    "funding_risk_advisories": 0,
                }

            stats = by_tenant[tenant_id]
            stats["total"] += 1

            if d.get("outcome") == "overridden":
                stats["overridden"] += 1

            if d.get("advisory_type") == "return":
                stats["return_advisories"] += 1
            elif d.get("advisory_type") == "funding_risk":
                stats["funding_risk_advisories"] += 1

        # Calculate rates
        for tenant_id, stats in by_tenant.items():
            if stats["total"] > 0:
                stats["override_rate"] = stats["overridden"] / stats["total"]

        return by_tenant

    def _breakdown_by_model_version(self, decisions: list[dict]) -> dict[str, dict]:
        """Break down stats by model version."""
        by_version: dict[str, dict] = {}

        for d in decisions:
            model = d.get("model_name", "unknown")
            version = d.get("model_version", "unknown")
            key = f"{model}@{version}"

            if key not in by_version:
                by_version[key] = {
                    "total": 0,
                    "accepted": 0,
                    "overridden": 0,
                    "accuracy": 0.0,
                }

            stats = by_version[key]
            stats["total"] += 1

            outcome = d.get("outcome", "pending")
            if outcome in ("accepted", "auto_applied"):
                stats["accepted"] += 1
            elif outcome == "overridden":
                stats["overridden"] += 1

        # Calculate accuracy
        for key, stats in by_version.items():
            decided = stats["accepted"] + stats["overridden"]
            if decided > 0:
                stats["accuracy"] = stats["accepted"] / decided

        return by_version

    def _generate_insights(
        self,
        decisions: list[dict],
        report: AdvisoryReport,
    ) -> list[Insight]:
        """Generate actionable insights from the data."""
        insights = []

        # 1. High override rate codes
        insights.extend(self._insight_high_override_codes(report.by_return_code))

        # 2. Confidence calibration drift
        insights.extend(self._insight_confidence_drift(decisions))

        # 3. Tenant-specific anomalies
        insights.extend(self._insight_tenant_anomalies(report.by_tenant))

        # 4. Model accuracy degradation
        insights.extend(self._insight_model_accuracy(report.by_model_version))

        # Sort by severity
        severity_order = {
            InsightSeverity.CRITICAL: 0,
            InsightSeverity.WARNING: 1,
            InsightSeverity.INFO: 2,
        }
        insights.sort(key=lambda i: severity_order[i.severity])

        return insights

    def _insight_high_override_codes(
        self,
        by_code: dict[str, dict],
    ) -> list[Insight]:
        """Generate insights for return codes with high override rates."""
        insights = []

        for code, stats in by_code.items():
            if stats["total"] < self.min_sample_size:
                continue

            override_rate = stats["override_rate"]

            if override_rate >= self.override_rate_critical:
                severity = InsightSeverity.CRITICAL
            elif override_rate >= self.override_rate_warning:
                severity = InsightSeverity.WARNING
            else:
                continue

            insights.append(Insight(
                insight_id=uuid4(),
                category=InsightCategory.OVERRIDE_PATTERN,
                severity=severity,
                title=f"High override rate for {code}",
                summary=(
                    f"Return code {code} has a {override_rate:.0%} override rate "
                    f"({stats['overridden']} of {stats['total']} decisions). "
                    f"The AI model may have incorrect priors for this code."
                ),
                evidence={
                    "return_code": code,
                    "total": stats["total"],
                    "overridden": stats["overridden"],
                    "override_rate": override_rate,
                    "avg_confidence": stats["avg_confidence"],
                },
                recommendation=(
                    f"Review the return code classification for {code}. "
                    f"Consider adjusting the fault_prior or ambiguity score in return_codes.py. "
                    f"Collect override reasons to understand the pattern."
                ),
            ))

        return insights

    def _insight_confidence_drift(
        self,
        decisions: list[dict],
    ) -> list[Insight]:
        """Generate insights for confidence calibration drift."""
        insights = []

        # Find high-confidence overrides
        high_conf_overrides = [
            d for d in decisions
            if d.get("confidence", 0) >= self.high_confidence_threshold
            and d.get("outcome") == "overridden"
        ]

        if len(high_conf_overrides) >= 3:
            # This is concerning - high confidence should rarely be overridden
            total_high_conf = len([
                d for d in decisions
                if d.get("confidence", 0) >= self.high_confidence_threshold
                and d.get("outcome") != "pending"
            ])

            override_rate = len(high_conf_overrides) / total_high_conf if total_high_conf > 0 else 0

            if override_rate >= 0.10:
                severity = InsightSeverity.CRITICAL
            elif override_rate >= 0.05:
                severity = InsightSeverity.WARNING
            else:
                severity = InsightSeverity.INFO

            # Collect override reasons
            reasons = [d.get("override_reason", "No reason given") for d in high_conf_overrides]
            reason_counts: dict[str, int] = {}
            for r in reasons:
                reason_counts[r] = reason_counts.get(r, 0) + 1

            insights.append(Insight(
                insight_id=uuid4(),
                category=InsightCategory.CONFIDENCE_DRIFT,
                severity=severity,
                title="High-confidence advisories being overridden",
                summary=(
                    f"{len(high_conf_overrides)} advisories with >{self.high_confidence_threshold:.0%} "
                    f"confidence were overridden ({override_rate:.0%} of high-confidence decisions). "
                    f"This suggests the model may be overconfident."
                ),
                evidence={
                    "high_conf_overrides": len(high_conf_overrides),
                    "total_high_conf_decided": total_high_conf,
                    "override_rate": override_rate,
                    "top_reasons": dict(sorted(
                        reason_counts.items(),
                        key=lambda x: x[1],
                        reverse=True
                    )[:5]),
                },
                recommendation=(
                    "Review confidence calibration. Consider lowering confidence ceilings "
                    "or adding more indicators before reaching high confidence. "
                    "Analyze override reasons for patterns."
                ),
            ))

        return insights

    def _insight_tenant_anomalies(
        self,
        by_tenant: dict[str, dict],
    ) -> list[Insight]:
        """Generate insights for tenant-specific anomalies."""
        insights = []

        # Find tenants with unusually high override rates
        for tenant_id, stats in by_tenant.items():
            if stats["total"] < self.min_sample_size:
                continue

            if stats["override_rate"] >= self.override_rate_critical:
                insights.append(Insight(
                    insight_id=uuid4(),
                    category=InsightCategory.TENANT_TREND,
                    severity=InsightSeverity.WARNING,
                    title=f"Tenant {tenant_id[:8]}... has high override rate",
                    summary=(
                        f"Tenant has {stats['override_rate']:.0%} override rate "
                        f"({stats['overridden']} of {stats['total']}). "
                        f"This tenant may have unusual patterns not captured by the model."
                    ),
                    evidence={
                        "tenant_id": tenant_id,
                        "total": stats["total"],
                        "overridden": stats["overridden"],
                        "override_rate": stats["override_rate"],
                        "return_advisories": stats["return_advisories"],
                        "funding_risk_advisories": stats["funding_risk_advisories"],
                    },
                    recommendation=(
                        "Investigate this tenant's specific patterns. "
                        "Consider tenant-specific configuration or "
                        "flagging for manual review."
                    ),
                ))

        return insights

    def _insight_model_accuracy(
        self,
        by_version: dict[str, dict],
    ) -> list[Insight]:
        """Generate insights for model accuracy issues."""
        insights = []

        for version, stats in by_version.items():
            decided = stats["accepted"] + stats["overridden"]
            if decided < self.min_sample_size:
                continue

            accuracy = stats["accuracy"]

            if accuracy < 0.70:
                severity = InsightSeverity.CRITICAL
                message = "critically low"
            elif accuracy < 0.80:
                severity = InsightSeverity.WARNING
                message = "below target"
            else:
                continue

            insights.append(Insight(
                insight_id=uuid4(),
                category=InsightCategory.MODEL_ACCURACY,
                severity=severity,
                title=f"Model {version} accuracy is {message}",
                summary=(
                    f"Model version {version} has {accuracy:.0%} accuracy "
                    f"({stats['accepted']} correct of {decided} decisions). "
                    f"Target is 80%+."
                ),
                evidence={
                    "model_version": version,
                    "accuracy": accuracy,
                    "total_decided": decided,
                    "accepted": stats["accepted"],
                    "overridden": stats["overridden"],
                },
                recommendation=(
                    "Consider reverting to a previous model version or "
                    "investigating recent changes. Check if new return codes "
                    "or tenant patterns are causing issues."
                ),
            ))

        return insights


# =============================================================================
# Event for report generation
# =============================================================================

def create_report_event(report: AdvisoryReport) -> dict:
    """
    Create an AIAdvisoryReportGenerated event.

    This event is emitted when a report is generated, enabling
    downstream systems to react (alerts, dashboards, etc.).
    """
    return {
        "event_type": "AIAdvisoryReportGenerated",
        "event_id": str(uuid4()),
        "timestamp": datetime.utcnow().isoformat(),
        "payload": {
            "report_id": str(report.report_id),
            "period_start": report.period_start.isoformat(),
            "period_end": report.period_end.isoformat(),
            "total_advisories": report.total_advisories,
            "overall_accuracy": report.overall_accuracy,
            "insight_count": len(report.insights),
            "critical_insights": len([
                i for i in report.insights
                if i.severity == InsightSeverity.CRITICAL
            ]),
            "warning_insights": len([
                i for i in report.insights
                if i.severity == InsightSeverity.WARNING
            ]),
        },
    }
