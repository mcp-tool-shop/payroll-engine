"""Tests for LedgerService - append-only double-entry ledger.

Tests verify:
1. Idempotent ledger posting (retries produce no duplicates)
2. Reversal-based corrections
3. Balance computation
4. Reservation creation and release
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from payroll_engine.psp.services.ledger_service import LedgerService, Balance, PostResult
from tests.psp.conftest import PSPTestData


class TestLedgerPosting:
    """Test ledger entry posting."""

    def test_post_entry_creates_entry(self, psp_sync_db: Session, test_data: PSPTestData):
        """Post entry creates a new ledger entry."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        result = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            idempotency_key="test_posting_001",
            entry_type="funding_received",
            debit_account_id=accounts["client_funding_clearing"],
            credit_account_id=accounts["psp_settlement_clearing"],
            amount=Decimal("10000.00"),
            source_type="funding_request",
            source_id=uuid4(),
        )

        assert result.entry_id is not None
        assert result.was_duplicate is False
        assert result.entry_type == "funding_received"

        # Verify entry exists in database
        row = psp_sync_db.execute(
            text("SELECT amount, entry_type FROM psp_ledger_entry WHERE psp_ledger_entry_id = :id"),
            {"id": str(result.entry_id)},
        ).fetchone()
        assert row is not None
        assert Decimal(str(row[0])) == Decimal("10000.00")
        assert row[1] == "funding_received"

    def test_post_entry_idempotent(self, psp_sync_db: Session, test_data: PSPTestData):
        """Posting with same idempotency key returns existing entry."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        # First post
        result1 = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            idempotency_key="idempotent_key_001",
            entry_type="funding_received",
            debit_account_id=accounts["client_funding_clearing"],
            credit_account_id=accounts["psp_settlement_clearing"],
            amount=Decimal("5000.00"),
            source_type="test",
            source_id=uuid4(),
        )
        psp_sync_db.commit()

        # Retry with same key
        result2 = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            idempotency_key="idempotent_key_001",
            entry_type="funding_received",  # Same key, could have different params
            debit_account_id=accounts["client_funding_clearing"],
            credit_account_id=accounts["psp_settlement_clearing"],
            amount=Decimal("9999.99"),  # Different amount - should be ignored
            source_type="test",
            source_id=uuid4(),
        )

        # Same entry returned
        assert result1.entry_id == result2.entry_id
        assert result2.was_duplicate is True

        # Only one entry in database
        count = psp_sync_db.execute(
            text("""
                SELECT COUNT(*) FROM psp_ledger_entry
                WHERE tenant_id = :tenant_id AND idempotency_key = :key
            """),
            {"tenant_id": str(test_data.tenant_id), "key": "idempotent_key_001"},
        ).scalar()
        assert count == 1

    def test_post_entry_rejects_negative_amount(self, psp_sync_db: Session, test_data: PSPTestData):
        """Posting negative amount raises error."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        with pytest.raises(ValueError, match="Amount must be positive"):
            ledger.post_entry(
                tenant_id=test_data.tenant_id,
                legal_entity_id=test_data.legal_entity_id,
                idempotency_key="negative_test",
                entry_type="funding_received",
                debit_account_id=accounts["client_funding_clearing"],
                credit_account_id=accounts["psp_settlement_clearing"],
                amount=Decimal("-100.00"),
                source_type="test",
                source_id=uuid4(),
            )


class TestLedgerReversal:
    """Test ledger entry reversals."""

    def test_reverse_entry_swaps_accounts(self, psp_sync_db: Session, test_data: PSPTestData):
        """Reversal swaps debit and credit accounts."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        # Create original entry
        original = ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            idempotency_key="original_entry",
            entry_type="funding_received",
            debit_account_id=accounts["client_funding_clearing"],
            credit_account_id=accounts["psp_settlement_clearing"],
            amount=Decimal("5000.00"),
            source_type="test",
            source_id=uuid4(),
        )
        psp_sync_db.commit()

        # Reverse it
        reversal = ledger.reverse_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            original_entry_id=original.entry_id,
            idempotency_key="reversal_entry",
            reason="Test reversal",
        )

        assert reversal.entry_id != original.entry_id
        assert reversal.entry_type == "reversal"
        assert reversal.was_duplicate is False

        # Verify reversal has swapped accounts
        row = psp_sync_db.execute(
            text("""
                SELECT debit_account_id, credit_account_id, amount
                FROM psp_ledger_entry WHERE psp_ledger_entry_id = :id
            """),
            {"id": str(reversal.entry_id)},
        ).fetchone()

        # Reversal should have psp_settlement_clearing as debit (was credit)
        assert str(row[0]) == str(accounts["psp_settlement_clearing"])
        assert str(row[1]) == str(accounts["client_funding_clearing"])
        assert Decimal(str(row[2])) == Decimal("5000.00")

    def test_reverse_entry_not_found(self, psp_sync_db: Session, test_data: PSPTestData):
        """Reversing non-existent entry raises error."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        with pytest.raises(ValueError, match="not found"):
            ledger.reverse_entry(
                tenant_id=test_data.tenant_id,
                legal_entity_id=test_data.legal_entity_id,
                original_entry_id=uuid4(),  # Non-existent
                idempotency_key="bad_reversal",
                reason="Should fail",
            )


class TestLedgerBalance:
    """Test balance computation."""

    def test_get_balance_empty(self, psp_sync_db: Session, test_data: PSPTestData):
        """Balance is zero for account with no entries."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        balance = ledger.get_balance(
            tenant_id=test_data.tenant_id,
            ledger_account_id=accounts["client_funding_clearing"],
        )

        assert balance.available == Decimal("0")
        assert balance.reserved == Decimal("0")

    def test_get_balance_credits_increase(self, psp_sync_db: Session, test_data: PSPTestData):
        """Credits to account increase balance."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        # Post funding received - credits client_funding_clearing
        # (In double-entry, funding_received debits settlement, credits funding)
        ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            idempotency_key="funding_001",
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("10000.00"),
            source_type="test",
            source_id=uuid4(),
        )
        psp_sync_db.commit()

        balance = ledger.get_balance(
            tenant_id=test_data.tenant_id,
            ledger_account_id=accounts["client_funding_clearing"],
        )

        assert balance.available == Decimal("10000.00")

    def test_get_balance_debits_decrease(self, psp_sync_db: Session, test_data: PSPTestData):
        """Debits from account decrease balance."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        # Add funding
        ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            idempotency_key="funding_002",
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("10000.00"),
            source_type="test",
            source_id=uuid4(),
        )

        # Initiate payment - debits funding
        ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            idempotency_key="payment_001",
            entry_type="employee_payment_initiated",
            debit_account_id=accounts["client_funding_clearing"],
            credit_account_id=accounts["client_net_pay_payable"],
            amount=Decimal("3000.00"),
            source_type="test",
            source_id=uuid4(),
        )
        psp_sync_db.commit()

        balance = ledger.get_balance(
            tenant_id=test_data.tenant_id,
            ledger_account_id=accounts["client_funding_clearing"],
        )

        assert balance.available == Decimal("7000.00")


class TestLedgerReservation:
    """Test fund reservations."""

    def test_create_reservation(self, psp_sync_db: Session, test_data: PSPTestData):
        """Create a fund reservation."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        reservation_id = ledger.create_reservation(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            reserve_type="net_pay",
            amount=Decimal("5000.00"),
            source_type="pay_run",
            source_id=uuid4(),
        )

        assert reservation_id is not None

        # Verify in database
        row = psp_sync_db.execute(
            text("SELECT amount, status, reserve_type FROM psp_reservation WHERE psp_reservation_id = :id"),
            {"id": str(reservation_id)},
        ).fetchone()
        assert row is not None
        assert Decimal(str(row[0])) == Decimal("5000.00")
        assert row[1] == "active"
        assert row[2] == "net_pay"

    def test_reservation_affects_balance(self, psp_sync_db: Session, test_data: PSPTestData):
        """Reservations are reflected in balance.reserved."""
        accounts = test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        # Add funding
        ledger.post_entry(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            idempotency_key="funding_res_001",
            entry_type="funding_received",
            debit_account_id=accounts["psp_settlement_clearing"],
            credit_account_id=accounts["client_funding_clearing"],
            amount=Decimal("10000.00"),
            source_type="test",
            source_id=uuid4(),
        )

        # Create reservation
        ledger.create_reservation(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            reserve_type="net_pay",
            amount=Decimal("3000.00"),
            source_type="pay_run",
            source_id=uuid4(),
        )
        psp_sync_db.commit()

        balance = ledger.get_balance(
            tenant_id=test_data.tenant_id,
            ledger_account_id=accounts["client_funding_clearing"],
        )

        assert balance.available == Decimal("10000.00")
        assert balance.reserved == Decimal("3000.00")
        assert balance.unreserved == Decimal("7000.00")

    def test_release_reservation(self, psp_sync_db: Session, test_data: PSPTestData):
        """Release a reservation."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        reservation_id = ledger.create_reservation(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            reserve_type="net_pay",
            amount=Decimal("5000.00"),
            source_type="pay_run",
            source_id=uuid4(),
        )
        psp_sync_db.commit()

        # Release it
        released = ledger.release_reservation(
            tenant_id=test_data.tenant_id,
            reservation_id=reservation_id,
            consumed=False,
        )

        assert released is True

        # Verify status changed
        row = psp_sync_db.execute(
            text("SELECT status FROM psp_reservation WHERE psp_reservation_id = :id"),
            {"id": str(reservation_id)},
        ).fetchone()
        assert row[0] == "released"

    def test_release_reservation_consumed(self, psp_sync_db: Session, test_data: PSPTestData):
        """Consume a reservation."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        reservation_id = ledger.create_reservation(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            reserve_type="net_pay",
            amount=Decimal("5000.00"),
            source_type="pay_run",
            source_id=uuid4(),
        )
        psp_sync_db.commit()

        # Consume it
        consumed = ledger.release_reservation(
            tenant_id=test_data.tenant_id,
            reservation_id=reservation_id,
            consumed=True,
        )

        assert consumed is True

        row = psp_sync_db.execute(
            text("SELECT status FROM psp_reservation WHERE psp_reservation_id = :id"),
            {"id": str(reservation_id)},
        ).fetchone()
        assert row[0] == "consumed"

    def test_create_reservation_invalid_type(self, psp_sync_db: Session, test_data: PSPTestData):
        """Invalid reserve type raises error."""
        test_data.create_ledger_accounts(psp_sync_db)
        ledger = LedgerService(psp_sync_db)

        with pytest.raises(ValueError, match="Invalid reserve_type"):
            ledger.create_reservation(
                tenant_id=test_data.tenant_id,
                legal_entity_id=test_data.legal_entity_id,
                reserve_type="invalid_type",
                amount=Decimal("1000.00"),
                source_type="test",
                source_id=uuid4(),
            )


class TestGetOrCreateAccount:
    """Test account get-or-create functionality."""

    def test_get_or_create_new_account(self, psp_sync_db: Session, test_data: PSPTestData):
        """Creates account if it doesn't exist."""
        ledger = LedgerService(psp_sync_db)

        account_id = ledger.get_or_create_account(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            account_type="client_funding_clearing",
        )

        assert account_id is not None

    def test_get_or_create_existing_account(self, psp_sync_db: Session, test_data: PSPTestData):
        """Returns existing account if it exists."""
        ledger = LedgerService(psp_sync_db)

        # Create account
        account_id_1 = ledger.get_or_create_account(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            account_type="client_funding_clearing",
        )
        psp_sync_db.commit()

        # Get same account
        account_id_2 = ledger.get_or_create_account(
            tenant_id=test_data.tenant_id,
            legal_entity_id=test_data.legal_entity_id,
            account_type="client_funding_clearing",
        )

        assert account_id_1 == account_id_2
