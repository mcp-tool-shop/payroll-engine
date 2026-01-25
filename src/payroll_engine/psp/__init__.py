"""PSP (Payment Service Provider) operations package.

This package contains services for:
- Ledger operations (append-only double-entry)
- Funding gate evaluations
- Payment orchestration
- Rail provider adapters
- Reconciliation jobs
- Liability attribution
- Domain events
"""

from payroll_engine.psp.services import (
    # Ledger
    LedgerService,
    AsyncLedgerService,
    Balance,
    PostResult,
    # Funding Gate
    FundingGateService,
    AsyncFundingGateService,
    GateResult,
    FundingRequirement,
    # Payment Orchestrator
    PaymentOrchestrator,
    AsyncPaymentOrchestrator,
    InstructionResult,
    SubmissionResult,
    # Reconciliation
    ReconciliationService,
    AsyncReconciliationService,
    ReconciliationResult,
)
from payroll_engine.psp.services.liability import (
    LiabilityService,
    AsyncLiabilityService,
    LiabilityClassification,
    LiabilityEvent,
    ErrorOrigin,
    LiabilityParty,
    RecoveryPath,
    RecoveryStatus,
)
from payroll_engine.psp.providers.base import (
    PaymentRailProvider,
    RailCapabilities,
    SubmitResult as ProviderSubmitResult,
    StatusResult,
    CancelResult,
    SettlementRecord,
)
from payroll_engine.psp.providers.ach_stub import AchStubProvider
from payroll_engine.psp.providers.fednow_stub import FedNowStubProvider
from payroll_engine.psp.events import (
    # Base types
    DomainEvent,
    EventMetadata,
    EventCategory,
    # Event emitter
    EventEmitter,
    AsyncEventEmitter,
    EventHandler,
    AsyncEventHandler,
    # Event store
    EventStore,
    AsyncEventStore,
    StoredEvent,
    # Funding events
    FundingRequested,
    FundingApproved,
    FundingBlocked,
    FundingInsufficientFunds,
    # Payment events
    PaymentInstructionCreated,
    PaymentSubmitted,
    PaymentAccepted,
    PaymentSettled,
    PaymentFailed,
    PaymentReturned,
    PaymentCanceled,
    # Ledger events
    LedgerEntryPosted,
    LedgerEntryReversed,
    # Settlement events
    SettlementReceived,
    SettlementMatched,
    SettlementUnmatched,
    SettlementStatusChanged,
    # Liability events
    LiabilityClassified,
    LiabilityRecoveryStarted,
    LiabilityRecovered,
    LiabilityWrittenOff,
    # Reconciliation events
    ReconciliationStarted,
    ReconciliationCompleted,
    ReconciliationFailed,
)

__all__ = [
    # Ledger Service
    "LedgerService",
    "AsyncLedgerService",
    "Balance",
    "PostResult",
    # Funding Gate
    "FundingGateService",
    "AsyncFundingGateService",
    "GateResult",
    "FundingRequirement",
    # Payment Orchestrator
    "PaymentOrchestrator",
    "AsyncPaymentOrchestrator",
    "InstructionResult",
    "SubmissionResult",
    # Reconciliation
    "ReconciliationService",
    "AsyncReconciliationService",
    "ReconciliationResult",
    # Liability
    "LiabilityService",
    "AsyncLiabilityService",
    "LiabilityClassification",
    "LiabilityEvent",
    "ErrorOrigin",
    "LiabilityParty",
    "RecoveryPath",
    "RecoveryStatus",
    # Provider Protocol and Types
    "PaymentRailProvider",
    "RailCapabilities",
    "ProviderSubmitResult",
    "StatusResult",
    "CancelResult",
    "SettlementRecord",
    # Stub Providers
    "AchStubProvider",
    "FedNowStubProvider",
    # Events - Base
    "DomainEvent",
    "EventMetadata",
    "EventCategory",
    # Events - Emitter
    "EventEmitter",
    "AsyncEventEmitter",
    "EventHandler",
    "AsyncEventHandler",
    # Events - Store
    "EventStore",
    "AsyncEventStore",
    "StoredEvent",
    # Events - Funding
    "FundingRequested",
    "FundingApproved",
    "FundingBlocked",
    "FundingInsufficientFunds",
    # Events - Payment
    "PaymentInstructionCreated",
    "PaymentSubmitted",
    "PaymentAccepted",
    "PaymentSettled",
    "PaymentFailed",
    "PaymentReturned",
    "PaymentCanceled",
    # Events - Ledger
    "LedgerEntryPosted",
    "LedgerEntryReversed",
    # Events - Settlement
    "SettlementReceived",
    "SettlementMatched",
    "SettlementUnmatched",
    "SettlementStatusChanged",
    # Events - Liability
    "LiabilityClassified",
    "LiabilityRecoveryStarted",
    "LiabilityRecovered",
    "LiabilityWrittenOff",
    # Events - Reconciliation
    "ReconciliationStarted",
    "ReconciliationCompleted",
    "ReconciliationFailed",
]
