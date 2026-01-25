"""
Counterfactual Simulator for Policy Planning.

Lets ops ask: "What if we used STRICT instead of HYBRID last month?"

This is a PURE FUNCTION that:
- Replays past payroll batches
- Swaps the funding policy config
- Reports impact (blocks, buffer needed, delays)

NO MONEY MOVEMENT. Just analysis for leadership decisions.

CRITICAL: This is read-only analysis. It NEVER modifies state.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Optional, Protocol
from uuid import UUID, uuid4


class FundingPolicy(Enum):
    """Funding policy options for counterfactual analysis."""
    STRICT = "strict"  # Block if ANY risk indicator
    HYBRID = "hybrid"  # Block only if multiple indicators or critical
    PERMISSIVE = "permissive"  # Only block for critical issues


@dataclass(frozen=True)
class PolicyConfig:
    """
    Configuration for a funding policy.

    These thresholds determine when payroll is blocked.
    """
    policy: FundingPolicy

    # Risk score thresholds
    block_threshold: float  # Block if risk_score >= this
    warn_threshold: float  # Warn if risk_score >= this

    # Spike thresholds
    spike_block_ratio: float  # Block if spike_ratio >= this
    spike_warn_ratio: float  # Warn if spike_ratio >= this

    # Headroom requirements
    min_headroom_pct: float  # Block if headroom < payroll * this
    required_buffer_pct: float  # Required buffer as % of payroll

    # Historical patterns
    block_if_recent_blocks: bool  # Block if funding blocks in last 30d
    max_settlement_delay_days: float  # Block if p95 delay > this


# Predefined policy configurations
STRICT_POLICY = PolicyConfig(
    policy=FundingPolicy.STRICT,
    block_threshold=0.30,
    warn_threshold=0.15,
    spike_block_ratio=1.5,
    spike_warn_ratio=1.2,
    min_headroom_pct=0.20,
    required_buffer_pct=0.15,
    block_if_recent_blocks=True,
    max_settlement_delay_days=3.0,
)

HYBRID_POLICY = PolicyConfig(
    policy=FundingPolicy.HYBRID,
    block_threshold=0.50,
    warn_threshold=0.25,
    spike_block_ratio=2.0,
    spike_warn_ratio=1.5,
    min_headroom_pct=0.10,
    required_buffer_pct=0.10,
    block_if_recent_blocks=False,  # Only warn, don't block
    max_settlement_delay_days=4.0,
)

PERMISSIVE_POLICY = PolicyConfig(
    policy=FundingPolicy.PERMISSIVE,
    block_threshold=0.70,
    warn_threshold=0.40,
    spike_block_ratio=3.0,
    spike_warn_ratio=2.0,
    min_headroom_pct=0.0,  # No minimum
    required_buffer_pct=0.05,
    block_if_recent_blocks=False,
    max_settlement_delay_days=5.0,
)


@dataclass
class PayrollBatchSnapshot:
    """
    Snapshot of a historical payroll batch for counterfactual analysis.

    This captures the state at the time of the batch, allowing
    accurate replay with different policies.
    """
    batch_id: UUID
    tenant_id: UUID
    batch_date: datetime
    payroll_amount: Decimal
    payment_count: int

    # Risk indicators at time of batch
    risk_score: float
    spike_ratio: float
    funding_headroom: Decimal
    funding_blocks_30d: int
    p95_settlement_delay: float

    # Actual outcome
    was_blocked: bool
    actual_policy: FundingPolicy
    block_reason: Optional[str] = None

    # Additional context
    available_balance: Decimal = Decimal("0")
    pending_settlements: Decimal = Decimal("0")


@dataclass
class CounterfactualOutcome:
    """Outcome of applying a policy to a single batch."""
    batch_id: UUID
    would_block: bool
    block_reasons: list[str]
    required_buffer: Decimal
    risk_score: float

    # Comparison to actual
    actual_blocked: bool
    outcome_changed: bool  # Did the counterfactual change the outcome?


@dataclass
class CounterfactualReport:
    """
    Complete counterfactual analysis report.

    Shows what would have happened with a different policy.
    """
    report_id: UUID
    generated_at: datetime
    period_start: datetime
    period_end: datetime

    # Policy comparison
    actual_policy: FundingPolicy
    counterfactual_policy: FundingPolicy

    # Batch counts
    total_batches: int = 0
    actual_blocks: int = 0
    counterfactual_blocks: int = 0

    # Impact analysis
    additional_blocks: int = 0  # Batches that would be blocked but weren't
    avoided_blocks: int = 0  # Batches that were blocked but wouldn't be
    unchanged: int = 0  # Same outcome either way

    # Financial impact
    total_payroll_volume: Decimal = Decimal("0")
    payroll_that_would_block: Decimal = Decimal("0")
    additional_buffer_required: Decimal = Decimal("0")

    # Delay impact (estimated)
    estimated_delay_days: int = 0  # Total days of delay from additional blocks

    # Detailed outcomes
    outcomes: list[CounterfactualOutcome] = field(default_factory=list)

    # Risk distribution
    risk_score_distribution: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "report_id": str(self.report_id),
            "generated_at": self.generated_at.isoformat(),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "policy_comparison": {
                "actual": self.actual_policy.value,
                "counterfactual": self.counterfactual_policy.value,
            },
            "summary": {
                "total_batches": self.total_batches,
                "actual_blocks": self.actual_blocks,
                "counterfactual_blocks": self.counterfactual_blocks,
                "additional_blocks": self.additional_blocks,
                "avoided_blocks": self.avoided_blocks,
                "unchanged": self.unchanged,
            },
            "financial_impact": {
                "total_payroll_volume": str(self.total_payroll_volume),
                "payroll_that_would_block": str(self.payroll_that_would_block),
                "additional_buffer_required": str(self.additional_buffer_required),
            },
            "delay_impact": {
                "estimated_delay_days": self.estimated_delay_days,
            },
            "risk_distribution": self.risk_score_distribution,
        }

    def to_markdown(self, max_items: int = 20) -> str:
        """
        Generate markdown report.

        Args:
            max_items: Maximum items to show in lists (default 20).
                       Use 0 for unlimited.
        """
        lines = [
            f"# Counterfactual Policy Analysis",
            f"",
            f"**Period:** {self.period_start.date()} to {self.period_end.date()}",
            f"**Generated:** {self.generated_at.isoformat()}",
            f"",
            f"## Policy Comparison",
            f"",
            f"| | Actual | Counterfactual |",
            f"|---|--------|----------------|",
            f"| Policy | {self.actual_policy.value.upper()} | {self.counterfactual_policy.value.upper()} |",
            f"| Blocks | {self.actual_blocks} | {self.counterfactual_blocks} |",
            f"| Block Rate | {self._pct(self.actual_blocks, self.total_batches)} | {self._pct(self.counterfactual_blocks, self.total_batches)} |",
            f"",
            f"## Impact Summary",
            f"",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Total Batches Analyzed | {self.total_batches} |",
            f"| Additional Blocks | {self.additional_blocks} |",
            f"| Avoided Blocks | {self.avoided_blocks} |",
            f"| Unchanged Outcomes | {self.unchanged} |",
            f"| Payroll Volume at Risk | ${self.payroll_that_would_block:,.2f} |",
            f"| Additional Buffer Needed | ${self.additional_buffer_required:,.2f} |",
            f"| Est. Delay Impact | {self.estimated_delay_days} days |",
            f"",
        ]

        if self.additional_blocks > 0:
            lines.extend([
                f"## Additional Blocks Under {self.counterfactual_policy.value.upper()}",
                f"",
                f"The following {self.additional_blocks} batches would have been blocked:",
                f"",
            ])
            shown = 0
            for outcome in self.outcomes:
                if outcome.would_block and not outcome.actual_blocked:
                    if max_items > 0 and shown >= max_items:
                        remaining = self.additional_blocks - shown
                        lines.append(f"- ... and {remaining} more")
                        break
                    lines.append(f"- Batch {str(outcome.batch_id)[:8]}... - Reasons: {', '.join(outcome.block_reasons)}")
                    shown += 1
            lines.append("")

        if self.avoided_blocks > 0:
            lines.extend([
                f"## Avoided Blocks Under {self.counterfactual_policy.value.upper()}",
                f"",
                f"The following {self.avoided_blocks} batches would NOT have been blocked:",
                f"",
            ])
            shown = 0
            for outcome in self.outcomes:
                if not outcome.would_block and outcome.actual_blocked:
                    if max_items > 0 and shown >= max_items:
                        remaining = self.avoided_blocks - shown
                        lines.append(f"- ... and {remaining} more")
                        break
                    lines.append(f"- Batch {str(outcome.batch_id)[:8]}...")
                    shown += 1
            lines.append("")

        return "\n".join(lines)

    def _pct(self, num: int, denom: int) -> str:
        """Format percentage."""
        if denom == 0:
            return "0%"
        return f"{100 * num / denom:.1f}%"


class CounterfactualSimulator:
    """
    Simulate what-if scenarios for funding policies.

    This is a PURE FUNCTION - it takes historical data and
    returns analysis without modifying any state.
    """

    def __init__(self, avg_delay_per_block_days: int = 1):
        """
        Initialize simulator.

        Args:
            avg_delay_per_block_days: Estimated delay when a batch is blocked
        """
        self.avg_delay_per_block = avg_delay_per_block_days

    def simulate(
        self,
        batches: list[PayrollBatchSnapshot],
        counterfactual_policy: PolicyConfig,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> CounterfactualReport:
        """
        Run counterfactual simulation.

        Args:
            batches: Historical batch snapshots
            counterfactual_policy: Policy to simulate
            period_start: Start of analysis period (optional)
            period_end: End of analysis period (optional)

        Returns:
            Complete counterfactual analysis report
        """
        # Filter by period if specified
        if period_start:
            batches = [b for b in batches if b.batch_date >= period_start]
        if period_end:
            batches = [b for b in batches if b.batch_date <= period_end]

        if not batches:
            return CounterfactualReport(
                report_id=uuid4(),
                generated_at=datetime.utcnow(),
                period_start=period_start or datetime.utcnow(),
                period_end=period_end or datetime.utcnow(),
                actual_policy=FundingPolicy.HYBRID,
                counterfactual_policy=counterfactual_policy.policy,
            )

        # Determine actual policy from data
        actual_policies = [b.actual_policy for b in batches]
        actual_policy = max(set(actual_policies), key=actual_policies.count)

        report = CounterfactualReport(
            report_id=uuid4(),
            generated_at=datetime.utcnow(),
            period_start=period_start or min(b.batch_date for b in batches),
            period_end=period_end or max(b.batch_date for b in batches),
            actual_policy=actual_policy,
            counterfactual_policy=counterfactual_policy.policy,
            total_batches=len(batches),
        )

        # Initialize risk distribution buckets
        report.risk_score_distribution = {
            "0-20%": 0,
            "20-40%": 0,
            "40-60%": 0,
            "60-80%": 0,
            "80-100%": 0,
        }

        for batch in batches:
            # Track actual blocks
            if batch.was_blocked:
                report.actual_blocks += 1

            # Apply counterfactual policy
            outcome = self._apply_policy(batch, counterfactual_policy)
            report.outcomes.append(outcome)

            # Track counterfactual blocks
            if outcome.would_block:
                report.counterfactual_blocks += 1
                report.payroll_that_would_block += batch.payroll_amount

            # Track changes
            if outcome.outcome_changed:
                if outcome.would_block and not outcome.actual_blocked:
                    report.additional_blocks += 1
                    report.estimated_delay_days += self.avg_delay_per_block
                elif not outcome.would_block and outcome.actual_blocked:
                    report.avoided_blocks += 1
            else:
                report.unchanged += 1

            # Track required buffer
            report.additional_buffer_required += outcome.required_buffer
            report.total_payroll_volume += batch.payroll_amount

            # Track risk distribution
            risk_pct = batch.risk_score * 100
            if risk_pct < 20:
                report.risk_score_distribution["0-20%"] += 1
            elif risk_pct < 40:
                report.risk_score_distribution["20-40%"] += 1
            elif risk_pct < 60:
                report.risk_score_distribution["40-60%"] += 1
            elif risk_pct < 80:
                report.risk_score_distribution["60-80%"] += 1
            else:
                report.risk_score_distribution["80-100%"] += 1

        return report

    def _apply_policy(
        self,
        batch: PayrollBatchSnapshot,
        policy: PolicyConfig,
    ) -> CounterfactualOutcome:
        """
        Apply a policy to a batch and determine outcome.

        This is a PURE FUNCTION - no side effects.
        """
        block_reasons: list[str] = []

        # Check risk score threshold
        if batch.risk_score >= policy.block_threshold:
            block_reasons.append(f"Risk score {batch.risk_score:.0%} >= {policy.block_threshold:.0%}")

        # Check spike ratio
        if batch.spike_ratio >= policy.spike_block_ratio:
            block_reasons.append(f"Spike ratio {batch.spike_ratio:.1f}x >= {policy.spike_block_ratio:.1f}x")

        # Check headroom
        min_headroom = batch.payroll_amount * Decimal(str(policy.min_headroom_pct))
        if batch.funding_headroom < min_headroom:
            block_reasons.append(
                f"Headroom ${batch.funding_headroom:,.2f} < required ${min_headroom:,.2f}"
            )

        # Check recent blocks
        if policy.block_if_recent_blocks and batch.funding_blocks_30d > 0:
            block_reasons.append(f"Recent funding blocks: {batch.funding_blocks_30d}")

        # Check settlement delay
        if batch.p95_settlement_delay > policy.max_settlement_delay_days:
            block_reasons.append(
                f"Settlement delay {batch.p95_settlement_delay:.1f}d > {policy.max_settlement_delay_days:.1f}d"
            )

        would_block = len(block_reasons) > 0

        # Calculate required buffer
        required_buffer = batch.payroll_amount * Decimal(str(policy.required_buffer_pct))
        if batch.risk_score >= policy.warn_threshold:
            # Increase buffer for warned batches
            required_buffer *= Decimal("1.5")

        return CounterfactualOutcome(
            batch_id=batch.batch_id,
            would_block=would_block,
            block_reasons=block_reasons,
            required_buffer=required_buffer,
            risk_score=batch.risk_score,
            actual_blocked=batch.was_blocked,
            outcome_changed=would_block != batch.was_blocked,
        )

    def compare_policies(
        self,
        batches: list[PayrollBatchSnapshot],
        policies: list[PolicyConfig],
    ) -> dict[str, CounterfactualReport]:
        """
        Compare multiple policies against the same batch data.

        Args:
            batches: Historical batch snapshots
            policies: List of policies to compare

        Returns:
            Dictionary mapping policy name to report
        """
        return {
            policy.policy.value: self.simulate(batches, policy)
            for policy in policies
        }


def get_policy_config(policy: FundingPolicy) -> PolicyConfig:
    """Get the configuration for a policy."""
    return {
        FundingPolicy.STRICT: STRICT_POLICY,
        FundingPolicy.HYBRID: HYBRID_POLICY,
        FundingPolicy.PERMISSIVE: PERMISSIVE_POLICY,
    }[policy]
