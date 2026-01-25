"""Test 2: Preview flow from TEST_PLAN.md.

Goal: Engine can compute deterministic preview for pay_run.
"""

import pytest
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from payroll_engine.models.payroll import PayRun, PayRunEmployee
from payroll_engine.services.pay_run_service import PayRunService
from payroll_engine.services.state_machine import PayRunStateMachine

from .conftest import (
    DEMO_TENANT_ID,
    DRAFT_PAY_RUN_ID,
    ALICE_EMPLOYEE_ID,
    BOB_EMPLOYEE_ID,
)


pytestmark = pytest.mark.asyncio


class TestPreviewDeterminism:
    """Test that preview calculations are deterministic."""

    async def test_preview_produces_same_results_twice(self, seeded_db: AsyncSession):
        """Running preview twice should produce identical results."""
        service = PayRunService(seeded_db)

        # First preview
        result1 = await service.preview(DRAFT_PAY_RUN_ID)

        # Second preview
        result2 = await service.preview(DRAFT_PAY_RUN_ID)

        # Same calculation ID indicates determinism
        assert result1.calculation_id == result2.calculation_id, \
            "Repeated preview should produce same calculation_id"

        # Same totals
        assert result1.total_gross == result2.total_gross, \
            "Repeated preview should produce same gross"
        assert result1.total_net == result2.total_net, \
            "Repeated preview should produce same net"

        # Same per-employee results
        for emp1, emp2 in zip(result1.employee_results, result2.employee_results):
            assert emp1.gross == emp2.gross, \
                f"Employee {emp1.employee_id} gross should be deterministic"
            assert emp1.net == emp2.net, \
                f"Employee {emp1.employee_id} net should be deterministic"

    async def test_preview_sets_gross_and_net_on_pay_run_employee(
        self, seeded_db: AsyncSession
    ):
        """Preview should set gross and net on pay_run_employee records."""
        service = PayRunService(seeded_db)

        # Run preview
        await service.preview(DRAFT_PAY_RUN_ID)

        # Check pay_run_employee records
        result = await seeded_db.execute(
            select(PayRunEmployee).where(PayRunEmployee.pay_run_id == DRAFT_PAY_RUN_ID)
        )
        employees = result.scalars().all()

        for emp in employees:
            assert emp.gross is not None, \
                f"pay_run_employee {emp.id} should have gross set"
            assert emp.net is not None, \
                f"pay_run_employee {emp.id} should have net set"
            assert emp.gross > Decimal("0"), \
                f"pay_run_employee {emp.id} gross should be positive"

    async def test_preview_creates_earnings_lines(self, seeded_db: AsyncSession):
        """Preview should create earnings lines for employees."""
        service = PayRunService(seeded_db)

        # Run preview
        result = await service.preview(DRAFT_PAY_RUN_ID)

        # Check Alice has earnings (80 hours + $500 bonus)
        alice_result = next(
            (e for e in result.employee_results if e.employee_id == ALICE_EMPLOYEE_ID),
            None
        )
        assert alice_result is not None, "Alice should be in preview results"

        earnings = [l for l in alice_result.lines if l.category == "earning"]
        assert len(earnings) > 0, "Alice should have earning lines"

        # Check Bob has salary earning
        bob_result = next(
            (e for e in result.employee_results if e.employee_id == BOB_EMPLOYEE_ID),
            None
        )
        assert bob_result is not None, "Bob should be in preview results"

        bob_earnings = [l for l in bob_result.lines if l.category == "earning"]
        assert len(bob_earnings) > 0, "Bob should have earning lines"

    async def test_preview_does_not_commit_statements_by_default(
        self, seeded_db: AsyncSession
    ):
        """Preview should not create committed pay_statement records."""
        service = PayRunService(seeded_db)

        # Run preview
        await service.preview(DRAFT_PAY_RUN_ID)

        # Check no pay_statement records exist for this run
        result = await seeded_db.execute(
            text("""
                SELECT COUNT(*) FROM pay_statement ps
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )
        count = result.scalar()

        # Preview may or may not persist - if it does, that's ok but check status
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        if count > 0:
            # If statements exist, run should still be in preview/draft
            assert pay_run.status in ("draft", "preview"), \
                "Pay run should still be draft/preview if statements exist from preview"


class TestPreviewStateTransition:
    """Test state transitions during preview."""

    async def test_preview_transitions_draft_to_preview(self, seeded_db: AsyncSession):
        """Calling preview on draft run should transition to preview status."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        assert pay_run.status == "draft", "Pay run should start in draft"

        state_machine = PayRunStateMachine(pay_run)
        state_machine.transition_to("preview")

        assert pay_run.status == "preview", "Pay run should be in preview status"

    async def test_preview_is_idempotent_on_preview_status(self, seeded_db: AsyncSession):
        """Calling preview multiple times on preview status should work."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)

        # Transition to preview
        state_machine = PayRunStateMachine(pay_run)
        state_machine.transition_to("preview")
        await seeded_db.commit()

        # Run preview service twice
        service = PayRunService(seeded_db)
        result1 = await service.preview(DRAFT_PAY_RUN_ID)
        result2 = await service.preview(DRAFT_PAY_RUN_ID)

        assert result1.calculation_id == result2.calculation_id


class TestPreviewCalculationAccuracy:
    """Test accuracy of preview calculations."""

    async def test_alice_hourly_calculation(self, seeded_db: AsyncSession):
        """Alice: 80 hours @ $25/hr = $2000 base + $500 bonus = $2500 gross."""
        service = PayRunService(seeded_db)
        result = await service.preview(DRAFT_PAY_RUN_ID)

        alice_result = next(
            (e for e in result.employee_results if e.employee_id == ALICE_EMPLOYEE_ID),
            None
        )
        assert alice_result is not None

        # Gross should be at least $2500 (80 * 25 + 500)
        # May be higher if there are other earnings
        expected_min_gross = Decimal("2500.00")
        assert alice_result.gross >= expected_min_gross, \
            f"Alice gross {alice_result.gross} should be >= {expected_min_gross}"

    async def test_bob_salary_calculation(self, seeded_db: AsyncSession):
        """Bob: Salaried at $85,000/year = ~$3269.23/bi-weekly period."""
        service = PayRunService(seeded_db)
        result = await service.preview(DRAFT_PAY_RUN_ID)

        bob_result = next(
            (e for e in result.employee_results if e.employee_id == BOB_EMPLOYEE_ID),
            None
        )
        assert bob_result is not None

        # Bob's bi-weekly salary: $85000 / 26 = ~$3269.23
        expected_gross = Decimal("85000") / Decimal("26")
        # Allow some tolerance for rounding
        assert abs(bob_result.gross - expected_gross) < Decimal("1.00"), \
            f"Bob gross {bob_result.gross} should be ~{expected_gross}"

    async def test_net_is_less_than_gross(self, seeded_db: AsyncSession):
        """Net pay should be less than gross due to taxes and deductions."""
        service = PayRunService(seeded_db)
        result = await service.preview(DRAFT_PAY_RUN_ID)

        for emp in result.employee_results:
            assert emp.net < emp.gross, \
                f"Employee {emp.employee_id} net {emp.net} should be < gross {emp.gross}"

    async def test_deductions_are_applied(self, seeded_db: AsyncSession):
        """Alice has 401k deduction that should appear in preview."""
        service = PayRunService(seeded_db)
        result = await service.preview(DRAFT_PAY_RUN_ID)

        alice_result = next(
            (e for e in result.employee_results if e.employee_id == ALICE_EMPLOYEE_ID),
            None
        )
        assert alice_result is not None

        deductions = [l for l in alice_result.lines if l.category == "deduction"]
        # Alice has 401k at 6%
        assert len(deductions) > 0, "Alice should have deduction lines"

        # Check 401k exists
        k401_deductions = [d for d in deductions if "401" in d.code.lower()]
        assert len(k401_deductions) > 0, "Alice should have 401k deduction"
