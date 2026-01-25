"""
Return Root-Cause Advisor.

Analyzes payment returns and provides:
- Suggested error origin
- Suggested liability party
- Suggested recovery path
- Human-readable explanation
- Confidence score

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
    ReturnAdvisory,
    ContributingFactor,
)
from payroll_engine.psp.ai.features import (
    FeatureExtractor,
    ReturnFeatures,
)
from payroll_engine.psp.ai.models.rules_baseline import RulesBaselineReturnModel


class ReturnAdvisor:
    """
    Advisor for payment return root-cause analysis.

    This advisor:
    1. Extracts features from the return event and history
    2. Runs a prediction model (rules-based or ML)
    3. Generates a human-readable explanation
    4. Emits an advisory event

    It NEVER:
    - Writes to the ledger
    - Changes payment status
    - Modifies liability records
    - Makes final decisions

    Humans or policies must review and confirm advisories.
    """

    def __init__(
        self,
        config: AdvisoryConfig,
        event_store,
        feature_extractor: Optional[FeatureExtractor] = None,
    ):
        """
        Initialize return advisor.

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
            return RulesBaselineReturnModel()
        # Future: Add ML models here
        # elif model_name == "sklearn_v1":
        #     return SklearnReturnModel.load("models/return_v1.pkl")
        else:
            # Fallback to rules baseline
            return RulesBaselineReturnModel()

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
        payment_id: UUID,
        return_code: str,
        return_date: Optional[datetime] = None,
    ) -> Optional[ReturnAdvisory]:
        """
        Analyze a payment return and generate an advisory.

        Args:
            tenant_id: The tenant ID
            payment_id: The payment instruction ID
            return_code: ACH/FedNow return code (e.g., "R01")
            return_date: When the return occurred (defaults to now)

        Returns:
            ReturnAdvisory with recommendation, or None if disabled
        """
        if not self._config.enabled:
            return None

        return_date = return_date or datetime.utcnow()

        # Step 1: Extract features
        features = self._feature_extractor.extract_return_features(
            tenant_id=tenant_id,
            payment_id=payment_id,
            return_code=return_code,
            return_date=return_date,
        )

        # Step 2: Run prediction
        (
            error_origin,
            liability_party,
            recovery_path,
            confidence,
            factors,
        ) = self._model.predict(features)

        # Step 3: Check confidence threshold
        if confidence < self._config.min_confidence_to_emit:
            return None

        # Step 4: Generate explanation
        explanation = self._generate_explanation(
            features=features,
            error_origin=error_origin,
            liability_party=liability_party,
            recovery_path=recovery_path,
            confidence=confidence,
            factors=factors,
        )

        # Step 5: Build advisory
        advisory = ReturnAdvisory(
            advisory_id=uuid4(),
            tenant_id=tenant_id,
            generated_at=datetime.utcnow(),
            model_name=self._model.model_name,
            model_version=self._model.model_version,
            feature_schema_hash=features.schema_hash,
            confidence=confidence,
            contributing_factors=tuple(factors),
            explanation=explanation,
            payment_id=payment_id,
            return_code=return_code,
            suggested_error_origin=error_origin,
            suggested_liability_party=liability_party,
            suggested_recovery_path=recovery_path,
        )

        return advisory

    def _generate_explanation(
        self,
        features: ReturnFeatures,
        error_origin: str,
        liability_party: str,
        recovery_path: str,
        confidence: float,
        factors: list[ContributingFactor],
    ) -> str:
        """Generate human-readable explanation."""
        # Sort factors by weight
        sorted_factors = sorted(factors, key=lambda f: f.weight, reverse=True)
        top_factors = sorted_factors[:3]

        # Build explanation
        confidence_word = self._confidence_word(confidence)

        lines = [
            f"This return is {confidence_word} {error_origin}-caused "
            f"({confidence:.0%} confidence).",
            "",
            "Key factors:",
        ]

        for f in top_factors:
            lines.append(f"  - {f.explanation}")

        lines.extend([
            "",
            f"Suggested liable party: {liability_party}",
            f"Suggested recovery path: {recovery_path}",
        ])

        # Add context
        if features.payee_is_new_account:
            lines.append("")
            lines.append(f"Note: This payee account is only {features.payee_account_age_days} days old.")

        if features.payee_prior_returns_90d > 0:
            lines.append(f"Note: This payee has {features.payee_prior_returns_90d} prior returns in 90 days.")

        return "\n".join(lines)

    def _confidence_word(self, confidence: float) -> str:
        """Convert confidence score to human word."""
        if confidence >= 0.9:
            return "almost certainly"
        elif confidence >= 0.75:
            return "likely"
        elif confidence >= 0.6:
            return "probably"
        elif confidence >= 0.4:
            return "possibly"
        else:
            return "uncertain if"


@dataclass
class ReturnAdvisoryResult:
    """Result of return advisory analysis."""
    advisory: Optional[ReturnAdvisory]
    features: ReturnFeatures
    raw_scores: dict[str, float]
