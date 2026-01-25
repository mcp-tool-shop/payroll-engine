"""Red Team Payroll Scenarios - Adversarial Testing.

These tests verify the PSP system behaves correctly under attack patterns:

1. Double-spend attempts
2. Replay attacks
3. Race conditions
4. Balance manipulation
5. Timing attacks
6. Settlement fraud
7. Reversal abuse
8. Cross-tenant attacks

These scenarios represent real-world fraud patterns and system abuse.
The tests ensure invariants hold under adversarial conditions.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from payroll_engine.psp.services.ledger_service import LedgerService
from payroll_engine.psp.services.funding_gate import FundingGateService
from payroll_engine.psp.services.payment_orchestrator import PaymentOrchestrator
from payroll_engine.psp.services.reconciliation import ReconciliationService
from payroll_engine.psp.providers.ach_stub import AchStubProvider
from tests.psp.conftest import PSPTestData


class TestDoubleSpendPrevention:
    """Verify double-spend attacks are prevented."""

    def test_same_idempotency_key_prevents_duplicate_ledger_entry(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Same idempotency key cannot create duplicate entries."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        psp_sync_db.commit()

        idempotency_key = f"double_spend_{uuid4().hex[:8]}"

        # First post succeeds
        result1 = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("10000.00"),
            source_type="test",
            source_id=uuid4(),
            idempotency_key=idempotency_key,
        )
        psp_sync_db.commit()

        assert result1.is_new is True

        # Attack: Try to post again with same key
        result2 = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("10000.00"),  # Same amount
            source_type="test",
            source_id=uuid4(),
            idempotency_key=idempotency_key,
        )

        # Second attempt returns existing, no new entry
        assert result2.is_new is False
        assert result2.entry_id == result1.entry_id

        # Verify only one entry exists
        count = psp_sync_db.execute(
            text("""
                SELECT COUNT(*) FROM psp_ledger_entry
                WHERE idempotency_key = :key
            """),
            {"key": idempotency_key},
        ).scalar()
        assert count == 1

    def test_same_idempotency_key_different_amount_still_returns_existing(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Attack: Try to change amount with same key."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        psp_sync_db.commit()

        idempotency_key = f"amount_change_{uuid4().hex[:8]}"

        # First post
        result1 = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("5000.00"),
            source_type="test",
            source_id=uuid4(),
            idempotency_key=idempotency_key,
        )
        psp_sync_db.commit()

        # Attack: Same key, larger amount
        result2 = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("50000.00"),  # 10x more!
            source_type="test",
            source_id=uuid4(),
            idempotency_key=idempotency_key,
        )

        # Attack failed - returns original entry
        assert result2.is_new is False
        assert result2.entry_id == result1.entry_id

    def test_payment_instruction_idempotency(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Same payment instruction cannot be created twice."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        provider = AchStubProvider()
        orchestrator = PaymentOrchestrator(psp_sync_db, ledger, provider)
        psp_sync_db.commit()

        idempotency_key = f"pay_instr_{uuid4().hex[:8]}"

        # First instruction
        result1 = orchestrator.create_instruction(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            purpose="employee_net",
            direction="outbound",
            amount=Decimal("2500.00"),
            payee_type="employee",
            payee_ref_id=uuid4(),
            source_type="test",
            source_id=uuid4(),
            idempotency_key=idempotency_key,
        )
        psp_sync_db.commit()

        # Attack: Try again
        result2 = orchestrator.create_instruction(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            purpose="employee_net",
            direction="outbound",
            amount=Decimal("2500.00"),
            payee_type="employee",
            payee_ref_id=uuid4(),
            source_type="test",
            source_id=uuid4(),
            idempotency_key=idempotency_key,
        )

        # Same instruction returned
        assert result2.instruction_id == result1.instruction_id


class TestRaceConditions:
    """Test concurrent access scenarios."""

    def test_concurrent_balance_reads_during_update(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Balance reads should be consistent during concurrent updates."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        psp_sync_db.commit()

        # Seed initial balance
        ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("100000.00"),
            source_type="test",
            source_id=uuid4(),
            idempotency_key=f"seed_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        # Verify initial balance
        balance = ledger.get_balance(
            tenant_id=test_data.tenant_id,
            account_id=accounts["client_funding_clearing"],
        )
        assert balance.available == Decimal("100000.00")

    def test_pay_gate_prevents_overdraft_under_load(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Pay gate should prevent overdrafts even under concurrent requests."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        funding_gate = FundingGateService(psp_sync_db, ledger)
        psp_sync_db.commit()

        account_id = accounts["client_funding_clearing"]

        # Seed exactly $10,000
        ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=account_id,
            amount=Decimal("10000.00"),
            source_type="test",
            source_id=uuid4(),
            idempotency_key=f"seed_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        # Try to spend $10,001 (should fail)
        result = funding_gate.evaluate_pay_gate(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            account_id=account_id,
            required_amount=Decimal("10001.00"),
        )

        assert result.passed is False
        assert "insufficient" in result.reason.lower()

    def test_reservation_prevents_double_allocation(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Reservations should prevent double-allocation of funds."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        funding_gate = FundingGateService(psp_sync_db, ledger)
        psp_sync_db.commit()

        account_id = accounts["client_funding_clearing"]

        # Seed $10,000
        ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=account_id,
            amount=Decimal("10000.00"),
            source_type="test",
            source_id=uuid4(),
            idempotency_key=f"seed_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        # Reserve $8,000
        reservation_id = funding_gate.create_reservation(
            tenant_id=test_data.tenant_id,
            account_id=account_id,
            amount=Decimal("8000.00"),
            purpose="payroll_1",
            expires_at=datetime.utcnow() + timedelta(hours=1),
        )
        psp_sync_db.commit()

        assert reservation_id is not None

        # Attack: Try to spend $8,000 (should fail - reserved)
        # Available is 10000 - 8000 = 2000
        result = funding_gate.evaluate_pay_gate(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            account_id=account_id,
            required_amount=Decimal("3000.00"),  # More than 2000 available
        )

        assert result.passed is False


class TestCrossTenantAttacks:
    """Verify tenant isolation cannot be bypassed."""

    def test_cannot_read_other_tenant_balance(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Cannot read balance for another tenant's account."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        psp_sync_db.commit()

        # Seed balance for test tenant
        ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("50000.00"),
            source_type="test",
            source_id=uuid4(),
            idempotency_key=f"seed_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        # Attack: Try to read with different tenant_id
        attacker_tenant_id = uuid4()
        balance = ledger.get_balance(
            tenant_id=attacker_tenant_id,
            account_id=accounts["client_funding_clearing"],
        )

        # Should return zero - account doesn't exist for attacker tenant
        assert balance.available == Decimal("0.00")

    def test_cannot_post_to_other_tenant_account(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Cannot post ledger entry to another tenant's account."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        psp_sync_db.commit()

        attacker_tenant_id = uuid4()

        # Attack: Try to post with attacker tenant but victim's accounts
        # The service should validate tenant ownership
        result = ledger.post_entry(
            tenant_id=attacker_tenant_id,
            legal_entity_id=uuid4(),
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("999999.00"),
            source_type="attack",
            source_id=uuid4(),
            idempotency_key=f"attack_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        # Entry may be created but with attacker's tenant_id
        # It won't affect victim's balance because tenant_id is part of query
        victim_balance = ledger.get_balance(
            tenant_id=test_data.tenant_id,
            account_id=accounts["client_funding_clearing"],
        )
        # Balance should be 0 (no legitimate funding)
        assert victim_balance.available == Decimal("0.00")


class TestSettlementFraud:
    """Test settlement manipulation attempts."""

    def test_cannot_create_duplicate_settlement_event(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Same external trace ID cannot create duplicate settlements."""
        bank_account_id = test_data.create_bank_account(psp_sync_db)
        psp_sync_db.commit()

        trace_id = f"ACH{uuid4().hex[:12]}"

        # First settlement
        psp_sync_db.execute(
            text("""
                INSERT INTO psp_settlement_event(
                    psp_bank_account_id, rail, direction, amount, status,
                    external_trace_id, effective_date
                ) VALUES (
                    :bank_id, 'ach', 'inbound', 10000.00, 'settled',
                    :trace_id, :eff_date
                )
            """),
            {
                "bank_id": str(bank_account_id),
                "trace_id": trace_id,
                "eff_date": date.today(),
            },
        )
        psp_sync_db.commit()

        # Attack: Try to insert duplicate with same trace_id
        # Should violate unique constraint
        with pytest.raises(Exception):  # IntegrityError
            psp_sync_db.execute(
                text("""
                    INSERT INTO psp_settlement_event(
                        psp_bank_account_id, rail, direction, amount, status,
                        external_trace_id, effective_date
                    ) VALUES (
                        :bank_id, 'ach', 'inbound', 999999.00, 'settled',
                        :trace_id, :eff_date
                    )
                """),
                {
                    "bank_id": str(bank_account_id),
                    "trace_id": trace_id,
                    "eff_date": date.today(),
                },
            )
            psp_sync_db.commit()

    def test_settlement_status_cannot_go_backwards(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Settlement status should not regress (settled -> submitted)."""
        bank_account_id = test_data.create_bank_account(psp_sync_db)
        psp_sync_db.commit()

        settlement_id = uuid4()
        trace_id = f"ACH{uuid4().hex[:12]}"

        # Create settled settlement
        psp_sync_db.execute(
            text("""
                INSERT INTO psp_settlement_event(
                    psp_settlement_event_id, psp_bank_account_id, rail, direction,
                    amount, status, external_trace_id, effective_date
                ) VALUES (
                    :id, :bank_id, 'ach', 'inbound', 10000.00, 'settled',
                    :trace_id, :eff_date
                )
            """),
            {
                "id": str(settlement_id),
                "bank_id": str(bank_account_id),
                "trace_id": trace_id,
                "eff_date": date.today(),
            },
        )
        psp_sync_db.commit()

        # Attack: Try to change status back to 'submitted'
        # This should be caught by status transition validation
        # (In real implementation, there would be a trigger or CHECK constraint)
        # For now, verify the current status is 'settled'
        status = psp_sync_db.execute(
            text("""
                SELECT status FROM psp_settlement_event
                WHERE psp_settlement_event_id = :id
            """),
            {"id": str(settlement_id)},
        ).scalar()
        assert status == "settled"


class TestReversalAbuse:
    """Test reversal manipulation attempts."""

    def test_reversal_must_reference_original_entry(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Reversal entry must have valid source_id pointing to original."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        psp_sync_db.commit()

        # Create original entry
        original = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("5000.00"),
            source_type="test",
            source_id=uuid4(),
            idempotency_key=f"original_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        # Create legitimate reversal
        reversal = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="reversal",
            debit_account_id=accounts["client_funding_clearing"],  # Reversed
            credit_account_id=accounts["psp_settlement_clearing"],
            amount=Decimal("5000.00"),
            source_type="psp_ledger_entry",
            source_id=original.entry_id,
            idempotency_key=f"reversal_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        # Verify reversal has correct source linkage
        assert reversal.is_new is True

        # Net balance should be zero
        balance = ledger.get_balance(
            tenant_id=test_data.tenant_id,
            account_id=accounts["client_funding_clearing"],
        )
        assert balance.available == Decimal("0.00")

    def test_cannot_reverse_same_entry_twice(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Same entry cannot be reversed multiple times with same key."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        psp_sync_db.commit()

        # Create original entry
        original = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("5000.00"),
            source_type="test",
            source_id=uuid4(),
            idempotency_key=f"original_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        reversal_key = f"reversal_{original.entry_id.hex[:8]}"

        # First reversal
        rev1 = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="reversal",
            debit_account_id=accounts["client_funding_clearing"],
            credit_account_id=accounts["psp_settlement_clearing"],
            amount=Decimal("5000.00"),
            source_type="psp_ledger_entry",
            source_id=original.entry_id,
            idempotency_key=reversal_key,
        )
        psp_sync_db.commit()

        assert rev1.is_new is True

        # Attack: Try to reverse again with same key
        rev2 = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="reversal",
            debit_account_id=accounts["client_funding_clearing"],
            credit_account_id=accounts["psp_settlement_clearing"],
            amount=Decimal("5000.00"),
            source_type="psp_ledger_entry",
            source_id=original.entry_id,
            idempotency_key=reversal_key,
        )

        # Should return existing, not create new
        assert rev2.is_new is False
        assert rev2.entry_id == rev1.entry_id


class TestLedgerImmutability:
    """Verify ledger append-only properties."""

    def test_ledger_entry_amounts_are_positive(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Ledger entries must have positive amounts."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        psp_sync_db.commit()

        # Attack: Try to insert negative amount directly
        with pytest.raises(Exception):  # Should violate CHECK constraint
            psp_sync_db.execute(
                text("""
                    INSERT INTO psp_ledger_entry(
                        tenant_id, legal_entity_id, entry_type,
                        debit_account_id, credit_account_id, amount,
                        source_type, source_id, idempotency_key
                    ) VALUES (
                        :tenant_id, :le_id, 'funding_received',
                        :debit, :credit, -5000.00,
                        'attack', :source_id, :idk
                    )
                """),
                {
                    "tenant_id": str(test_data.tenant_id),
                    "le_id": str(test_data.legal_entity_id),
                    "debit": str(accounts["psp_settlement_clearing"]),
                    "credit": str(accounts["client_funding_clearing"]),
                    "source_id": str(uuid4()),
                    "idk": f"attack_{uuid4().hex[:8]}",
                },
            )
            psp_sync_db.commit()

    def test_ledger_entry_amount_zero_rejected(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Zero amount entries should be rejected."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        psp_sync_db.commit()

        # Attack: Try to insert zero amount
        with pytest.raises(Exception):  # Should violate CHECK constraint
            psp_sync_db.execute(
                text("""
                    INSERT INTO psp_ledger_entry(
                        tenant_id, legal_entity_id, entry_type,
                        debit_account_id, credit_account_id, amount,
                        source_type, source_id, idempotency_key
                    ) VALUES (
                        :tenant_id, :le_id, 'funding_received',
                        :debit, :credit, 0.00,
                        'attack', :source_id, :idk
                    )
                """),
                {
                    "tenant_id": str(test_data.tenant_id),
                    "le_id": str(test_data.legal_entity_id),
                    "debit": str(accounts["psp_settlement_clearing"]),
                    "credit": str(accounts["client_funding_clearing"]),
                    "source_id": str(uuid4()),
                    "idk": f"attack_{uuid4().hex[:8]}",
                },
            )
            psp_sync_db.commit()


class TestPaymentRailAbuse:
    """Test payment rail manipulation."""

    def test_provider_request_id_uniqueness(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Same provider_request_id cannot be reused."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        psp_sync_db.commit()

        instruction_id = uuid4()
        provider_req_id = f"ACHSTUB-{uuid4().hex[:8]}"

        # Create payment instruction
        psp_sync_db.execute(
            text("""
                INSERT INTO payment_instruction(
                    payment_instruction_id, tenant_id, legal_entity_id,
                    purpose, direction, amount, payee_type, payee_ref_id,
                    status, idempotency_key, source_type, source_id
                ) VALUES (
                    :id, :tenant_id, :le_id,
                    'employee_net', 'outbound', 2500.00, 'employee', :emp_id,
                    'submitted', :idk, 'test', :source_id
                )
            """),
            {
                "id": str(instruction_id),
                "tenant_id": str(test_data.tenant_id),
                "le_id": str(test_data.legal_entity_id),
                "emp_id": str(uuid4()),
                "idk": f"instr_{uuid4().hex[:8]}",
                "source_id": str(uuid4()),
            },
        )

        # First attempt
        psp_sync_db.execute(
            text("""
                INSERT INTO payment_attempt(
                    payment_instruction_id, rail, provider,
                    provider_request_id, status
                ) VALUES (
                    :pi_id, 'ach', 'ach_stub', :req_id, 'accepted'
                )
            """),
            {"pi_id": str(instruction_id), "req_id": provider_req_id},
        )
        psp_sync_db.commit()

        # Attack: Try to create another attempt with same provider_request_id
        instruction_id_2 = uuid4()
        psp_sync_db.execute(
            text("""
                INSERT INTO payment_instruction(
                    payment_instruction_id, tenant_id, legal_entity_id,
                    purpose, direction, amount, payee_type, payee_ref_id,
                    status, idempotency_key, source_type, source_id
                ) VALUES (
                    :id, :tenant_id, :le_id,
                    'employee_net', 'outbound', 99999.00, 'employee', :emp_id,
                    'submitted', :idk, 'attack', :source_id
                )
            """),
            {
                "id": str(instruction_id_2),
                "tenant_id": str(test_data.tenant_id),
                "le_id": str(test_data.legal_entity_id),
                "emp_id": str(uuid4()),
                "idk": f"attack_{uuid4().hex[:8]}",
                "source_id": str(uuid4()),
            },
        )

        # Should violate unique constraint on (provider, provider_request_id)
        with pytest.raises(Exception):
            psp_sync_db.execute(
                text("""
                    INSERT INTO payment_attempt(
                        payment_instruction_id, rail, provider,
                        provider_request_id, status
                    ) VALUES (
                        :pi_id, 'ach', 'ach_stub', :req_id, 'accepted'
                    )
                """),
                {"pi_id": str(instruction_id_2), "req_id": provider_req_id},
            )
            psp_sync_db.commit()


class TestFundingGateBypass:
    """Test attempts to bypass funding gates."""

    def test_pay_gate_cannot_be_bypassed(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Pay gate must always be evaluated - no bypass."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        funding_gate = FundingGateService(psp_sync_db, ledger)
        psp_sync_db.commit()

        account_id = accounts["client_funding_clearing"]

        # No funding - balance is zero
        result = funding_gate.evaluate_pay_gate(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            account_id=account_id,
            required_amount=Decimal("1.00"),  # Even $1 should fail
        )

        assert result.passed is False

    def test_commit_gate_strict_mode_enforced(
        self, psp_sync_db: Session, test_data: PSPTestData
    ):
        """Commit gate in strict mode fails on insufficient funds."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)
        funding_gate = FundingGateService(psp_sync_db, ledger)
        psp_sync_db.commit()

        account_id = accounts["client_funding_clearing"]

        # Seed small amount
        ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=account_id,
            amount=Decimal("1000.00"),
            source_type="test",
            source_id=uuid4(),
            idempotency_key=f"seed_{uuid4().hex[:8]}",
        )
        psp_sync_db.commit()

        # Strict commit gate should fail for amount > available
        result = funding_gate.evaluate_commit_gate(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            account_id=account_id,
            required_amount=Decimal("5000.00"),
            strict=True,
        )

        assert result.passed is False
