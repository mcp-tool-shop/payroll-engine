"""
Advisory Decision Record.

Queryable record that pairs AI advisories with human decisions.

This enables:
- "Show me all advisories overridden last week and why"
- Model accuracy tracking over time
- Audit trail for regulatory review
- Training data collection for future ML models
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4
import hashlib
import json


class DecisionOutcome(Enum):
    """Outcome of an advisory decision."""
    PENDING = "pending"  # No decision made yet
    ACCEPTED = "accepted"  # Advisory was accepted as-is
    ACCEPTED_MODIFIED = "accepted_modified"  # Accepted with modifications
    OVERRIDDEN = "overridden"  # Human chose different classification
    IGNORED = "ignored"  # Advisory was ignored (no action taken)
    AUTO_APPLIED = "auto_applied"  # Policy auto-applied (high confidence)


@dataclass
class AdvisoryDecisionRecord:
    """
    Record of an advisory and its outcome.

    This is designed to be stored in psp_advisory_decision table
    for queryable audit trail.
    """
    # Identity
    record_id: UUID
    advisory_id: UUID
    advisory_type: str  # "return" or "funding_risk"
    tenant_id: UUID

    # Timing
    advisory_generated_at: datetime
    decision_made_at: Optional[datetime] = None

    # Model info
    model_name: str = ""
    model_version: str = ""

    # Input fingerprint (for reproducibility)
    feature_hash: str = ""  # Hash of input features
    evaluation_time: datetime = field(default_factory=datetime.utcnow)  # "as-of" time

    # Advisory output
    suggested_outcome: dict = field(default_factory=dict)
    confidence: float = 0.0
    feature_completeness: float = 1.0

    # Decision
    outcome: DecisionOutcome = DecisionOutcome.PENDING
    actual_outcome: Optional[dict] = None  # What was actually chosen
    override_reason: Optional[str] = None
    decided_by: Optional[str] = None  # User ID or "policy:auto_high_confidence"

    # Tracking
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def mark_accepted(
        self,
        decided_by: str,
        at: Optional[datetime] = None,
    ) -> None:
        """Mark advisory as accepted."""
        self.outcome = DecisionOutcome.ACCEPTED
        self.actual_outcome = self.suggested_outcome
        self.decided_by = decided_by
        self.decision_made_at = at or datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def mark_overridden(
        self,
        actual_outcome: dict,
        reason: str,
        decided_by: str,
        at: Optional[datetime] = None,
    ) -> None:
        """Mark advisory as overridden with different decision."""
        self.outcome = DecisionOutcome.OVERRIDDEN
        self.actual_outcome = actual_outcome
        self.override_reason = reason
        self.decided_by = decided_by
        self.decision_made_at = at or datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def mark_auto_applied(
        self,
        policy_name: str,
        at: Optional[datetime] = None,
    ) -> None:
        """Mark advisory as auto-applied by policy."""
        self.outcome = DecisionOutcome.AUTO_APPLIED
        self.actual_outcome = self.suggested_outcome
        self.decided_by = f"policy:{policy_name}"
        self.decision_made_at = at or datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def was_correct(self) -> Optional[bool]:
        """
        Check if advisory matched actual decision.

        Returns None if no decision made yet.
        """
        if self.outcome == DecisionOutcome.PENDING:
            return None
        if self.outcome in (DecisionOutcome.ACCEPTED, DecisionOutcome.AUTO_APPLIED):
            return True
        if self.outcome == DecisionOutcome.OVERRIDDEN:
            return False
        return None

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "record_id": str(self.record_id),
            "advisory_id": str(self.advisory_id),
            "advisory_type": self.advisory_type,
            "tenant_id": str(self.tenant_id),
            "advisory_generated_at": self.advisory_generated_at.isoformat(),
            "decision_made_at": self.decision_made_at.isoformat() if self.decision_made_at else None,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "feature_hash": self.feature_hash,
            "evaluation_time": self.evaluation_time.isoformat(),
            "suggested_outcome": self.suggested_outcome,
            "confidence": self.confidence,
            "feature_completeness": self.feature_completeness,
            "outcome": self.outcome.value,
            "actual_outcome": self.actual_outcome,
            "override_reason": self.override_reason,
            "decided_by": self.decided_by,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_return_advisory(
        cls,
        advisory,  # ReturnAdvisory
        feature_hash: str,
        evaluation_time: datetime,
        feature_completeness: float = 1.0,
    ) -> "AdvisoryDecisionRecord":
        """Create record from a return advisory."""
        return cls(
            record_id=uuid4(),
            advisory_id=advisory.advisory_id,
            advisory_type="return",
            tenant_id=advisory.tenant_id,
            advisory_generated_at=advisory.generated_at,
            model_name=advisory.model_name,
            model_version=advisory.model_version,
            feature_hash=feature_hash,
            evaluation_time=evaluation_time,
            suggested_outcome={
                "error_origin": advisory.suggested_error_origin,
                "liability_party": advisory.suggested_liability_party,
                "recovery_path": advisory.suggested_recovery_path,
            },
            confidence=advisory.confidence,
            feature_completeness=feature_completeness,
        )

    @classmethod
    def from_funding_risk_advisory(
        cls,
        advisory,  # FundingRiskAdvisory
        feature_hash: str,
        evaluation_time: datetime,
        feature_completeness: float = 1.0,
    ) -> "AdvisoryDecisionRecord":
        """Create record from a funding risk advisory."""
        return cls(
            record_id=uuid4(),
            advisory_id=advisory.advisory_id,
            advisory_type="funding_risk",
            tenant_id=advisory.tenant_id,
            advisory_generated_at=advisory.generated_at,
            model_name=advisory.model_name,
            model_version=advisory.model_version,
            feature_hash=feature_hash,
            evaluation_time=evaluation_time,
            suggested_outcome={
                "risk_score": advisory.risk_score,
                "risk_band": advisory.risk_band,
                "suggested_reserve_buffer": str(advisory.suggested_reserve_buffer),
            },
            confidence=advisory.confidence,
            feature_completeness=feature_completeness,
        )


def compute_feature_hash(features: dict) -> str:
    """
    Compute deterministic hash of features.

    Used to verify reproducibility - same features should
    produce same hash regardless of dict ordering.
    """
    # Sort keys for deterministic ordering
    canonical = json.dumps(features, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()[:32]


# =============================================================================
# SQL Schema for psp_advisory_decision table
# =============================================================================

ADVISORY_DECISION_TABLE_SQL = """
-- Advisory decision record table
-- Tracks AI advisories and human decisions for audit/accuracy tracking

CREATE TABLE IF NOT EXISTS psp_advisory_decision (
    record_id UUID PRIMARY KEY,
    advisory_id UUID NOT NULL,
    advisory_type VARCHAR(50) NOT NULL,  -- 'return' or 'funding_risk'
    tenant_id UUID NOT NULL,

    -- Timing
    advisory_generated_at TIMESTAMPTZ NOT NULL,
    decision_made_at TIMESTAMPTZ,

    -- Model info
    model_name VARCHAR(100) NOT NULL,
    model_version VARCHAR(50) NOT NULL,

    -- Input fingerprint
    feature_hash VARCHAR(64) NOT NULL,
    evaluation_time TIMESTAMPTZ NOT NULL,

    -- Advisory output
    suggested_outcome JSONB NOT NULL,
    confidence DECIMAL(5,4) NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
    feature_completeness DECIMAL(5,4) NOT NULL DEFAULT 1.0
        CHECK (feature_completeness >= 0 AND feature_completeness <= 1),

    -- Decision
    outcome VARCHAR(50) NOT NULL DEFAULT 'pending',
    actual_outcome JSONB,
    override_reason TEXT,
    decided_by VARCHAR(255),

    -- Tracking
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Constraints
    CONSTRAINT valid_outcome CHECK (
        outcome IN ('pending', 'accepted', 'accepted_modified', 'overridden', 'ignored', 'auto_applied')
    )
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_advisory_decision_tenant
    ON psp_advisory_decision(tenant_id);

CREATE INDEX IF NOT EXISTS idx_advisory_decision_outcome
    ON psp_advisory_decision(outcome);

CREATE INDEX IF NOT EXISTS idx_advisory_decision_generated
    ON psp_advisory_decision(advisory_generated_at);

CREATE INDEX IF NOT EXISTS idx_advisory_decision_type_outcome
    ON psp_advisory_decision(advisory_type, outcome);

-- Query: "Show me all overridden advisories last week"
CREATE INDEX IF NOT EXISTS idx_advisory_decision_overrides
    ON psp_advisory_decision(decision_made_at)
    WHERE outcome = 'overridden';

-- Query: "Model accuracy by version"
CREATE INDEX IF NOT EXISTS idx_advisory_decision_model
    ON psp_advisory_decision(model_name, model_version, outcome);
"""

# =============================================================================
# Common Queries
# =============================================================================

QUERY_OVERRIDES_LAST_WEEK = """
SELECT
    record_id,
    advisory_id,
    advisory_type,
    tenant_id,
    suggested_outcome,
    actual_outcome,
    override_reason,
    decided_by,
    confidence,
    decision_made_at
FROM psp_advisory_decision
WHERE outcome = 'overridden'
  AND decision_made_at >= NOW() - INTERVAL '7 days'
ORDER BY decision_made_at DESC;
"""

QUERY_MODEL_ACCURACY = """
SELECT
    model_name,
    model_version,
    COUNT(*) AS total_decisions,
    SUM(CASE WHEN outcome IN ('accepted', 'auto_applied') THEN 1 ELSE 0 END) AS correct,
    SUM(CASE WHEN outcome = 'overridden' THEN 1 ELSE 0 END) AS overridden,
    ROUND(
        100.0 * SUM(CASE WHEN outcome IN ('accepted', 'auto_applied') THEN 1 ELSE 0 END) /
        NULLIF(COUNT(*), 0),
        2
    ) AS accuracy_pct
FROM psp_advisory_decision
WHERE outcome != 'pending'
  AND advisory_generated_at >= NOW() - INTERVAL '30 days'
GROUP BY model_name, model_version
ORDER BY model_name, model_version;
"""

QUERY_OVERRIDE_REASONS = """
SELECT
    advisory_type,
    override_reason,
    COUNT(*) AS count,
    AVG(confidence) AS avg_confidence_when_overridden
FROM psp_advisory_decision
WHERE outcome = 'overridden'
  AND decision_made_at >= NOW() - INTERVAL '30 days'
GROUP BY advisory_type, override_reason
ORDER BY count DESC;
"""
