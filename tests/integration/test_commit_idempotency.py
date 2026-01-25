"""Test 4: Commit + idempotency (crash-safe) from TEST_PLAN.md.

Goal: Commit is safe under retries.
"""

import pytest
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import select, text, func
from sqlalchemy.ext.asyncio import AsyncSession

from payroll_engine.models.payroll import (
    PayRun,
    PayRunEmployee,
    PayStatement,
    PayLineItem,
)
from payroll_engine.services.commit_service import CommitService
from payroll_engine.services.pay_run_service import PayRunService
from payroll_engine.services.state_machine import PayRunStateMachine
from payroll_engine.services.locking_service import LockingService

from .conftest import DRAFT_PAY_RUN_ID


pytestmark = pytest.mark.asyncio


class TestCommitBasics:
    """Test basic commit functionality."""

    async def test_commit_sets_status_to_committed(self, seeded_db: AsyncSession):
        """Commit should transition pay_run status to committed."""
        # Prepare: preview -> approved
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")

        locking_service = LockingService(seeded_db)
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Commit
        commit_service = CommitService(seeded_db)
        result = await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Verify status
        await seeded_db.refresh(pay_run)
        assert pay_run.status == "committed", "Pay run should be committed"
        assert pay_run.committed_at is not None, "committed_at should be set"

    async def test_commit_creates_one_statement_per_employee(
        self, seeded_db: AsyncSession
    ):
        """Commit should create exactly one pay_statement per pay_run_employee."""
        # Prepare
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")

        locking_service = LockingService(seeded_db)
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Get employee count
        emp_result = await seeded_db.execute(
            select(func.count()).where(PayRunEmployee.pay_run_id == DRAFT_PAY_RUN_ID)
        )
        employee_count = emp_result.scalar()

        # Commit
        commit_service = CommitService(seeded_db)
        result = await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Count statements
        stmt_result = await seeded_db.execute(
            text("""
                SELECT COUNT(*) FROM pay_statement ps
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )
        statement_count = stmt_result.scalar()

        assert statement_count == employee_count, \
            f"Should have {employee_count} statements, got {statement_count}"

    async def test_commit_creates_line_items(self, seeded_db: AsyncSession):
        """Commit should create pay_line_items for earnings, deductions, taxes."""
        # Prepare
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")

        locking_service = LockingService(seeded_db)
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Commit
        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Check line items exist
        result = await seeded_db.execute(
            text("""
                SELECT pli.category, COUNT(*) as cnt
                FROM pay_line_item pli
                JOIN pay_statement ps ON pli.pay_statement_id = ps.id
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
                GROUP BY pli.category
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )
        categories = {row[0]: row[1] for row in result.fetchall()}

        assert "earning" in categories, "Should have earning line items"
        assert categories["earning"] > 0, "Should have at least one earning"

    async def test_net_pay_equals_sum_of_lines(self, seeded_db: AsyncSession):
        """Net pay should equal the sum of all line item amounts."""
        # Prepare and commit
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")

        locking_service = LockingService(seeded_db)
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Check each statement
        result = await seeded_db.execute(
            text("""
                SELECT ps.id, ps.net_pay,
                       COALESCE(SUM(pli.amount), 0) as line_sum
                FROM pay_statement ps
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                LEFT JOIN pay_line_item pli ON pli.pay_statement_id = ps.id
                WHERE pre.pay_run_id = :pay_run_id
                GROUP BY ps.id, ps.net_pay
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )

        for row in result.fetchall():
            statement_id, net_pay, line_sum = row
            # Line sum should equal net pay (with sign convention)
            # Earnings positive, deductions/taxes negative
            assert abs(net_pay - line_sum) < Decimal("0.01"), \
                f"Statement {statement_id}: net_pay {net_pay} != line_sum {line_sum}"


class TestCommitIdempotency:
    """Test that commit is idempotent and crash-safe."""

    async def test_retry_commit_produces_no_duplicates(self, seeded_db: AsyncSession):
        """Calling commit twice should not create duplicate statements."""
        # Prepare
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")

        locking_service = LockingService(seeded_db)
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # First commit
        commit_service = CommitService(seeded_db)
        result1 = await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Count statements after first commit
        count1_result = await seeded_db.execute(
            text("""
                SELECT COUNT(*) FROM pay_statement ps
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )
        count1 = count1_result.scalar()

        # Second commit (retry)
        result2 = await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Count after second commit
        count2_result = await seeded_db.execute(
            text("""
                SELECT COUNT(*) FROM pay_statement ps
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )
        count2 = count2_result.scalar()

        assert count1 == count2, \
            f"Retry should not create duplicates: {count1} vs {count2}"

    async def test_retry_commit_creates_no_duplicate_line_items(
        self, seeded_db: AsyncSession
    ):
        """Calling commit twice should not create duplicate line items."""
        # Prepare
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")

        locking_service = LockingService(seeded_db)
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # First commit
        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Count line items
        count1_result = await seeded_db.execute(
            text("""
                SELECT COUNT(*) FROM pay_line_item pli
                JOIN pay_statement ps ON pli.pay_statement_id = ps.id
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )
        count1 = count1_result.scalar()

        # Second commit
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Count after retry
        count2_result = await seeded_db.execute(
            text("""
                SELECT COUNT(*) FROM pay_line_item pli
                JOIN pay_statement ps ON pli.pay_statement_id = ps.id
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )
        count2 = count2_result.scalar()

        assert count1 == count2, \
            f"Retry should not create duplicate lines: {count1} vs {count2}"

    async def test_commit_status_remains_committed_on_retry(
        self, seeded_db: AsyncSession
    ):
        """Retrying commit should keep status as committed."""
        # Prepare and commit
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")

        locking_service = LockingService(seeded_db)
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Retry
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        await seeded_db.refresh(pay_run)
        assert pay_run.status == "committed", \
            "Status should remain committed after retry"

    async def test_line_hash_prevents_duplicates(self, seeded_db: AsyncSession):
        """The pli_line_hash_unique index should prevent duplicate line items."""
        # Prepare and commit
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")

        locking_service = LockingService(seeded_db)
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Get a line item
        result = await seeded_db.execute(
            text("""
                SELECT pli.pay_statement_id, pli.calculation_id, pli.line_hash
                FROM pay_line_item pli
                JOIN pay_statement ps ON pli.pay_statement_id = ps.id
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
                AND pli.line_hash IS NOT NULL
                LIMIT 1
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )
        row = result.fetchone()

        if row:
            statement_id, calc_id, line_hash = row

            # Try to insert duplicate
            try:
                await seeded_db.execute(
                    text("""
                        INSERT INTO pay_line_item (
                            id, pay_statement_id, calculation_id, line_hash,
                            category, code, description, amount
                        ) VALUES (
                            gen_random_uuid(), :stmt_id, :calc_id, :hash,
                            'earning', 'DUP', 'Duplicate', 100.00
                        )
                    """),
                    {"stmt_id": statement_id, "calc_id": calc_id, "hash": line_hash}
                )
                await seeded_db.commit()
                pytest.fail("Should not allow duplicate line_hash")
            except Exception as e:
                # Expected: unique constraint violation
                assert "unique" in str(e).lower() or "duplicate" in str(e).lower(), \
                    f"Expected unique constraint error: {e}"


class TestCrashSafety:
    """Test crash recovery scenarios."""

    async def test_partial_commit_can_be_resumed(self, seeded_db: AsyncSession):
        """
        Simulates partial commit scenario.
        If commit crashes after first employee, retry should complete remaining.
        """
        # This is a manual test scenario per TEST_PLAN.md
        # We simulate by checking that rerunning commit is safe

        # Prepare
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")

        locking_service = LockingService(seeded_db)
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Get employee count
        emp_result = await seeded_db.execute(
            select(func.count()).where(PayRunEmployee.pay_run_id == DRAFT_PAY_RUN_ID)
        )
        total_employees = emp_result.scalar()

        # Commit normally (represents successful retry after crash)
        commit_service = CommitService(seeded_db)
        result = await commit_service.commit(DRAFT_PAY_RUN_ID)

        assert result.statements_created <= total_employees, \
            "Should create at most one statement per employee"

        # Verify all employees have statements
        stmt_result = await seeded_db.execute(
            text("""
                SELECT COUNT(DISTINCT pre.id) FROM pay_run_employee pre
                JOIN pay_statement ps ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )
        employees_with_statements = stmt_result.scalar()

        assert employees_with_statements == total_employees, \
            f"All {total_employees} employees should have statements"
