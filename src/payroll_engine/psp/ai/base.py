"""
Base interfaces for PSP AI Advisory Engine.

All advisors share these foundational types and constraints.
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional, Protocol
from uuid import UUID


class AdvisoryMode(Enum):
    """
    Operating mode for AI advisors.

    ADVISORY_ONLY is the only allowed mode.
    This enum exists to make the constraint explicit and auditable.
    """
    ADVISORY_ONLY = "advisory_only"
    # No other modes are permitted.
    # If you're tempted to add AUTONOMOUS or AUTO_APPLY, stop.
    # AI must never mutate financial state.


@dataclass(frozen=True)
class AdvisoryConfig:
    """
    Configuration for AI advisory engine.

    IMPORTANT: AI is disabled by default.

    To enable AI advisory features:
    1. Install with: pip install payroll-engine[ai]
    2. Explicitly enable: AdvisoryConfig(enabled=True)

    This two-step opt-in ensures:
    - Zero runtime cost if not enabled
    - Explicit intent to use advisory features
    - No accidental AI code paths in production
    """
    enabled: bool = False  # OFF by default - explicit opt-in required
    mode: AdvisoryMode = AdvisoryMode.ADVISORY_ONLY
    model_name: str = "rules_baseline"
    emit_events: bool = True

    # Confidence thresholds
    min_confidence_to_emit: float = 0.0  # Emit all advisories by default
    high_confidence_threshold: float = 0.85

    # Feature extraction settings
    lookback_days: int = 90

    def __post_init__(self) -> None:
        """Validate configuration."""
        if self.mode != AdvisoryMode.ADVISORY_ONLY:
            raise ValueError(
                f"Only ADVISORY_ONLY mode is permitted. Got: {self.mode}"
            )
        if not 0.0 <= self.min_confidence_to_emit <= 1.0:
            raise ValueError("min_confidence_to_emit must be between 0 and 1")
        if not 0.0 <= self.high_confidence_threshold <= 1.0:
            raise ValueError("high_confidence_threshold must be between 0 and 1")


@dataclass(frozen=True)
class ContributingFactor:
    """A factor that contributed to an advisory decision."""
    name: str
    value: Any
    weight: float  # How much this factor influenced the decision (0-1)
    direction: str  # "increases_risk", "decreases_risk", "neutral"
    explanation: str


@dataclass(frozen=True)
class Advisory:
    """
    Base class for all AI advisories.

    An advisory is a recommendation that:
    - Has a confidence score
    - Is fully explainable
    - Never triggers automatic action
    - Is persisted as a domain event
    """
    advisory_id: UUID
    tenant_id: UUID
    generated_at: datetime
    model_name: str
    model_version: str
    feature_schema_hash: str
    confidence: float
    contributing_factors: tuple[ContributingFactor, ...]
    explanation: str

    def __post_init__(self) -> None:
        """Validate advisory."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0 and 1. Got: {self.confidence}")


@dataclass(frozen=True)
class ReturnAdvisory(Advisory):
    """Advisory for payment return root-cause analysis."""
    payment_id: UUID
    return_code: str
    suggested_error_origin: str  # "employee", "employer", "provider", "unknown"
    suggested_liability_party: str  # "employee", "employer", "psp", "provider", "unknown"
    suggested_recovery_path: str  # "offset", "clawback", "write_off", "investigate"

    def __post_init__(self) -> None:
        super().__post_init__()
        valid_origins = {"employee", "employer", "provider", "psp", "unknown"}
        valid_parties = {"employee", "employer", "psp", "provider", "unknown"}
        valid_paths = {"offset", "clawback", "write_off", "investigate"}

        if self.suggested_error_origin not in valid_origins:
            raise ValueError(f"Invalid error origin: {self.suggested_error_origin}")
        if self.suggested_liability_party not in valid_parties:
            raise ValueError(f"Invalid liability party: {self.suggested_liability_party}")
        if self.suggested_recovery_path not in valid_paths:
            raise ValueError(f"Invalid recovery path: {self.suggested_recovery_path}")


@dataclass(frozen=True)
class FundingRiskAdvisory(Advisory):
    """Advisory for payroll funding risk prediction."""
    payroll_batch_id: Optional[UUID]  # None for general tenant risk
    predicted_amount: Decimal
    risk_score: float  # 0.0 (no risk) to 1.0 (certain failure)
    risk_band: str  # "low", "medium", "high", "critical"
    suggested_reserve_buffer: Decimal

    def __post_init__(self) -> None:
        super().__post_init__()
        if not 0.0 <= self.risk_score <= 1.0:
            raise ValueError(f"Risk score must be between 0 and 1. Got: {self.risk_score}")
        if self.risk_band not in {"low", "medium", "high", "critical"}:
            raise ValueError(f"Invalid risk band: {self.risk_band}")
        if self.suggested_reserve_buffer < 0:
            raise ValueError("Suggested reserve buffer cannot be negative")


class Advisor(Protocol):
    """Protocol for all advisors."""

    @property
    def model_name(self) -> str:
        """Return the model name."""
        ...

    @property
    def model_version(self) -> str:
        """Return the model version."""
        ...

    def is_enabled(self) -> bool:
        """Return whether the advisor is enabled."""
        ...
