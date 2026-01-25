"""
Funding Risk Advisor.

Predicts the risk of payroll funding failure and provides:
- Risk score (0.0 to 1.0)
- Risk band (low/medium/high/critical)
- Suggested reserve buffer
- Human-readable explanation
- Contributing factors

CONSTRAINT: This advisor NEVER mutates state.
It only reads events and emits advisory recommendations.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from payroll_engine.psp.ai.base import (
    AdvisoryConfig,
    FundingRiskAdvisory,
    ContributingFactor,
)
from payroll_engine.psp.ai.features import (
    FeatureExtractor,
    FundingRiskFeatures,
)
from payroll_engine.psp.ai.models.rules_baseline import RulesBaselineFundingRiskModel


class FundingRiskAdvisor:
    """
    Advisor for payroll funding risk prediction.

    This advisor:
    1. Extracts features from payroll and funding history
    2. Runs a prediction model (rules-based or ML)
    3. Generates a human-readable explanation
    4. Emits an advisory event

    Use this BEFORE commit gate to:
    - Warn ops about high-risk payrolls
    - Suggest reserve buffer increases
    - Identify tenants needing attention

    It NEVER:
    - Blocks the funding gate
    - Modifies reservations
    - Changes account balances
    - Makes final decisions

    Humans or policies must review and act on advisories.
    """

    def __init__(
        self,
        config: AdvisoryConfig,
        event_store,
        feature_extractor: Optional[FeatureExtractor] = None,
    ):
        """
        Initialize funding risk advisor.

        Args:
            config: Advisory configuration
            event_store: PSP event store for reading history
            feature_extractor: Optional custom feature extractor
        """
        self._config = config
        self._event_store = event_store
        self._feature_extractor = feature_extractor or FeatureExtractor(
            event_store,
            lookback_days=config.lookback_days,
        )
        self._model = self._load_model(config.model_name)

    def _load_model(self, model_name: str):
        """Load the prediction model."""
        if model_name == "rules_baseline":
            return RulesBaselineFundingRiskModel()
        # Future: Add ML models here
        else:
            return RulesBaselineFundingRiskModel()

    @property
    def model_name(self) -> str:
        return self._model.model_name

    @property
    def model_version(self) -> str:
        return self._model.model_version

    def is_enabled(self) -> bool:
        return self._config.enabled

    def analyze(
        self,
        tenant_id: UUID,
        payroll_amount: Decimal,
        payment_count: int,
        scheduled_date: Optional[datetime] = None,
        payroll_batch_id: Optional[UUID] = None,
    ) -> Optional[FundingRiskAdvisory]:
        """
        Analyze funding risk for an upcoming payroll.

        Args:
            tenant_id: The tenant ID
            payroll_amount: Total payroll amount
            payment_count: Number of payments in batch
            scheduled_date: When payroll is scheduled (defaults to now)
            payroll_batch_id: Optional batch ID

        Returns:
            FundingRiskAdvisory with recommendation, or None if disabled
        """
        if not self._config.enabled:
            return None

        scheduled_date = scheduled_date or datetime.utcnow()

        # Step 1: Extract features
        features = self._feature_extractor.extract_funding_risk_features(
            tenant_id=tenant_id,
            payroll_amount=payroll_amount,
            payment_count=payment_count,
            scheduled_date=scheduled_date,
            payroll_batch_id=payroll_batch_id,
        )

        # Step 2: Run prediction
        (
            risk_score,
            risk_band,
            suggested_buffer,
            factors,
        ) = self._model.predict(features)

        # Step 3: Check confidence threshold (risk_score acts as confidence here)
        # For funding risk, we always want to emit even low-risk advisories
        # The risk_score IS the signal

        # Step 4: Generate explanation
        explanation = self._generate_explanation(
            features=features,
            risk_score=risk_score,
            risk_band=risk_band,
            suggested_buffer=suggested_buffer,
            factors=factors,
        )

        # Step 5: Build advisory
        advisory = FundingRiskAdvisory(
            advisory_id=uuid4(),
            tenant_id=tenant_id,
            generated_at=datetime.utcnow(),
            model_name=self._model.model_name,
            model_version=self._model.model_version,
            feature_schema_hash=features.schema_hash,
            confidence=1.0 - risk_score,  # Confidence in successful funding
            contributing_factors=tuple(factors),
            explanation=explanation,
            payroll_batch_id=payroll_batch_id,
            predicted_amount=payroll_amount,
            risk_score=risk_score,
            risk_band=risk_band,
            suggested_reserve_buffer=suggested_buffer,
        )

        return advisory

    def analyze_tenant(
        self,
        tenant_id: UUID,
        lookforward_days: int = 7,
    ) -> Optional[FundingRiskAdvisory]:
        """
        Analyze general funding risk for a tenant.

        This is for proactive monitoring, not specific payroll batches.
        Uses the tenant's average payroll as the predicted amount.

        Args:
            tenant_id: The tenant ID
            lookforward_days: How far ahead to consider

        Returns:
            FundingRiskAdvisory with general tenant risk
        """
        if not self._config.enabled:
            return None

        # Get average payroll amount from history
        avg_amount = self._get_average_payroll(tenant_id)
        avg_count = self._get_average_payment_count(tenant_id)

        if avg_amount == Decimal("0"):
            return None  # No history to analyze

        return self.analyze(
            tenant_id=tenant_id,
            payroll_amount=avg_amount,
            payment_count=avg_count,
            scheduled_date=datetime.utcnow(),
            payroll_batch_id=None,  # No specific batch
        )

    def _get_average_payroll(self, tenant_id: UUID) -> Decimal:
        """Get average payroll amount from history."""
        # This would query historical payroll events
        # For now, return 0 to indicate no history
        return Decimal("0")

    def _get_average_payment_count(self, tenant_id: UUID) -> int:
        """Get average payment count from history."""
        return 0

    def _generate_explanation(
        self,
        features: FundingRiskFeatures,
        risk_score: float,
        risk_band: str,
        suggested_buffer: Decimal,
        factors: list[ContributingFactor],
    ) -> str:
        """Generate human-readable explanation."""
        # Sort factors by weight
        sorted_factors = sorted(factors, key=lambda f: f.weight, reverse=True)
        top_factors = sorted_factors[:3]

        # Build explanation
        risk_word = self._risk_word(risk_band)

        lines = [
            f"Funding risk is {risk_word} ({risk_score:.0%} risk score).",
            "",
        ]

        if factors:
            lines.append("Contributing factors:")
            for f in top_factors:
                lines.append(f"  - {f.explanation}")
            lines.append("")

        # Add recommendations
        lines.append("Recommendations:")

        if risk_band == "critical":
            lines.extend([
                f"  - Increase reserve buffer to at least ${suggested_buffer:,.2f}",
                "  - Consider delaying payroll until funding is confirmed",
                "  - Contact tenant about funding status",
            ])
        elif risk_band == "high":
            lines.extend([
                f"  - Increase reserve buffer to ${suggested_buffer:,.2f}",
                "  - Monitor settlement activity before commit",
            ])
        elif risk_band == "medium":
            lines.extend([
                f"  - Suggested reserve buffer: ${suggested_buffer:,.2f}",
                "  - No immediate action required",
            ])
        else:
            lines.extend([
                "  - Standard processing recommended",
                f"  - Standard reserve buffer: ${suggested_buffer:,.2f}",
            ])

        # Add context
        if features.funding_blocks_30d > 0:
            lines.append("")
            lines.append(f"Note: Tenant had {features.funding_blocks_30d} funding block(s) in the last 30 days.")

        if features.spike_ratio > 1.5:
            lines.append(f"Note: This payroll is {features.spike_ratio:.1f}x the 90-day average.")

        if features.funding_headroom < Decimal("0"):
            lines.append(f"Warning: Current available balance (${features.current_available_balance:,.2f}) "
                        f"is insufficient for this payroll (${features.payroll_amount:,.2f}).")

        return "\n".join(lines)

    def _risk_word(self, risk_band: str) -> str:
        """Convert risk band to human word."""
        return {
            "critical": "CRITICAL",
            "high": "HIGH",
            "medium": "ELEVATED",
            "low": "LOW",
        }.get(risk_band, "UNKNOWN")


@dataclass
class FundingRiskResult:
    """Result of funding risk analysis."""
    advisory: Optional[FundingRiskAdvisory]
    features: FundingRiskFeatures
    raw_score: float
