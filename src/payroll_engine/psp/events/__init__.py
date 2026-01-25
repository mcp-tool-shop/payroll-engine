"""PSP domain events package.

This package provides:
- Typed domain events for all PSP operations
- Event emitter for publishing events
- Event store for persistence and replay
- Event handlers for routing
"""

from payroll_engine.psp.events.types import (
    # Base
    DomainEvent,
    EventMetadata,
    EventCategory,
    # Funding Events
    FundingRequested,
    FundingApproved,
    FundingBlocked,
    FundingInsufficientFunds,
    # Payment Events
    PaymentInstructionCreated,
    PaymentSubmitted,
    PaymentAccepted,
    PaymentSettled,
    PaymentFailed,
    PaymentReturned,
    PaymentCanceled,
    # Ledger Events
    LedgerEntryPosted,
    LedgerEntryReversed,
    # Settlement Events
    SettlementReceived,
    SettlementMatched,
    SettlementUnmatched,
    SettlementStatusChanged,
    # Liability Events
    LiabilityClassified,
    LiabilityRecoveryStarted,
    LiabilityRecovered,
    LiabilityWrittenOff,
    # Reconciliation Events
    ReconciliationStarted,
    ReconciliationCompleted,
    ReconciliationFailed,
)
from payroll_engine.psp.events.emitter import (
    EventEmitter,
    AsyncEventEmitter,
    EventHandler,
    AsyncEventHandler,
)
from payroll_engine.psp.events.store import (
    EventStore,
    AsyncEventStore,
    StoredEvent,
)

__all__ = [
    # Base
    "DomainEvent",
    "EventMetadata",
    "EventCategory",
    # Funding Events
    "FundingRequested",
    "FundingApproved",
    "FundingBlocked",
    "FundingInsufficientFunds",
    # Payment Events
    "PaymentInstructionCreated",
    "PaymentSubmitted",
    "PaymentAccepted",
    "PaymentSettled",
    "PaymentFailed",
    "PaymentReturned",
    "PaymentCanceled",
    # Ledger Events
    "LedgerEntryPosted",
    "LedgerEntryReversed",
    # Settlement Events
    "SettlementReceived",
    "SettlementMatched",
    "SettlementUnmatched",
    "SettlementStatusChanged",
    # Liability Events
    "LiabilityClassified",
    "LiabilityRecoveryStarted",
    "LiabilityRecovered",
    "LiabilityWrittenOff",
    # Reconciliation Events
    "ReconciliationStarted",
    "ReconciliationCompleted",
    "ReconciliationFailed",
    # Emitter
    "EventEmitter",
    "AsyncEventEmitter",
    "EventHandler",
    "AsyncEventHandler",
    # Store
    "EventStore",
    "AsyncEventStore",
    "StoredEvent",
]
