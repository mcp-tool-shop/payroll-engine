"""Tests for PSP domain events system.

Tests verify:
1. Event types are properly structured and serializable
2. Event emitter routes to correct handlers
3. Event batching works for transactional boundaries
4. Handler errors are isolated
5. Async event emission works correctly
"""

import asyncio
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from payroll_engine.psp.events.types import (
    EventMetadata,
    EventCategory,
    # Funding events
    FundingRequested,
    FundingApproved,
    FundingBlocked,
    FundingInsufficientFunds,
    # Payment events
    PaymentInstructionCreated,
    PaymentSubmitted,
    PaymentSettled,
    PaymentFailed,
    PaymentReturned,
    # Ledger events
    LedgerEntryPosted,
    LedgerEntryReversed,
    # Settlement events
    SettlementReceived,
    SettlementStatusChanged,
    # Liability events
    LiabilityClassified,
    # Reconciliation events
    ReconciliationStarted,
    ReconciliationCompleted,
)
from payroll_engine.psp.events.emitter import (
    EventEmitter,
    AsyncEventEmitter,
)


class TestEventMetadata:
    """Test EventMetadata creation and properties."""

    def test_create_metadata_auto_generates_fields(self):
        """Create generates event_id, timestamp, correlation_id."""
        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        assert meta.event_id is not None
        assert meta.timestamp is not None
        assert meta.tenant_id == tenant_id
        assert meta.correlation_id is not None
        assert meta.causation_id is None
        assert meta.actor_type == "system"
        assert meta.source_service == "psp"
        assert meta.version == 1

    def test_create_metadata_with_custom_values(self):
        """Create respects provided values."""
        tenant_id = uuid4()
        correlation_id = uuid4()
        causation_id = uuid4()
        actor_id = uuid4()

        meta = EventMetadata.create(
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            causation_id=causation_id,
            actor_id=actor_id,
            actor_type="user",
            source_service="api",
        )

        assert meta.tenant_id == tenant_id
        assert meta.correlation_id == correlation_id
        assert meta.causation_id == causation_id
        assert meta.actor_id == actor_id
        assert meta.actor_type == "user"
        assert meta.source_service == "api"


class TestFundingEvents:
    """Test funding domain events."""

    def test_funding_requested_event(self):
        """FundingRequested event structure."""
        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)
        funding_id = uuid4()
        legal_entity_id = uuid4()
        pay_period_id = uuid4()

        event = FundingRequested(
            metadata=meta,
            funding_request_id=funding_id,
            legal_entity_id=legal_entity_id,
            pay_period_id=pay_period_id,
            requested_amount=Decimal("50000.00"),
            currency="USD",
            requested_date=date(2025, 1, 15),
        )

        assert event.event_type == "FundingRequested"
        assert event.category == EventCategory.FUNDING
        assert event.funding_request_id == funding_id
        assert event.requested_amount == Decimal("50000.00")

    def test_funding_blocked_event(self):
        """FundingBlocked event captures block reason."""
        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        event = FundingBlocked(
            metadata=meta,
            funding_request_id=uuid4(),
            legal_entity_id=uuid4(),
            requested_amount=Decimal("100000.00"),
            available_balance=Decimal("50000.00"),
            block_reason="Exceeds credit limit",
            policy_violated="max_credit_utilization",
            gate_evaluation_id=uuid4(),
        )

        assert event.event_type == "FundingBlocked"
        assert event.category == EventCategory.FUNDING
        assert event.block_reason == "Exceeds credit limit"
        assert event.policy_violated == "max_credit_utilization"

    def test_funding_event_serialization(self):
        """Events serialize to dict and JSON."""
        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        event = FundingInsufficientFunds(
            metadata=meta,
            funding_request_id=uuid4(),
            legal_entity_id=uuid4(),
            requested_amount=Decimal("75000.00"),
            available_balance=Decimal("50000.00"),
            shortfall=Decimal("25000.00"),
            gate_evaluation_id=uuid4(),
        )

        data = event.to_dict()
        assert "metadata" in data
        assert data["requested_amount"] == "75000.00"
        assert data["shortfall"] == "25000.00"

        json_str = event.to_json()
        assert "FundingInsufficientFunds" not in json_str  # Type not in payload
        assert "75000.00" in json_str


class TestPaymentEvents:
    """Test payment domain events."""

    def test_payment_instruction_created(self):
        """PaymentInstructionCreated event."""
        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        event = PaymentInstructionCreated(
            metadata=meta,
            payment_instruction_id=uuid4(),
            legal_entity_id=uuid4(),
            purpose="employee_net",
            direction="outbound",
            amount=Decimal("2500.00"),
            currency="USD",
            payee_type="employee",
            payee_ref_id=uuid4(),
            source_type="pay_statement",
            source_id=uuid4(),
        )

        assert event.event_type == "PaymentInstructionCreated"
        assert event.category == EventCategory.PAYMENT
        assert event.purpose == "employee_net"

    def test_payment_settled_event(self):
        """PaymentSettled event."""
        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        event = PaymentSettled(
            metadata=meta,
            payment_instruction_id=uuid4(),
            settlement_event_id=uuid4(),
            amount=Decimal("2500.00"),
            currency="USD",
            effective_date=date(2025, 1, 16),
            external_trace_id="ACH123456789",
        )

        assert event.event_type == "PaymentSettled"
        assert event.category == EventCategory.PAYMENT
        assert event.external_trace_id == "ACH123456789"

    def test_payment_returned_event(self):
        """PaymentReturned event captures return details."""
        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        event = PaymentReturned(
            metadata=meta,
            payment_instruction_id=uuid4(),
            settlement_event_id=uuid4(),
            amount=Decimal("2500.00"),
            return_code="R01",
            return_reason="Insufficient Funds",
            return_date=date(2025, 1, 18),
            original_settlement_date=date(2025, 1, 16),
            liability_party="employer",
        )

        assert event.event_type == "PaymentReturned"
        assert event.category == EventCategory.PAYMENT
        assert event.return_code == "R01"
        assert event.liability_party == "employer"


class TestEventEmitter:
    """Test synchronous event emitter."""

    def test_emit_to_type_handler(self):
        """Emitter routes to type-specific handler."""
        emitter = EventEmitter()
        received = []

        def handler(event):
            received.append(event)

        emitter.on(PaymentSettled, handler)

        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        event = PaymentSettled(
            metadata=meta,
            payment_instruction_id=uuid4(),
            settlement_event_id=uuid4(),
            amount=Decimal("1000.00"),
            currency="USD",
            effective_date=date.today(),
            external_trace_id="TEST123",
        )

        emitter.emit(event)

        assert len(received) == 1
        assert received[0] is event

    def test_emit_does_not_route_to_wrong_handler(self):
        """Handler for different type doesn't receive event."""
        emitter = EventEmitter()
        received = []

        def handler(event):
            received.append(event)

        emitter.on(PaymentFailed, handler)  # Different type

        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        event = PaymentSettled(
            metadata=meta,
            payment_instruction_id=uuid4(),
            settlement_event_id=uuid4(),
            amount=Decimal("1000.00"),
            currency="USD",
            effective_date=date.today(),
            external_trace_id="TEST123",
        )

        emitter.emit(event)

        assert len(received) == 0

    def test_emit_to_category_handler(self):
        """Emitter routes to category handler."""
        emitter = EventEmitter()
        received = []

        def handler(event):
            received.append(event)

        emitter.on_category(EventCategory.PAYMENT, handler)

        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        # Emit multiple payment events
        emitter.emit(PaymentSettled(
            metadata=meta,
            payment_instruction_id=uuid4(),
            settlement_event_id=uuid4(),
            amount=Decimal("1000.00"),
            currency="USD",
            effective_date=date.today(),
            external_trace_id="TEST1",
        ))

        emitter.emit(PaymentFailed(
            metadata=meta,
            payment_instruction_id=uuid4(),
            payment_attempt_id=uuid4(),
            provider="ach_stub",
            failure_reason="Rejected",
            failure_code="R01",
            is_retryable=False,
            error_origin="bank",
        ))

        assert len(received) == 2

    def test_emit_to_all_handler(self):
        """on_all handler receives all events."""
        emitter = EventEmitter()
        received = []

        emitter.on_all(lambda e: received.append(e))

        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        emitter.emit(FundingApproved(
            metadata=meta,
            funding_request_id=uuid4(),
            legal_entity_id=uuid4(),
            approved_amount=Decimal("50000.00"),
            available_balance=Decimal("100000.00"),
            gate_evaluation_id=uuid4(),
        ))

        emitter.emit(LedgerEntryPosted(
            metadata=meta,
            ledger_entry_id=uuid4(),
            legal_entity_id=uuid4(),
            entry_type="funding_received",
            debit_account_id=uuid4(),
            credit_account_id=uuid4(),
            amount=Decimal("50000.00"),
            currency="USD",
            source_type="funding_request",
            source_id=uuid4(),
        ))

        assert len(received) == 2

    def test_handler_error_isolation(self):
        """Handler errors don't stop other handlers."""
        emitter = EventEmitter()
        received = []

        def failing_handler(event):
            raise ValueError("Handler failed")

        def working_handler(event):
            received.append(event)

        emitter.on_all(failing_handler)
        emitter.on_all(working_handler)

        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        event = ReconciliationStarted(
            metadata=meta,
            reconciliation_id=uuid4(),
            reconciliation_date=date.today(),
            bank_account_id=uuid4(),
            provider="ach_stub",
        )

        errors = emitter.emit(event)

        # Working handler still received the event
        assert len(received) == 1
        # Error was captured
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)

    def test_batch_holds_events(self):
        """Batch context holds events until exit."""
        emitter = EventEmitter()
        received = []

        emitter.on_all(lambda e: received.append(e))

        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        with emitter.batch() as batch:
            batch.add(ReconciliationStarted(
                metadata=meta,
                reconciliation_id=uuid4(),
                reconciliation_date=date.today(),
                bank_account_id=uuid4(),
                provider="ach_stub",
            ))
            # Not yet emitted
            assert len(received) == 0

            batch.add(ReconciliationCompleted(
                metadata=meta,
                reconciliation_id=uuid4(),
                reconciliation_date=date.today(),
                records_processed=100,
                records_matched=95,
                records_created=5,
                records_failed=0,
                unmatched_count=2,
            ))
            # Still not emitted
            assert len(received) == 0

        # Now both are emitted
        assert len(received) == 2

    def test_batch_discards_on_exception(self):
        """Batch discards events if exception occurs."""
        emitter = EventEmitter()
        received = []

        emitter.on_all(lambda e: received.append(e))

        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        try:
            with emitter.batch() as batch:
                batch.add(ReconciliationStarted(
                    metadata=meta,
                    reconciliation_id=uuid4(),
                    reconciliation_date=date.today(),
                    bank_account_id=uuid4(),
                    provider="ach_stub",
                ))
                raise RuntimeError("Transaction failed")
        except RuntimeError:
            pass

        # Events were discarded
        assert len(received) == 0

    def test_unregister_handler(self):
        """off() removes handler."""
        emitter = EventEmitter()
        received = []

        def handler(event):
            received.append(event)

        emitter.on_all(handler)
        emitter.off(handler)

        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        emitter.emit(ReconciliationStarted(
            metadata=meta,
            reconciliation_id=uuid4(),
            reconciliation_date=date.today(),
            bank_account_id=uuid4(),
            provider="ach_stub",
        ))

        assert len(received) == 0


class TestAsyncEventEmitter:
    """Test asynchronous event emitter."""

    @pytest.mark.asyncio
    async def test_async_emit_to_handler(self):
        """Async emitter routes to async handler."""
        emitter = AsyncEventEmitter()
        received = []

        async def handler(event):
            await asyncio.sleep(0.01)  # Simulate async work
            received.append(event)

        emitter.on(SettlementReceived, handler)

        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        event = SettlementReceived(
            metadata=meta,
            settlement_event_id=uuid4(),
            bank_account_id=uuid4(),
            rail="ach",
            direction="outbound",
            amount=Decimal("5000.00"),
            currency="USD",
            external_trace_id="ACH987654321",
            effective_date=date.today(),
            status="settled",
        )

        await emitter.emit(event)

        assert len(received) == 1
        assert received[0] is event

    @pytest.mark.asyncio
    async def test_async_parallel_handlers(self):
        """Async handlers run in parallel."""
        emitter = AsyncEventEmitter()
        order = []

        async def slow_handler(event):
            order.append("slow_start")
            await asyncio.sleep(0.05)
            order.append("slow_end")

        async def fast_handler(event):
            order.append("fast_start")
            await asyncio.sleep(0.01)
            order.append("fast_end")

        emitter.on_all(slow_handler)
        emitter.on_all(fast_handler)

        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        await emitter.emit(SettlementStatusChanged(
            metadata=meta,
            settlement_event_id=uuid4(),
            previous_status="accepted",
            new_status="settled",
            change_reason=None,
            return_code=None,
            requires_reversal=False,
        ))

        # Fast should finish before slow due to parallel execution
        assert "fast_end" in order
        assert order.index("fast_end") < order.index("slow_end")

    @pytest.mark.asyncio
    async def test_async_handler_error_isolation(self):
        """Async handler errors don't stop other handlers."""
        emitter = AsyncEventEmitter()
        received = []

        async def failing_handler(event):
            raise ValueError("Async handler failed")

        async def working_handler(event):
            received.append(event)

        emitter.on_all(failing_handler)
        emitter.on_all(working_handler)

        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        errors = await emitter.emit(LiabilityClassified(
            metadata=meta,
            liability_event_id=uuid4(),
            payment_instruction_id=uuid4(),
            settlement_event_id=None,
            error_origin="bank",
            liability_party="employer",
            recovery_path="offset_future",
            amount=Decimal("2500.00"),
            return_code="R01",
            classification_reason="Insufficient funds - employer responsible",
        ))

        assert len(received) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)


class TestEventSerialization:
    """Test event serialization for persistence."""

    def test_all_event_types_serialize(self):
        """All event types can serialize to dict and JSON."""
        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        events = [
            FundingRequested(
                metadata=meta,
                funding_request_id=uuid4(),
                legal_entity_id=uuid4(),
                pay_period_id=uuid4(),
                requested_amount=Decimal("50000.00"),
                currency="USD",
                requested_date=date.today(),
            ),
            PaymentSettled(
                metadata=meta,
                payment_instruction_id=uuid4(),
                settlement_event_id=uuid4(),
                amount=Decimal("2500.00"),
                currency="USD",
                effective_date=date.today(),
                external_trace_id="TEST",
            ),
            LedgerEntryReversed(
                metadata=meta,
                reversal_entry_id=uuid4(),
                original_entry_id=uuid4(),
                legal_entity_id=uuid4(),
                amount=Decimal("1000.00"),
                reversal_reason="Payment returned",
                source_type="psp_settlement_event",
                source_id=uuid4(),
            ),
        ]

        for event in events:
            data = event.to_dict()
            assert isinstance(data, dict)
            assert "metadata" in data

            json_str = event.to_json()
            assert isinstance(json_str, str)
            assert len(json_str) > 0

    def test_decimal_serialization(self):
        """Decimals serialize as strings to preserve precision."""
        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)

        event = PaymentSettled(
            metadata=meta,
            payment_instruction_id=uuid4(),
            settlement_event_id=uuid4(),
            amount=Decimal("12345.67"),
            currency="USD",
            effective_date=date.today(),
            external_trace_id="TEST",
        )

        data = event.to_dict()
        assert data["amount"] == "12345.67"
        assert isinstance(data["amount"], str)

    def test_uuid_serialization(self):
        """UUIDs serialize as strings."""
        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)
        instruction_id = uuid4()

        event = PaymentSettled(
            metadata=meta,
            payment_instruction_id=instruction_id,
            settlement_event_id=uuid4(),
            amount=Decimal("1000.00"),
            currency="USD",
            effective_date=date.today(),
            external_trace_id="TEST",
        )

        data = event.to_dict()
        assert data["payment_instruction_id"] == str(instruction_id)
        assert isinstance(data["payment_instruction_id"], str)

    def test_date_serialization(self):
        """Dates serialize as ISO strings."""
        tenant_id = uuid4()
        meta = EventMetadata.create(tenant_id=tenant_id)
        test_date = date(2025, 1, 15)

        event = PaymentSettled(
            metadata=meta,
            payment_instruction_id=uuid4(),
            settlement_event_id=uuid4(),
            amount=Decimal("1000.00"),
            currency="USD",
            effective_date=test_date,
            external_trace_id="TEST",
        )

        data = event.to_dict()
        assert data["effective_date"] == "2025-01-15"
