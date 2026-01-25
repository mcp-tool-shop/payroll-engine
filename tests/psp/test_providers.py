"""Tests for PSP provider adapters (ACH and FedNow stubs).

Tests verify:
1. Provider capabilities correctly reported
2. Payment submission and tracking
3. Status transitions
4. ACH return simulation
5. FedNow instant settlement
"""

from datetime import date
from uuid import uuid4

import pytest

from payroll_engine.psp.providers.ach_stub import AchStubProvider
from payroll_engine.psp.providers.fednow_stub import FedNowStubProvider
from payroll_engine.psp.providers.base import RailCapabilities


class TestAchStubProvider:
    """Test ACH stub provider."""

    def test_capabilities(self):
        """ACH provider reports correct capabilities."""
        provider = AchStubProvider()
        caps = provider.capabilities()

        assert caps.ach_credit is True
        assert caps.ach_debit is True
        assert caps.fednow is False
        assert caps.rtp is False

    def test_submit_payment(self):
        """Submit payment returns accepted result."""
        provider = AchStubProvider()

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "1500.00",
            "idempotency_key": f"ach_test_{uuid4().hex[:8]}",
            "purpose": "employee_net",
            "payee_type": "employee",
            "payee_ref_id": str(uuid4()),
        }

        result = provider.submit(instruction)

        assert result.accepted is True
        assert result.provider_request_id is not None
        assert result.provider_request_id.startswith("ACHSTUB-")

    def test_get_status_after_submit(self):
        """Get status for newly submitted payment."""
        provider = AchStubProvider(auto_settle=False)

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "2000.00",
            "idempotency_key": f"status_test_{uuid4().hex[:8]}",
        }

        submit_result = provider.submit(instruction)
        status_result = provider.get_status(submit_result.provider_request_id)

        assert status_result.status == "accepted"

    def test_submit_auto_settles_by_default(self):
        """Auto-settle mode settles immediately."""
        provider = AchStubProvider(auto_settle=True)

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "2000.00",
            "idempotency_key": f"auto_settle_{uuid4().hex[:8]}",
        }

        submit_result = provider.submit(instruction)
        status_result = provider.get_status(submit_result.provider_request_id)

        assert status_result.status == "settled"

    def test_simulate_settlement(self):
        """Simulate ACH settlement after submission."""
        provider = AchStubProvider(auto_settle=False)

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "3000.00",
            "idempotency_key": f"settle_test_{uuid4().hex[:8]}",
        }

        submit_result = provider.submit(instruction)

        # Simulate settlement
        provider.simulate_settlement(submit_result.provider_request_id)

        status = provider.get_status(submit_result.provider_request_id)
        assert status.status == "settled"

    def test_simulate_return(self):
        """Simulate ACH return (NSF, etc.)."""
        provider = AchStubProvider(auto_settle=False)

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "1000.00",
            "idempotency_key": f"return_test_{uuid4().hex[:8]}",
        }

        submit_result = provider.submit(instruction)

        # Simulate return
        provider.simulate_return(submit_result.provider_request_id, return_code="R01")

        status = provider.get_status(submit_result.provider_request_id)
        assert status.status == "returned"

    def test_cancel_pending(self):
        """Cancel a pending ACH payment."""
        provider = AchStubProvider(auto_settle=False)

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "500.00",
            "idempotency_key": f"cancel_test_{uuid4().hex[:8]}",
        }

        submit_result = provider.submit(instruction)

        # Cancel before settlement
        cancel_result = provider.cancel(submit_result.provider_request_id)

        assert cancel_result.success is True

        # Verify status changed
        status = provider.get_status(submit_result.provider_request_id)
        assert status.status == "canceled"

    def test_cancel_settled_fails(self):
        """Cannot cancel already settled payment."""
        provider = AchStubProvider(auto_settle=True)

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "500.00",
            "idempotency_key": f"cancel_settled_{uuid4().hex[:8]}",
        }

        submit_result = provider.submit(instruction)

        # Try to cancel after settlement
        cancel_result = provider.cancel(submit_result.provider_request_id)

        assert cancel_result.success is False

    def test_reconcile_returns_settlements(self):
        """Reconcile returns settlement records."""
        provider = AchStubProvider(auto_settle=False)

        # Submit and settle a payment for today
        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "2500.00",
            "idempotency_key": f"recon_test_{uuid4().hex[:8]}",
            "requested_settlement_date": date.today(),
        }

        submit_result = provider.submit(instruction)
        provider.simulate_settlement(submit_result.provider_request_id, date.today())

        # Reconcile
        records = provider.reconcile(date.today())

        # Should include our settled payment - match by trace_id from submit result
        assert len(records) >= 1
        # Find the record that matches (trace_id is returned in submit result)
        assert any(r.status == "settled" for r in records)

    def test_get_status_not_found(self):
        """Get status for non-existent payment."""
        provider = AchStubProvider()

        status = provider.get_status("nonexistent_id")

        assert status.status == "unknown"


class TestFedNowStubProvider:
    """Test FedNow stub provider."""

    def test_capabilities(self):
        """FedNow provider reports correct capabilities."""
        provider = FedNowStubProvider()
        caps = provider.capabilities()

        assert caps.fednow is True
        assert caps.ach_credit is False
        assert caps.ach_debit is False
        assert caps.rtp is False

    def test_submit_instant_settlement(self):
        """FedNow submits with instant settlement."""
        provider = FedNowStubProvider()

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "1000.00",
            "idempotency_key": f"fednow_test_{uuid4().hex[:8]}",
            "purpose": "employee_net",
        }

        result = provider.submit(instruction)

        assert result.accepted is True
        assert result.provider_request_id.startswith("FEDNOW-")

        # FedNow settles instantly
        status = provider.get_status(result.provider_request_id)
        assert status.status == "settled"

    def test_submit_rejects_over_limit(self):
        """FedNow rejects payments over $500K limit."""
        provider = FedNowStubProvider()

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "600000.00",  # Over $500K
            "idempotency_key": f"fednow_limit_{uuid4().hex[:8]}",
        }

        result = provider.submit(instruction)

        assert result.accepted is False
        assert "limit" in result.message.lower()

    def test_simulate_reject(self):
        """Simulate FedNow rejection."""
        provider = FedNowStubProvider()

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "2000.00",
            "idempotency_key": f"fednow_reject_{uuid4().hex[:8]}",
        }

        result = provider.submit(instruction)

        # Simulate rejection
        provider.simulate_reject(result.provider_request_id, reason="Account frozen")

        status = provider.get_status(result.provider_request_id)
        assert status.status == "rejected"

    def test_reconcile_returns_instant_settlements(self):
        """Reconcile returns FedNow instant settlements."""
        provider = FedNowStubProvider()

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "5000.00",
            "idempotency_key": f"fednow_recon_{uuid4().hex[:8]}",
        }

        submit_result = provider.submit(instruction)

        records = provider.reconcile(date.today())

        # Should include instant settlement
        assert len(records) >= 1
        assert any(r.status == "settled" for r in records)

    def test_cancel_not_supported(self):
        """FedNow doesn't support cancellation (instant settlement)."""
        provider = FedNowStubProvider()

        instruction = {
            "payment_instruction_id": str(uuid4()),
            "amount": "1000.00",
            "idempotency_key": f"fednow_cancel_{uuid4().hex[:8]}",
        }

        submit_result = provider.submit(instruction)

        # Try to cancel - should fail because already settled
        cancel_result = provider.cancel(submit_result.provider_request_id)

        # Cancellation fails for settled payments
        assert cancel_result.success is False


class TestProviderProtocol:
    """Test that providers conform to protocol."""

    @pytest.mark.parametrize("provider_class", [AchStubProvider, FedNowStubProvider])
    def test_provider_has_required_methods(self, provider_class):
        """Providers implement required protocol methods."""
        provider = provider_class()

        assert hasattr(provider, "provider_name")
        assert callable(getattr(provider, "capabilities", None))
        assert callable(getattr(provider, "submit", None))
        assert callable(getattr(provider, "get_status", None))
        assert callable(getattr(provider, "cancel", None))
        assert callable(getattr(provider, "reconcile", None))

    @pytest.mark.parametrize("provider_class", [AchStubProvider, FedNowStubProvider])
    def test_capabilities_returns_rail_capabilities(self, provider_class):
        """Capabilities returns RailCapabilities instance."""
        provider = provider_class()
        caps = provider.capabilities()

        assert isinstance(caps, RailCapabilities)
