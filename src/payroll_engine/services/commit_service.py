"""Idempotent commit service for pay statements and line items."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from payroll_engine.calculators.line_builder import LineItemBuilder
from payroll_engine.calculators.types import LineCandidate
from payroll_engine.models import PayLineItem, PayRun, PayRunEmployee, PayStatement

if TYPE_CHECKING:
    from payroll_engine.calculators.engine import CalculationResult


class CalculationMismatchError(Exception):
    """Raised when existing statement has different calculation ID."""

    def __init__(
        self,
        pay_run_employee_id: UUID,
        existing_calc_id: UUID,
        new_calc_id: UUID,
    ):
        self.pay_run_employee_id = pay_run_employee_id
        self.existing_calc_id = existing_calc_id
        self.new_calc_id = new_calc_id
        super().__init__(
            f"Statement for {pay_run_employee_id} exists with calculation_id "
            f"{existing_calc_id}, but attempted to write {new_calc_id}. "
            "Requires reopen/void path."
        )


class CommitService:
    """Service for idempotent statement and line item persistence.

    Key invariants:
    1. One pay_statement per pay_run_employee (enforced by unique constraint)
    2. Line items are deduplicated by (statement_id, calculation_id, line_hash)
    3. Retries are safe - existing records are skipped by ON CONFLICT DO NOTHING
    4. If statement exists with different calculation_id, abort (data has changed)
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def commit_all_statements(
        self,
        pay_run: PayRun,
        calculation_results: dict[UUID, CalculationResult],
    ) -> int:
        """Commit statements for all employees in a pay run.

        Returns count of statements committed (may be 0 if all exist).
        """
        committed_count = 0

        for pre in pay_run.employees:
            if pre.status != "included":
                continue

            result = calculation_results.get(pre.employee_id)
            if result is None or not result.success:
                continue

            # Get check date from period
            check_date = pay_run.pay_period.check_date if pay_run.pay_period else date.today()

            # Commit statement and lines
            committed = await self.commit_statement(
                pay_run_employee=pre,
                check_date=check_date,
                calculation_result=result,
            )
            if committed:
                committed_count += 1

        return committed_count

    async def commit_statement(
        self,
        pay_run_employee: PayRunEmployee,
        check_date: date,
        calculation_result: CalculationResult,
    ) -> bool:
        """Commit a single statement with its line items.

        Returns True if new statement was created, False if already exists.

        Raises:
            CalculationMismatchError: If existing statement has different calculation_id
        """
        pre_id = pay_run_employee.pay_run_employee_id
        calc_id = calculation_result.calculation_id

        # Try to insert statement (idempotent)
        stmt_insert = (
            insert(PayStatement)
            .values(
                pay_run_employee_id=pre_id,
                check_date=check_date,
                payment_method="ach",  # Default for Phase 1
                statement_status="issued",
                net_pay=calculation_result.net,
                calculation_id=calc_id,
            )
            .on_conflict_do_nothing(index_elements=["pay_run_employee_id"])
        )
        result = await self.session.execute(stmt_insert)

        # Check if we inserted or if it existed
        if result.rowcount == 0:
            # Statement exists - verify calculation_id matches
            existing = await self.session.execute(
                select(PayStatement).where(
                    PayStatement.pay_run_employee_id == pre_id
                )
            )
            existing_stmt = existing.scalar_one()

            if existing_stmt.calculation_id != calc_id:
                raise CalculationMismatchError(
                    pre_id, existing_stmt.calculation_id, calc_id
                )

            # Same calculation - lines should already exist, but ensure they do
            statement_id = existing_stmt.pay_statement_id
            is_new = False
        else:
            # New statement - get its ID
            get_stmt = await self.session.execute(
                select(PayStatement).where(
                    PayStatement.pay_run_employee_id == pre_id
                )
            )
            statement = get_stmt.scalar_one()
            statement_id = statement.pay_statement_id
            is_new = True

        # Insert line items (idempotent via unique index on line_hash)
        await self._commit_line_items(
            statement_id=statement_id,
            calculation_id=calc_id,
            lines=calculation_result.lines,
        )

        # Update pay_run_employee totals
        pay_run_employee.gross = calculation_result.gross
        pay_run_employee.net = calculation_result.net

        return is_new

    async def _commit_line_items(
        self,
        statement_id: UUID,
        calculation_id: UUID,
        lines: list[LineCandidate],
    ) -> int:
        """Commit line items for a statement.

        Returns count of lines inserted (may be 0 if all exist).
        """
        if not lines:
            return 0

        inserted_count = 0

        for line in lines:
            line_hash = LineItemBuilder.compute_line_hash(line)

            # Use ON CONFLICT DO NOTHING for idempotency
            line_insert = (
                insert(PayLineItem)
                .values(
                    pay_statement_id=statement_id,
                    line_type=line.line_type.value,
                    earning_code_id=line.earning_code_id,
                    deduction_code_id=line.deduction_code_id,
                    tax_agency_id=line.tax_agency_id,
                    jurisdiction_id=line.jurisdiction_id,
                    quantity=line.quantity,
                    rate=line.rate,
                    amount=line.amount,
                    taxability_flags_json=line.taxability_flags,
                    source_input_id=line.source_input_id,
                    rule_id=line.rule_id,
                    rule_version_id=line.rule_version_id,
                    explanation=line.explanation,
                    calculation_id=calculation_id,
                    line_hash=line_hash,
                )
                .on_conflict_do_nothing(
                    index_elements=["pay_statement_id", "calculation_id", "line_hash"]
                )
            )
            result = await self.session.execute(line_insert)
            inserted_count += result.rowcount or 0

        return inserted_count

    async def verify_statement_integrity(
        self, statement_id: UUID
    ) -> tuple[bool, list[str]]:
        """Verify that statement's line items sum correctly.

        Returns (is_valid, list_of_errors).
        """
        errors: list[str] = []

        # Get statement
        stmt_result = await self.session.execute(
            select(PayStatement).where(PayStatement.pay_statement_id == statement_id)
        )
        statement = stmt_result.scalar_one_or_none()

        if statement is None:
            return False, ["Statement not found"]

        # Get all line items
        lines_result = await self.session.execute(
            select(PayLineItem).where(PayLineItem.pay_statement_id == statement_id)
        )
        lines = list(lines_result.scalars().all())

        # Calculate net from lines (excluding employer taxes)
        calculated_net = sum(
            line.amount
            for line in lines
            if line.line_type != "EMPLOYER_TAX"
        )

        # Compare to statement net
        if calculated_net != statement.net_pay:
            errors.append(
                f"Net mismatch: statement shows {statement.net_pay}, "
                f"lines sum to {calculated_net}"
            )

        return len(errors) == 0, errors
