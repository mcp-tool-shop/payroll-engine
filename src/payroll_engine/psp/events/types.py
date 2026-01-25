"""Domain event types for PSP operations.

All events are:
- Immutable (frozen dataclasses)
- Typed with explicit payloads
- Traceable via metadata
- Serializable for persistence and replay

Events are the source of truth for what happened. They enable:
- Audit trails
- Compliance alerts
- Client notifications
- Support tooling
- Deterministic replay
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4


class EventCategory(str, Enum):
    """Event categories for routing and filtering."""

    FUNDING = "funding"
    PAYMENT = "payment"
    LEDGER = "ledger"
    SETTLEMENT = "settlement"
    LIABILITY = "liability"
    RECONCILIATION = "reconciliation"


@dataclass(frozen=True)
class EventMetadata:
    """Metadata attached to every domain event.

    Provides traceability and replay capability.
    """

    event_id: UUID
    timestamp: datetime
    tenant_id: UUID
    correlation_id: UUID  # Links related events
    causation_id: UUID | None  # Event that caused this one
    actor_id: UUID | None  # User or system that triggered
    actor_type: str  # 'user', 'system', 'scheduler', 'webhook'
    source_service: str  # Service that emitted
    version: int = 1  # Schema version for evolution

    @classmethod
    def create(
        cls,
        tenant_id: UUID,
        correlation_id: UUID | None = None,
        causation_id: UUID | None = None,
        actor_id: UUID | None = None,
        actor_type: str = "system",
        source_service: str = "psp",
    ) -> EventMetadata:
        """Create metadata with auto-generated fields."""
        return cls(
            event_id=uuid4(),
            timestamp=datetime.utcnow(),
            tenant_id=tenant_id,
            correlation_id=correlation_id or uuid4(),
            causation_id=causation_id,
            actor_id=actor_id,
            actor_type=actor_type,
            source_service=source_service,
        )


@dataclass(frozen=True)
class DomainEvent:
    """Base class for all domain events.

    All events must be:
    - Immutable (frozen)
    - Self-describing (event_type)
    - Traceable (metadata)
    - Serializable (to_dict/from_dict)
    """

    metadata: EventMetadata

    @property
    def event_type(self) -> str:
        """Event type name for routing."""
        return self.__class__.__name__

    @property
    def category(self) -> EventCategory:
        """Event category for filtering."""
        raise NotImplementedError("Subclasses must define category")

    def to_dict(self) -> dict[str, Any]:
        """Serialize event to dictionary."""
        data = asdict(self)
        # Convert UUIDs and other types
        return _serialize_dict(data)

    def to_json(self) -> str:
        """Serialize event to JSON string."""
        return json.dumps(self.to_dict(), default=str)


def _serialize_dict(obj: Any) -> Any:
    """Recursively serialize objects for JSON compatibility."""
    if isinstance(obj, dict):
        return {k: _serialize_dict(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_serialize_dict(v) for v in obj]
    elif isinstance(obj, UUID):
        return str(obj)
    elif isinstance(obj, datetime):
        return obj.isoformat()
    elif isinstance(obj, date):
        return obj.isoformat()
    elif isinstance(obj, Decimal):
        return str(obj)
    elif isinstance(obj, Enum):
        return obj.value
    return obj


# =============================================================================
# Funding Events
# =============================================================================


@dataclass(frozen=True)
class FundingRequested(DomainEvent):
    """A funding request was submitted for evaluation."""

    funding_request_id: UUID
    legal_entity_id: UUID
    pay_period_id: UUID
    requested_amount: Decimal
    currency: str
    requested_date: date

    @property
    def category(self) -> EventCategory:
        return EventCategory.FUNDING


@dataclass(frozen=True)
class FundingApproved(DomainEvent):
    """Funding request passed both gates and was approved."""

    funding_request_id: UUID
    legal_entity_id: UUID
    approved_amount: Decimal
    available_balance: Decimal
    gate_evaluation_id: UUID

    @property
    def category(self) -> EventCategory:
        return EventCategory.FUNDING


@dataclass(frozen=True)
class FundingBlocked(DomainEvent):
    """Funding request was blocked by policy gate."""

    funding_request_id: UUID
    legal_entity_id: UUID
    requested_amount: Decimal
    available_balance: Decimal
    block_reason: str
    policy_violated: str | None
    gate_evaluation_id: UUID

    @property
    def category(self) -> EventCategory:
        return EventCategory.FUNDING


@dataclass(frozen=True)
class FundingInsufficientFunds(DomainEvent):
    """Funding request failed due to insufficient funds (pay gate)."""

    funding_request_id: UUID
    legal_entity_id: UUID
    requested_amount: Decimal
    available_balance: Decimal
    shortfall: Decimal
    gate_evaluation_id: UUID

    @property
    def category(self) -> EventCategory:
        return EventCategory.FUNDING


# =============================================================================
# Payment Events
# =============================================================================


@dataclass(frozen=True)
class PaymentInstructionCreated(DomainEvent):
    """A payment instruction was created."""

    payment_instruction_id: UUID
    legal_entity_id: UUID
    purpose: str
    direction: str
    amount: Decimal
    currency: str
    payee_type: str
    payee_ref_id: UUID
    source_type: str
    source_id: UUID

    @property
    def category(self) -> EventCategory:
        return EventCategory.PAYMENT


@dataclass(frozen=True)
class PaymentSubmitted(DomainEvent):
    """Payment was submitted to a rail provider."""

    payment_instruction_id: UUID
    payment_attempt_id: UUID
    rail: str
    provider: str
    provider_request_id: str
    estimated_settlement_date: date | None

    @property
    def category(self) -> EventCategory:
        return EventCategory.PAYMENT


@dataclass(frozen=True)
class PaymentAccepted(DomainEvent):
    """Provider accepted the payment."""

    payment_instruction_id: UUID
    payment_attempt_id: UUID
    provider: str
    provider_request_id: str
    trace_id: str | None
    estimated_settlement_date: date | None

    @property
    def category(self) -> EventCategory:
        return EventCategory.PAYMENT


@dataclass(frozen=True)
class PaymentSettled(DomainEvent):
    """Payment has settled (funds delivered)."""

    payment_instruction_id: UUID
    settlement_event_id: UUID
    amount: Decimal
    currency: str
    effective_date: date
    external_trace_id: str

    @property
    def category(self) -> EventCategory:
        return EventCategory.PAYMENT


@dataclass(frozen=True)
class PaymentFailed(DomainEvent):
    """Payment failed during processing."""

    payment_instruction_id: UUID
    payment_attempt_id: UUID | None
    provider: str | None
    failure_reason: str
    failure_code: str | None
    is_retryable: bool
    error_origin: str | None  # client, payroll_engine, provider, bank, recipient

    @property
    def category(self) -> EventCategory:
        return EventCategory.PAYMENT


@dataclass(frozen=True)
class PaymentReturned(DomainEvent):
    """Payment was returned after settlement."""

    payment_instruction_id: UUID
    settlement_event_id: UUID
    amount: Decimal
    return_code: str
    return_reason: str
    return_date: date
    original_settlement_date: date
    liability_party: str | None

    @property
    def category(self) -> EventCategory:
        return EventCategory.PAYMENT


@dataclass(frozen=True)
class PaymentCanceled(DomainEvent):
    """Payment was canceled before settlement."""

    payment_instruction_id: UUID
    canceled_by: str  # user, system, provider
    cancel_reason: str
    was_submitted: bool

    @property
    def category(self) -> EventCategory:
        return EventCategory.PAYMENT


# =============================================================================
# Ledger Events
# =============================================================================


@dataclass(frozen=True)
class LedgerEntryPosted(DomainEvent):
    """A ledger entry was posted."""

    ledger_entry_id: UUID
    legal_entity_id: UUID
    entry_type: str
    debit_account_id: UUID
    credit_account_id: UUID
    amount: Decimal
    currency: str
    source_type: str
    source_id: UUID

    @property
    def category(self) -> EventCategory:
        return EventCategory.LEDGER


@dataclass(frozen=True)
class LedgerEntryReversed(DomainEvent):
    """A ledger entry was reversed."""

    reversal_entry_id: UUID
    original_entry_id: UUID
    legal_entity_id: UUID
    amount: Decimal
    reversal_reason: str
    source_type: str
    source_id: UUID

    @property
    def category(self) -> EventCategory:
        return EventCategory.LEDGER


# =============================================================================
# Settlement Events
# =============================================================================


@dataclass(frozen=True)
class SettlementReceived(DomainEvent):
    """Settlement record received from provider."""

    settlement_event_id: UUID
    bank_account_id: UUID
    rail: str
    direction: str
    amount: Decimal
    currency: str
    external_trace_id: str
    effective_date: date
    status: str

    @property
    def category(self) -> EventCategory:
        return EventCategory.SETTLEMENT


@dataclass(frozen=True)
class SettlementMatched(DomainEvent):
    """Settlement was matched to a payment instruction."""

    settlement_event_id: UUID
    payment_instruction_id: UUID
    payment_attempt_id: UUID
    match_method: str  # trace_id, amount_date, manual

    @property
    def category(self) -> EventCategory:
        return EventCategory.SETTLEMENT


@dataclass(frozen=True)
class SettlementUnmatched(DomainEvent):
    """Settlement could not be matched to any instruction."""

    settlement_event_id: UUID
    external_trace_id: str
    amount: Decimal
    direction: str
    reason: str  # no_trace_match, ambiguous_match, orphan

    @property
    def category(self) -> EventCategory:
        return EventCategory.SETTLEMENT


@dataclass(frozen=True)
class SettlementStatusChanged(DomainEvent):
    """Settlement status changed (e.g., settled -> returned)."""

    settlement_event_id: UUID
    previous_status: str
    new_status: str
    change_reason: str | None
    return_code: str | None
    requires_reversal: bool

    @property
    def category(self) -> EventCategory:
        return EventCategory.SETTLEMENT


# =============================================================================
# Liability Events
# =============================================================================


@dataclass(frozen=True)
class LiabilityClassified(DomainEvent):
    """A return/failure was classified for liability."""

    liability_event_id: UUID
    payment_instruction_id: UUID | None
    settlement_event_id: UUID | None
    error_origin: str
    liability_party: str
    recovery_path: str
    amount: Decimal
    return_code: str | None
    classification_reason: str

    @property
    def category(self) -> EventCategory:
        return EventCategory.LIABILITY


@dataclass(frozen=True)
class LiabilityRecoveryStarted(DomainEvent):
    """Recovery process started for a liability."""

    liability_event_id: UUID
    recovery_path: str
    recovery_method: str  # offset, clawback, dispute, insurance_claim
    target_amount: Decimal

    @property
    def category(self) -> EventCategory:
        return EventCategory.LIABILITY


@dataclass(frozen=True)
class LiabilityRecovered(DomainEvent):
    """Liability was successfully recovered."""

    liability_event_id: UUID
    recovered_amount: Decimal
    recovery_method: str
    recovery_reference: str | None  # e.g., offset payroll_id, insurance claim #

    @property
    def category(self) -> EventCategory:
        return EventCategory.LIABILITY


@dataclass(frozen=True)
class LiabilityWrittenOff(DomainEvent):
    """Liability was written off as unrecoverable."""

    liability_event_id: UUID
    written_off_amount: Decimal
    write_off_reason: str
    approved_by: UUID | None
    accounting_reference: str | None

    @property
    def category(self) -> EventCategory:
        return EventCategory.LIABILITY


# =============================================================================
# Reconciliation Events
# =============================================================================


@dataclass(frozen=True)
class ReconciliationStarted(DomainEvent):
    """Reconciliation job started."""

    reconciliation_id: UUID
    reconciliation_date: date
    bank_account_id: UUID
    provider: str

    @property
    def category(self) -> EventCategory:
        return EventCategory.RECONCILIATION


@dataclass(frozen=True)
class ReconciliationCompleted(DomainEvent):
    """Reconciliation job completed successfully."""

    reconciliation_id: UUID
    reconciliation_date: date
    records_processed: int
    records_matched: int
    records_created: int
    records_failed: int
    unmatched_count: int

    @property
    def category(self) -> EventCategory:
        return EventCategory.RECONCILIATION


@dataclass(frozen=True)
class ReconciliationFailed(DomainEvent):
    """Reconciliation job failed."""

    reconciliation_id: UUID
    reconciliation_date: date
    error_code: str
    error_message: str
    records_processed_before_failure: int

    @property
    def category(self) -> EventCategory:
        return EventCategory.RECONCILIATION
