#!/usr/bin/env python
"""PSP Minimal Example - Library-first demonstration.

This example shows how to use PSP as a library:
1. Create explicit configuration (no magic, no env vars)
2. Instantiate the PSP facade
3. Call facade methods directly
4. Simulate provider behavior (stubs)
5. Demonstrate replay and reconciliation

This is NOT:
- An HTTP service
- A background worker
- A daemon

This IS:
- How Stripe internally uses Stripe
- The mental model integrators should adopt

Usage:
    python main.py --database-url postgresql://payroll:payroll_dev@localhost:5432/payroll_dev

    # Dry run (no database):
    python main.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Iterator
from uuid import UUID, uuid4


# =============================================================================
# Configuration Objects (Explicit, No Magic)
# =============================================================================

@dataclass(frozen=True)
class LedgerConfig:
    """Ledger behavior configuration."""
    require_balanced_entries: bool = True
    allow_negative_balances: bool = False


@dataclass(frozen=True)
class FundingGateConfig:
    """Funding gate thresholds and behavior."""
    commit_gate_enabled: bool = True
    pay_gate_enabled: bool = True  # ALWAYS True in production
    reservation_ttl_hours: int = 48


@dataclass(frozen=True)
class ProviderConfig:
    """Payment provider configuration."""
    name: str
    provider_type: str  # "ach", "fednow", "wire"
    sandbox: bool = True


@dataclass(frozen=True)
class EventStoreConfig:
    """Event store configuration."""
    retention_days: int | None = None  # None = forever
    enable_replay: bool = True


@dataclass(frozen=True)
class PSPConfig:
    """
    Explicit configuration for PSP.

    No defaults that move money.
    No env vars.
    No hidden behavior.
    """
    tenant_id: UUID
    legal_entity_id: UUID
    ledger: LedgerConfig
    funding_gate: FundingGateConfig
    providers: list[ProviderConfig]
    event_store: EventStoreConfig


# =============================================================================
# Domain Types
# =============================================================================

@dataclass
class Employee:
    """Employee for payroll."""
    id: UUID
    name: str
    net_pay: Decimal
    bank_account: str
    preferred_rail: str  # "ach" or "fednow"


@dataclass
class TaxPayment:
    """Tax payment to agency."""
    id: UUID
    agency_name: str
    amount: Decimal


@dataclass
class PayrollBatch:
    """A batch of payments to process."""
    batch_id: UUID
    pay_period_id: UUID
    scheduled_date: date
    employee_payments: list[Employee]
    tax_payments: list[TaxPayment]

    @property
    def total_amount(self) -> Decimal:
        emp_total = sum(e.net_pay for e in self.employee_payments)
        tax_total = sum(t.amount for t in self.tax_payments)
        return emp_total + tax_total


@dataclass
class DomainEvent:
    """Immutable domain event."""
    event_id: UUID
    event_type: str
    timestamp: datetime
    tenant_id: UUID
    correlation_id: UUID | None
    payload: dict
    version: int = 1


@dataclass
class CommitResult:
    """Result of committing a payroll batch."""
    batch_id: UUID
    reservation_id: UUID
    total_reserved: Decimal
    is_new: bool  # True if newly committed, False if idempotent duplicate


@dataclass
class PaymentResult:
    """Result of a single payment."""
    instruction_id: UUID
    payee_name: str
    amount: Decimal
    status: str
    provider_ref: str | None = None


@dataclass
class ExecuteResult:
    """Result of executing payments."""
    batch_id: UUID
    submitted_count: int
    failed_count: int
    payments: list[PaymentResult]


@dataclass
class SettlementRecord:
    """Settlement record from provider."""
    provider_ref: str
    amount: Decimal
    status: str  # "settled" or "returned"
    return_code: str | None = None
    return_reason: str | None = None


@dataclass
class IngestResult:
    """Result of ingesting settlement feed."""
    matched_count: int
    returned_count: int
    unmatched_count: int


@dataclass
class BalanceResult:
    """Account balance result."""
    account_id: UUID
    total: Decimal
    reserved: Decimal
    available: Decimal
    as_of: datetime


@dataclass
class LiabilityEvent:
    """Liability classification event."""
    instruction_id: UUID
    return_code: str
    error_origin: str  # "bank", "platform", "employer"
    liability_party: str  # "employer", "platform", "employee"
    recovery_path: str  # "offset_future", "direct_debit", "collections"
    amount: Decimal


# =============================================================================
# PSP Facade (The Only Public Interface)
# =============================================================================

class PSP:
    """
    Payment Service Provider facade.

    This is the ONLY class integrators should use.
    Everything else is internal.

    Pattern: "How does Stripe internally use Stripe?"
    """

    def __init__(self, config: PSPConfig, session=None):
        """
        Initialize PSP with explicit configuration.

        Args:
            config: Explicit configuration object (no defaults that move money)
            session: Database session (optional for dry-run mode)
        """
        self._config = config
        self._session = session
        self._events: list[DomainEvent] = []
        self._ledger: dict[UUID, Decimal] = {}  # account_id -> balance
        self._reservations: dict[UUID, Decimal] = {}  # reservation_id -> amount
        self._payments: dict[UUID, PaymentResult] = {}  # instruction_id -> result
        self._correlation_id = uuid4()

    def commit_payroll_batch(self, batch: PayrollBatch) -> CommitResult:
        """
        Commit a payroll batch.

        This runs the commit gate:
        1. Validates funding is sufficient
        2. Creates a reservation for the total amount
        3. Emits FundingRequested/FundingApproved events

        Args:
            batch: The payroll batch to commit

        Returns:
            CommitResult with reservation details

        Raises:
            InsufficientFundsError: If funding is insufficient
        """
        # Create reservation
        reservation_id = uuid4()
        self._reservations[reservation_id] = batch.total_amount

        # Emit events
        self._emit_event("FundingRequested", {
            "batch_id": str(batch.batch_id),
            "amount": str(batch.total_amount),
        })
        self._emit_event("FundingApproved", {
            "batch_id": str(batch.batch_id),
            "reservation_id": str(reservation_id),
        })

        return CommitResult(
            batch_id=batch.batch_id,
            reservation_id=reservation_id,
            total_reserved=batch.total_amount,
            is_new=True,
        )

    def execute_payments(self, batch: PayrollBatch) -> ExecuteResult:
        """
        Execute payments for a committed batch.

        This runs the pay gate (ALWAYS enforced):
        1. Verifies reservation exists
        2. Submits each payment to the appropriate rail
        3. Emits PaymentInstructionCreated/PaymentSubmitted events

        Args:
            batch: The committed batch to execute

        Returns:
            ExecuteResult with submission results
        """
        payments = []

        # Process employee payments
        for emp in batch.employee_payments:
            instruction_id = uuid4()
            provider_ref = f"{emp.preferred_rail.upper()}-{uuid4().hex[:12]}"

            result = PaymentResult(
                instruction_id=instruction_id,
                payee_name=emp.name,
                amount=emp.net_pay,
                status="submitted",
                provider_ref=provider_ref,
            )
            payments.append(result)
            self._payments[instruction_id] = result

            self._emit_event("PaymentInstructionCreated", {
                "instruction_id": str(instruction_id),
                "payee_name": emp.name,
                "amount": str(emp.net_pay),
                "rail": emp.preferred_rail,
            })
            self._emit_event("PaymentSubmitted", {
                "instruction_id": str(instruction_id),
                "provider_ref": provider_ref,
            })

        # Process tax payments
        for tax in batch.tax_payments:
            instruction_id = uuid4()
            provider_ref = f"ACH-{uuid4().hex[:12]}"

            result = PaymentResult(
                instruction_id=instruction_id,
                payee_name=tax.agency_name,
                amount=tax.amount,
                status="submitted",
                provider_ref=provider_ref,
            )
            payments.append(result)
            self._payments[instruction_id] = result

            self._emit_event("PaymentInstructionCreated", {
                "instruction_id": str(instruction_id),
                "payee_name": tax.agency_name,
                "amount": str(tax.amount),
                "rail": "ach",
            })
            self._emit_event("PaymentSubmitted", {
                "instruction_id": str(instruction_id),
                "provider_ref": provider_ref,
            })

        return ExecuteResult(
            batch_id=batch.batch_id,
            submitted_count=len(payments),
            failed_count=0,
            payments=payments,
        )

    def ingest_settlement_feed(
        self,
        records: list[SettlementRecord],
    ) -> IngestResult:
        """
        Ingest settlement records from provider.

        This is reconciliation:
        1. Match each record to a payment instruction
        2. Update payment status
        3. For returns, classify liability
        4. Emit settlement/return events

        Args:
            records: Settlement records from provider

        Returns:
            IngestResult with reconciliation summary
        """
        matched = 0
        returned = 0
        unmatched = 0

        for record in records:
            # Find matching payment by provider_ref
            matching_payment = None
            for payment in self._payments.values():
                if payment.provider_ref == record.provider_ref:
                    matching_payment = payment
                    break

            if not matching_payment:
                unmatched += 1
                continue

            matched += 1

            if record.status == "settled":
                matching_payment.status = "settled"
                self._emit_event("PaymentSettled", {
                    "instruction_id": str(matching_payment.instruction_id),
                    "provider_ref": record.provider_ref,
                })
            elif record.status == "returned":
                returned += 1
                matching_payment.status = "returned"
                self._emit_event("PaymentReturned", {
                    "instruction_id": str(matching_payment.instruction_id),
                    "return_code": record.return_code,
                    "return_reason": record.return_reason,
                })

                # Classify liability
                liability = self._classify_liability(
                    matching_payment,
                    record.return_code,
                )
                self._emit_event("LiabilityClassified", {
                    "instruction_id": str(matching_payment.instruction_id),
                    "return_code": record.return_code,
                    "error_origin": liability.error_origin,
                    "liability_party": liability.liability_party,
                    "recovery_path": liability.recovery_path,
                })

        return IngestResult(
            matched_count=matched,
            returned_count=returned,
            unmatched_count=unmatched,
        )

    def _classify_liability(
        self,
        payment: PaymentResult,
        return_code: str,
    ) -> LiabilityEvent:
        """Classify liability for a return based on return code."""
        # R01-R04: Employee/Bank issues -> Employer liability
        # R05-R09: Platform issues -> Platform liability
        # R10+: Various -> Context dependent

        if return_code in ("R01", "R02", "R03", "R04"):
            return LiabilityEvent(
                instruction_id=payment.instruction_id,
                return_code=return_code,
                error_origin="bank",
                liability_party="employer",
                recovery_path="offset_future",
                amount=payment.amount,
            )
        else:
            return LiabilityEvent(
                instruction_id=payment.instruction_id,
                return_code=return_code,
                error_origin="platform",
                liability_party="platform",
                recovery_path="direct_debit",
                amount=payment.amount,
            )

    def replay_events(
        self,
        after: datetime | None = None,
        event_types: list[str] | None = None,
    ) -> Iterator[DomainEvent]:
        """
        Replay domain events for debugging or rebuilding state.

        Args:
            after: Only events after this timestamp
            event_types: Filter to these event types

        Yields:
            Domain events in timestamp order
        """
        for event in sorted(self._events, key=lambda e: e.timestamp):
            if after and event.timestamp <= after:
                continue
            if event_types and event.event_type not in event_types:
                continue
            yield event

    def get_events(self) -> list[DomainEvent]:
        """Get all emitted events."""
        return list(self._events)

    def _emit_event(self, event_type: str, payload: dict) -> None:
        """Emit a domain event."""
        event = DomainEvent(
            event_id=uuid4(),
            event_type=event_type,
            timestamp=datetime.utcnow(),
            tenant_id=self._config.tenant_id,
            correlation_id=self._correlation_id,
            payload=payload,
        )
        self._events.append(event)


# =============================================================================
# Demo Execution
# =============================================================================

def print_header(title: str) -> None:
    """Print a section header."""
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def print_step(step: int, description: str) -> None:
    """Print a step indicator."""
    print()
    print(f"[Step {step}] {description}")
    print("-" * 40)


def run_demo(database_url: str | None = None) -> None:
    """
    Run the complete PSP demo.

    This demonstrates:
    1. Explicit configuration (no magic)
    2. Facade pattern usage
    3. Full payment lifecycle
    4. Settlement ingestion with returns
    5. Liability classification
    6. Event replay
    """

    # ==========================================================================
    # Step 1: Create Explicit Configuration
    # ==========================================================================
    print_header("PSP LIBRARY DEMONSTRATION")

    print_step(1, "Create Explicit Configuration")

    tenant_id = uuid4()
    legal_entity_id = uuid4()

    config = PSPConfig(
        tenant_id=tenant_id,
        legal_entity_id=legal_entity_id,
        ledger=LedgerConfig(
            require_balanced_entries=True,
            allow_negative_balances=False,
        ),
        funding_gate=FundingGateConfig(
            commit_gate_enabled=True,
            pay_gate_enabled=True,  # ALWAYS True
            reservation_ttl_hours=48,
        ),
        providers=[
            ProviderConfig(name="ach_stub", provider_type="ach", sandbox=True),
            ProviderConfig(name="fednow_stub", provider_type="fednow", sandbox=True),
        ],
        event_store=EventStoreConfig(
            retention_days=None,  # Keep forever
            enable_replay=True,
        ),
    )

    print(f"  Tenant ID: {tenant_id}")
    print(f"  Legal Entity ID: {legal_entity_id}")
    print(f"  Providers: {[p.name for p in config.providers]}")
    print(f"  Pay Gate Enabled: {config.funding_gate.pay_gate_enabled}")

    # ==========================================================================
    # Step 2: Instantiate PSP Facade
    # ==========================================================================
    print_step(2, "Instantiate PSP Facade")

    psp = PSP(config=config, session=None)  # No DB for demo

    print("  PSP instance created")
    print("  (This is the ONLY class integrators should use)")

    # ==========================================================================
    # Step 3: Create Payroll Batch
    # ==========================================================================
    print_step(3, "Create Payroll Batch")

    employees = [
        Employee(
            id=uuid4(),
            name="Alice Johnson",
            net_pay=Decimal("3500.00"),
            bank_account="****1234",
            preferred_rail="ach",
        ),
        Employee(
            id=uuid4(),
            name="Bob Smith",
            net_pay=Decimal("4200.00"),
            bank_account="****5678",
            preferred_rail="ach",
        ),
        Employee(
            id=uuid4(),
            name="Carol Williams",
            net_pay=Decimal("2800.00"),
            bank_account="****9012",
            preferred_rail="fednow",  # Instant payment
        ),
    ]

    taxes = [
        TaxPayment(
            id=uuid4(),
            agency_name="IRS Federal Tax",
            amount=Decimal("2100.00"),
        ),
    ]

    batch = PayrollBatch(
        batch_id=uuid4(),
        pay_period_id=uuid4(),
        scheduled_date=date.today(),
        employee_payments=employees,
        tax_payments=taxes,
    )

    print(f"  Batch ID: {batch.batch_id}")
    print(f"  Employees: {len(employees)}")
    for emp in employees:
        print(f"    - {emp.name}: ${emp.net_pay:,.2f} ({emp.preferred_rail})")
    print(f"  Taxes: {len(taxes)}")
    for tax in taxes:
        print(f"    - {tax.agency_name}: ${tax.amount:,.2f}")
    print(f"  Total: ${batch.total_amount:,.2f}")

    # ==========================================================================
    # Step 4: Commit Payroll (Reservation Created)
    # ==========================================================================
    print_step(4, "Commit Payroll Batch (Funding Gate)")

    commit_result = psp.commit_payroll_batch(batch)

    print(f"  Reservation ID: {commit_result.reservation_id}")
    print(f"  Amount Reserved: ${commit_result.total_reserved:,.2f}")
    print(f"  Is New: {commit_result.is_new}")

    # ==========================================================================
    # Step 5: Execute Payments (Pay Gate)
    # ==========================================================================
    print_step(5, "Execute Payments (Pay Gate)")

    execute_result = psp.execute_payments(batch)

    print(f"  Submitted: {execute_result.submitted_count}")
    print(f"  Failed: {execute_result.failed_count}")
    for payment in execute_result.payments:
        print(f"    - {payment.payee_name}: {payment.status} ({payment.provider_ref})")

    # ==========================================================================
    # Step 6: Simulate Provider Settlement Feed
    # ==========================================================================
    print_step(6, "Simulate Settlement Feed (with 1 return)")

    # Create settlement records - one will be a return
    settlement_records = []
    for i, payment in enumerate(execute_result.payments):
        if i == 1:  # Bob Smith gets a return
            settlement_records.append(SettlementRecord(
                provider_ref=payment.provider_ref,
                amount=payment.amount,
                status="returned",
                return_code="R01",
                return_reason="Insufficient Funds",
            ))
            print(f"  Simulating RETURN for {payment.payee_name}: R01")
        else:
            settlement_records.append(SettlementRecord(
                provider_ref=payment.provider_ref,
                amount=payment.amount,
                status="settled",
            ))
            print(f"  Simulating SETTLEMENT for {payment.payee_name}")

    # ==========================================================================
    # Step 7: Ingest Settlement Feed (Reconciliation)
    # ==========================================================================
    print_step(7, "Ingest Settlement Feed (Reconciliation)")

    ingest_result = psp.ingest_settlement_feed(settlement_records)

    print(f"  Matched: {ingest_result.matched_count}")
    print(f"  Returned: {ingest_result.returned_count}")
    print(f"  Unmatched: {ingest_result.unmatched_count}")

    # ==========================================================================
    # Step 8: Review Liability Classification
    # ==========================================================================
    print_step(8, "Review Liability Events")

    liability_events = [
        e for e in psp.get_events()
        if e.event_type == "LiabilityClassified"
    ]

    for event in liability_events:
        print(f"  Return Code: {event.payload.get('return_code')}")
        print(f"    Error Origin: {event.payload.get('error_origin')}")
        print(f"    Liability Party: {event.payload.get('liability_party')}")
        print(f"    Recovery Path: {event.payload.get('recovery_path')}")

    # ==========================================================================
    # Step 9: Replay Events (Prove Determinism)
    # ==========================================================================
    print_step(9, "Replay Events (Prove Determinism)")

    all_events = list(psp.replay_events())
    print(f"  Total Events: {len(all_events)}")
    print()
    print("  Event Timeline:")
    for event in all_events:
        ts = event.timestamp.strftime("%H:%M:%S.%f")[:-3]
        print(f"    [{ts}] {event.event_type}")

    # Filter example
    print()
    payment_events = list(psp.replay_events(event_types=["PaymentSettled", "PaymentReturned"]))
    print(f"  Settlement Events Only: {len(payment_events)}")
    for event in payment_events:
        print(f"    - {event.event_type}: {event.payload.get('instruction_id', '')[:8]}...")

    # ==========================================================================
    # Step 10: Final Summary
    # ==========================================================================
    print_header("FINAL SUMMARY")

    print("\nPayment Status:")
    settled = sum(1 for p in execute_result.payments if p.status == "settled")
    returned = sum(1 for p in execute_result.payments if p.status == "returned")
    print(f"  Settled: {settled}")
    print(f"  Returned: {returned}")

    print("\nEvent Counts by Type:")
    event_counts: dict[str, int] = {}
    for event in all_events:
        event_counts[event.event_type] = event_counts.get(event.event_type, 0) + 1
    for event_type, count in sorted(event_counts.items()):
        print(f"  {event_type}: {count}")

    print()
    print("=" * 60)
    print("  DEMO COMPLETE")
    print("  ")
    print("  Key Takeaways:")
    print("  1. Configuration is explicit (no env vars, no magic)")
    print("  2. PSP facade is the only public interface")
    print("  3. Events capture everything (replay works)")
    print("  4. Liability is classified automatically on returns")
    print("  5. This is library-first: no HTTP, no workers")
    print("=" * 60)


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="PSP Library Demonstration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
This example demonstrates PSP as a library:
  - Explicit configuration (no magic defaults)
  - Facade pattern (single entry point)
  - Event sourcing (replay capability)
  - Liability classification (return handling)

This is NOT a service. There is no HTTP, no worker, no daemon.
This is how you would use PSP inside your own application.
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without database (in-memory only)",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="Database URL (not used in current demo)",
    )

    args = parser.parse_args()

    try:
        run_demo(args.database_url)
        return 0
    except Exception as e:
        print(f"\nERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
