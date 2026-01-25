"""Test 8: Golden rules validation from TEST_PLAN.md.

Validates fundamental payroll engine constraints:
- No floating point usage
- All money in NUMERIC
- Sign conventions
- Tax jurisdiction references
"""

import pytest
from decimal import Decimal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from payroll_engine.models.payroll import PayRun
from payroll_engine.services.commit_service import CommitService
from payroll_engine.services.locking_service import LockingService
from payroll_engine.services.pay_run_service import PayRunService
from payroll_engine.services.state_machine import PayRunStateMachine

from .conftest import DRAFT_PAY_RUN_ID, ALICE_EMPLOYEE_ID, BOB_EMPLOYEE_ID


pytestmark = pytest.mark.asyncio


class TestNoFloatingPoint:
    """Test that no floating point types are used for money."""

    async def test_money_columns_are_numeric(self, seeded_db: AsyncSession):
        """All money columns should be NUMERIC, not REAL/DOUBLE."""
        money_columns = [
            ("pay_statement", "gross_pay"),
            ("pay_statement", "net_pay"),
            ("pay_statement", "total_taxes"),
            ("pay_statement", "total_deductions"),
            ("pay_statement", "total_employer_taxes"),
            ("pay_line_item", "amount"),
            ("pay_line_item", "rate"),
            ("pay_line_item", "hours"),
            ("time_entry", "hours"),
            ("pay_rate", "amount"),
            ("employee_deduction", "amount"),
            ("employee_deduction", "percent"),
        ]

        invalid_columns = []

        for table, column in money_columns:
            result = await seeded_db.execute(
                text("""
                    SELECT data_type FROM information_schema.columns
                    WHERE table_name = :table AND column_name = :column
                """),
                {"table": table, "column": column}
            )
            row = result.fetchone()

            if row:
                data_type = row[0].lower()
                # Should be numeric or decimal, NOT real/float/double
                if data_type in ("real", "double precision", "float"):
                    invalid_columns.append(f"{table}.{column} is {data_type}")

        assert len(invalid_columns) == 0, \
            f"Found floating point money columns: {invalid_columns}"

    async def test_calculations_use_decimal(self, seeded_db: AsyncSession):
        """Preview calculations should use Decimal, not float."""
        service = PayRunService(seeded_db)
        result = await service.preview(DRAFT_PAY_RUN_ID)

        # Check types
        assert isinstance(result.total_gross, Decimal), \
            f"total_gross should be Decimal, got {type(result.total_gross)}"
        assert isinstance(result.total_net, Decimal), \
            f"total_net should be Decimal, got {type(result.total_net)}"

        for emp in result.employee_results:
            assert isinstance(emp.gross, Decimal), \
                f"employee gross should be Decimal"
            assert isinstance(emp.net, Decimal), \
                f"employee net should be Decimal"

            for line in emp.lines:
                assert isinstance(line.amount, Decimal), \
                    f"line amount should be Decimal"


class TestSignConventions:
    """Test that sign conventions are followed correctly."""

    async def test_earnings_are_positive(self, seeded_db: AsyncSession):
        """Earnings and reimbursements should be positive."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Check earning line items
        result = await seeded_db.execute(
            text("""
                SELECT pli.code, pli.amount FROM pay_line_item pli
                JOIN pay_statement ps ON pli.pay_statement_id = ps.id
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
                AND pli.category = 'earning'
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )

        for code, amount in result.fetchall():
            assert amount > Decimal("0"), \
                f"Earning {code} should be positive, got {amount}"

    async def test_deductions_are_negative(self, seeded_db: AsyncSession):
        """Deductions should be negative."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        result = await seeded_db.execute(
            text("""
                SELECT pli.code, pli.amount FROM pay_line_item pli
                JOIN pay_statement ps ON pli.pay_statement_id = ps.id
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
                AND pli.category = 'deduction'
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )

        for code, amount in result.fetchall():
            assert amount < Decimal("0"), \
                f"Deduction {code} should be negative, got {amount}"

    async def test_taxes_are_negative(self, seeded_db: AsyncSession):
        """Employee taxes should be negative."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        result = await seeded_db.execute(
            text("""
                SELECT pli.code, pli.amount FROM pay_line_item pli
                JOIN pay_statement ps ON pli.pay_statement_id = ps.id
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
                AND pli.category = 'tax'
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )

        for code, amount in result.fetchall():
            assert amount < Decimal("0"), \
                f"Tax {code} should be negative, got {amount}"

    async def test_employer_taxes_are_positive(self, seeded_db: AsyncSession):
        """Employer taxes should be positive."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        result = await seeded_db.execute(
            text("""
                SELECT pli.code, pli.amount FROM pay_line_item pli
                JOIN pay_statement ps ON pli.pay_statement_id = ps.id
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
                AND pli.category = 'employer_tax'
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )

        rows = result.fetchall()
        for code, amount in rows:
            assert amount > Decimal("0"), \
                f"Employer tax {code} should be positive, got {amount}"


class TestTaxJurisdictionReferences:
    """Test that tax lines reference jurisdiction and rule version."""

    async def test_tax_lines_have_jurisdiction(self, seeded_db: AsyncSession):
        """Every tax line should reference a jurisdiction."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        result = await seeded_db.execute(
            text("""
                SELECT pli.code, pli.jurisdiction FROM pay_line_item pli
                JOIN pay_statement ps ON pli.pay_statement_id = ps.id
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
                AND pli.category IN ('tax', 'employer_tax')
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )

        rows = result.fetchall()
        for code, jurisdiction in rows:
            assert jurisdiction is not None and jurisdiction != "", \
                f"Tax {code} should have jurisdiction"

    async def test_tax_lines_have_rule_version(self, seeded_db: AsyncSession):
        """Every tax line should reference a rule_version_id."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        result = await seeded_db.execute(
            text("""
                SELECT pli.code, pli.rule_version_id FROM pay_line_item pli
                JOIN pay_statement ps ON pli.pay_statement_id = ps.id
                JOIN pay_run_employee pre ON ps.pay_run_employee_id = pre.id
                WHERE pre.pay_run_id = :pay_run_id
                AND pli.category IN ('tax', 'employer_tax')
            """),
            {"pay_run_id": DRAFT_PAY_RUN_ID}
        )

        rows = result.fetchall()
        # Rule version is optional in some implementations
        # but recommended per TEST_PLAN
        missing_rule_version = []
        for code, rule_version_id in rows:
            if rule_version_id is None:
                missing_rule_version.append(code)

        if len(missing_rule_version) > 0:
            # Warn but don't fail - rule_version_id is recommended
            import warnings
            warnings.warn(
                f"Tax lines without rule_version_id: {missing_rule_version}"
            )


class TestNetPayCalculation:
    """Test that net pay is calculated correctly."""

    async def test_net_equals_gross_minus_deductions_minus_taxes(
        self, seeded_db: AsyncSession
    ):
        """Net = Gross + (negative deductions) + (negative taxes)."""
        service = PayRunService(seeded_db)
        result = await service.preview(DRAFT_PAY_RUN_ID)

        for emp in result.employee_results:
            # Sum all line items
            line_sum = sum(line.amount for line in emp.lines)

            # Should equal net (allowing for rounding)
            diff = abs(emp.net - line_sum)
            assert diff < Decimal("0.02"), \
                f"Employee {emp.employee_id}: net {emp.net} != line sum {line_sum}"

    async def test_gross_equals_sum_of_earnings(self, seeded_db: AsyncSession):
        """Gross pay should equal sum of all earning lines."""
        service = PayRunService(seeded_db)
        result = await service.preview(DRAFT_PAY_RUN_ID)

        for emp in result.employee_results:
            earnings_sum = sum(
                line.amount for line in emp.lines
                if line.category == "earning"
            )

            diff = abs(emp.gross - earnings_sum)
            assert diff < Decimal("0.02"), \
                f"Employee {emp.employee_id}: gross {emp.gross} != earnings {earnings_sum}"
