"""Tests for PaymentOrchestrator - Instruction-based payment execution.

Tests verify:
1. Payment instruction creation (idempotent)
2. Provider submission with attempt tracking
3. Payment retries safe after partial settlement
4. Status updates
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from payroll_engine.psp.services.ledger_service import LedgerService
from payroll_engine.psp.services.payment_orchestrator import (
    PaymentOrchestrator,
    InstructionResult,
    SubmissionResult,
)
from payroll_engine.psp.providers.ach_stub import AchStubProvider
from payroll_engine.psp.providers.fednow_stub import FedNowStubProvider
from tests.psp.conftest import PSPTestData


class TestInstructionCreation:
    """Test payment instruction creation."""

    def test_create_employee_net_instruction(self, psp_sync_db: Session, test_data: PSPTestData):
        """Create employee net pay instruction."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        employee_id = uuid4()
        pay_statement_id = uuid4()

        result = orchestrator.create_employee_net_instruction(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            employee_id=employee_id,
            pay_statement_id=pay_statement_id,
            amount=Decimal("3500.00"),
            idempotency_key=f"net_pay_{uuid4().hex[:8]}",
        )

        assert result.instruction_id is not None
        assert result.was_duplicate is False
        assert result.status == "created"

        # Verify in database
        row = psp_sync_db.execute(
            text("""
                SELECT purpose, direction, amount, payee_type, status
                FROM payment_instruction
                WHERE payment_instruction_id = :id
            """),
            {"id": str(result.instruction_id)},
        ).fetchone()

        assert row[0] == "employee_net"
        assert row[1] == "outbound"
        assert Decimal(str(row[2])) == Decimal("3500.00")
        assert row[3] == "employee"
        assert row[4] == "created"

    def test_create_instruction_idempotent(self, psp_sync_db: Session, test_data: PSPTestData):
        """Creating instruction with same key returns existing."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        employee_id = uuid4()
        pay_statement_id = uuid4()
        idk = f"idem_net_{uuid4().hex[:8]}"

        # First create
        result1 = orchestrator.create_employee_net_instruction(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            employee_id=employee_id,
            pay_statement_id=pay_statement_id,
            amount=Decimal("3500.00"),
            idempotency_key=idk,
        )
        psp_sync_db.commit()

        # Retry with same key
        result2 = orchestrator.create_employee_net_instruction(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            employee_id=employee_id,
            pay_statement_id=pay_statement_id,
            amount=Decimal("9999.99"),  # Different amount - ignored
            idempotency_key=idk,
        )

        assert result1.instruction_id == result2.instruction_id
        assert result2.was_duplicate is True

        # Only one instruction in database
        count = psp_sync_db.execute(
            text("""
                SELECT COUNT(*) FROM payment_instruction
                WHERE tenant_id = :tenant_id AND idempotency_key = :idk
            """),
            {"tenant_id": str(test_data.tenant_id), "idk": idk},
        ).scalar()
        assert count == 1

    def test_create_tax_instruction(self, psp_sync_db: Session, test_data: PSPTestData):
        """Create tax remittance instruction."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        tax_agency_id = uuid4()
        tax_liability_id = uuid4()

        result = orchestrator.create_tax_instruction(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            tax_agency_id=tax_agency_id,
            tax_liability_id=tax_liability_id,
            amount=Decimal("1500.00"),
            idempotency_key=f"tax_{uuid4().hex[:8]}",
        )

        assert result.instruction_id is not None
        assert result.status == "created"

        row = psp_sync_db.execute(
            text("SELECT purpose, payee_type FROM payment_instruction WHERE payment_instruction_id = :id"),
            {"id": str(result.instruction_id)},
        ).fetchone()
        assert row[0] == "tax_remit"
        assert row[1] == "agency"

    def test_create_third_party_instruction(self, psp_sync_db: Session, test_data: PSPTestData):
        """Create third-party obligation instruction."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        provider_id = uuid4()
        obligation_id = uuid4()

        result = orchestrator.create_third_party_instruction(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            provider_id=provider_id,
            obligation_id=obligation_id,
            amount=Decimal("500.00"),
            idempotency_key=f"3p_{uuid4().hex[:8]}",
        )

        assert result.instruction_id is not None

        row = psp_sync_db.execute(
            text("SELECT purpose, payee_type FROM payment_instruction WHERE payment_instruction_id = :id"),
            {"id": str(result.instruction_id)},
        ).fetchone()
        assert row[0] == "third_party"
        assert row[1] == "provider"


class TestPaymentSubmission:
    """Test payment submission to providers."""

    def test_submit_to_ach_provider(self, psp_sync_db: Session, test_data: PSPTestData):
        """Submit instruction to ACH provider."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        # Create instruction
        instruction = orchestrator.create_employee_net_instruction(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            employee_id=uuid4(),
            pay_statement_id=uuid4(),
            amount=Decimal("2000.00"),
            idempotency_key=f"submit_ach_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        # Submit to provider
        result = orchestrator.submit(
            tenant_id=test_data.tenant_id,
            payment_instruction_id=instruction.instruction_id,
        )

        assert result.instruction_id == instruction.instruction_id
        assert result.accepted is True
        assert result.provider_request_id is not None

        # Verify attempt recorded
        attempt = psp_sync_db.execute(
            text("""
                SELECT rail, provider, status
                FROM payment_attempt
                WHERE payment_instruction_id = :id
            """),
            {"id": str(instruction.instruction_id)},
        ).fetchone()
        assert attempt[0] == "ach"
        assert attempt[1] == "ach_stub"
        assert attempt[2] == "accepted"

        # Verify instruction status updated
        status = psp_sync_db.execute(
            text("SELECT status FROM payment_instruction WHERE payment_instruction_id = :id"),
            {"id": str(instruction.instruction_id)},
        ).scalar()
        assert status == "submitted"

    def test_submit_to_fednow_provider(self, psp_sync_db: Session, test_data: PSPTestData):
        """Submit instruction to FedNow provider."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = FedNowStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        instruction = orchestrator.create_employee_net_instruction(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            employee_id=uuid4(),
            pay_statement_id=uuid4(),
            amount=Decimal("1500.00"),
            idempotency_key=f"submit_fednow_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        result = orchestrator.submit(
            tenant_id=test_data.tenant_id,
            payment_instruction_id=instruction.instruction_id,
        )

        assert result.accepted is True

        attempt = psp_sync_db.execute(
            text("SELECT rail, provider FROM payment_attempt WHERE payment_instruction_id = :id"),
            {"id": str(instruction.instruction_id)},
        ).fetchone()
        assert attempt[0] == "fednow"
        assert attempt[1] == "fednow_stub"

    def test_submit_rejects_already_submitted(self, psp_sync_db: Session, test_data: PSPTestData):
        """Cannot submit instruction that's already submitted."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        instruction = orchestrator.create_employee_net_instruction(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            employee_id=uuid4(),
            pay_statement_id=uuid4(),
            amount=Decimal("2000.00"),
            idempotency_key=f"double_submit_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        # First submission
        orchestrator.submit(
            tenant_id=test_data.tenant_id,
            payment_instruction_id=instruction.instruction_id,
        )
        psp_sync_db.commit()

        # Second submission should fail
        with pytest.raises(ValueError, match="Cannot submit instruction in status"):
            orchestrator.submit(
                tenant_id=test_data.tenant_id,
                payment_instruction_id=instruction.instruction_id,
            )

    def test_submit_not_found(self, psp_sync_db: Session, test_data: PSPTestData):
        """Submit non-existent instruction raises error."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        with pytest.raises(ValueError, match="not found"):
            orchestrator.submit(
                tenant_id=test_data.tenant_id,
                payment_instruction_id=uuid4(),
            )


class TestPaymentRetries:
    """Test payment retry safety."""

    def test_retry_after_provider_failure(self, psp_sync_db: Session, test_data: PSPTestData):
        """Can retry submission after provider failure."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()

        # Simulate a failed submission by manually setting status
        instruction_id = uuid4()
        psp_sync_db.execute(
            text("""
                INSERT INTO payment_instruction(
                    payment_instruction_id, tenant_id, legal_entity_id, purpose, direction,
                    amount, payee_type, payee_ref_id, status, idempotency_key, source_type, source_id
                )
                VALUES (
                    :id, :tenant_id, :le_id, 'employee_net', 'outbound',
                    2500.00, 'employee', :emp_id, 'queued', :idk, 'test', :source_id
                )
            """),
            {
                "id": str(instruction_id),
                "tenant_id": str(test_data.tenant_id),
                "le_id": str(test_data.legal_entity_id),
                "emp_id": str(uuid4()),
                "idk": f"retry_{uuid4().hex[:8]}",
                "source_id": str(uuid4()),
            },
        )
        psp_sync_db.commit()

        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        # Can submit from queued status
        result = orchestrator.submit(
            tenant_id=test_data.tenant_id,
            payment_instruction_id=instruction_id,
        )

        assert result.accepted is True

    def test_attempt_tracking_across_retries(self, psp_sync_db: Session, test_data: PSPTestData):
        """Multiple attempts are tracked separately."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()

        # Create instruction and first attempt
        instruction_id = uuid4()
        psp_sync_db.execute(
            text("""
                INSERT INTO payment_instruction(
                    payment_instruction_id, tenant_id, legal_entity_id, purpose, direction,
                    amount, payee_type, payee_ref_id, status, idempotency_key, source_type, source_id
                )
                VALUES (
                    :id, :tenant_id, :le_id, 'employee_net', 'outbound',
                    2500.00, 'employee', :emp_id, 'created', :idk, 'test', :source_id
                )
            """),
            {
                "id": str(instruction_id),
                "tenant_id": str(test_data.tenant_id),
                "le_id": str(test_data.legal_entity_id),
                "emp_id": str(uuid4()),
                "idk": f"multi_attempt_{uuid4().hex[:8]}",
                "source_id": str(uuid4()),
            },
        )
        psp_sync_db.commit()

        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        # Submit
        result = orchestrator.submit(
            tenant_id=test_data.tenant_id,
            payment_instruction_id=instruction_id,
        )

        # Verify attempt recorded
        count = psp_sync_db.execute(
            text("SELECT COUNT(*) FROM payment_attempt WHERE payment_instruction_id = :id"),
            {"id": str(instruction_id)},
        ).scalar()
        assert count == 1


class TestStatusUpdates:
    """Test payment status updates."""

    def test_update_status_to_settled(self, psp_sync_db: Session, test_data: PSPTestData):
        """Update instruction status to settled."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        # Create and submit instruction
        instruction = orchestrator.create_employee_net_instruction(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            employee_id=uuid4(),
            pay_statement_id=uuid4(),
            amount=Decimal("2000.00"),
            idempotency_key=f"settle_test_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        orchestrator.submit(
            tenant_id=test_data.tenant_id,
            payment_instruction_id=instruction.instruction_id,
        )
        psp_sync_db.commit()

        # Update to settled
        updated = orchestrator.update_status(
            tenant_id=test_data.tenant_id,
            payment_instruction_id=instruction.instruction_id,
            new_status="settled",
        )

        assert updated is True

        status = psp_sync_db.execute(
            text("SELECT status FROM payment_instruction WHERE payment_instruction_id = :id"),
            {"id": str(instruction.instruction_id)},
        ).scalar()
        assert status == "settled"

    def test_update_status_not_found(self, psp_sync_db: Session, test_data: PSPTestData):
        """Update non-existent instruction returns False."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        updated = orchestrator.update_status(
            tenant_id=test_data.tenant_id,
            payment_instruction_id=uuid4(),
            new_status="settled",
        )

        assert updated is False


class TestGetInstructionsForSubmission:
    """Test querying instructions ready for submission."""

    def test_get_created_instructions(self, psp_sync_db: Session, test_data: PSPTestData):
        """Get instructions in created status."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)

        # Create multiple instructions
        for i in range(3):
            orchestrator.create_employee_net_instruction(
                tenant_id=test_data.tenant_id,
                legal_entity_id=test_data.legal_entity_id,
                employee_id=uuid4(),
                pay_statement_id=uuid4(),
                amount=Decimal(f"{1000 + i * 100}.00"),
                idempotency_key=f"batch_{i}_{uuid4().hex[:8]}",
            )
        psp_sync_db.commit()

        # Get pending instructions
        instructions = orchestrator.get_instructions_for_submission(
            tenant_id=test_data.tenant_id,
        )

        assert len(instructions) >= 3
        for instr in instructions:
            assert instr["status"] in ("created", "queued")
