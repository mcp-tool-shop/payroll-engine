"""GL (General Ledger) export service."""

from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from payroll_engine.models import (
    GLConfig,
    GLJournalBatch,
    GLJournalLine,
    GLMappingRule,
    PayLineItem,
    PayRun,
    PayRunEmployee,
    PayStatement,
)

if TYPE_CHECKING:
    pass


class GLService:
    """Service for generating GL journal entries.

    Creates journal entries from pay line items using GL mapping rules.
    Supports CSV export for Phase 1.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def generate_gl_batch(self, pay_run_id: UUID) -> GLJournalBatch:
        """Generate GL journal batch for a pay run.

        Returns the created GLJournalBatch with journal lines.
        """
        # Load pay run with all necessary relationships
        pay_run = await self._load_pay_run(pay_run_id)

        if pay_run is None:
            raise ValueError(f"Pay run {pay_run_id} not found")

        if pay_run.status not in ("committed", "paid"):
            raise ValueError(
                f"Cannot generate GL for pay run in status '{pay_run.status}'"
            )

        # Load GL config for the legal entity
        config = await self._get_gl_config(pay_run.legal_entity_id)
        if config is None:
            raise ValueError(
                f"No GL config found for legal entity {pay_run.legal_entity_id}"
            )

        # Create journal batch
        batch = GLJournalBatch(
            pay_run_id=pay_run_id,
            status="generated",
        )
        self.session.add(batch)
        await self.session.flush()  # Get batch ID

        # Generate journal lines from pay line items
        for pre in pay_run.employees:
            if pre.status != "included" or pre.statement is None:
                continue

            await self._generate_lines_for_statement(
                batch=batch,
                statement=pre.statement,
                config=config,
            )

        return batch

    async def export_to_csv(self, gl_batch_id: UUID) -> str:
        """Export GL batch to CSV format.

        Returns CSV content as a string.
        """
        batch_result = await self.session.execute(
            select(GLJournalBatch)
            .where(GLJournalBatch.gl_journal_batch_id == gl_batch_id)
            .options(
                selectinload(GLJournalBatch.lines),
                selectinload(GLJournalBatch.pay_run),
            )
        )
        batch = batch_result.scalar_one_or_none()

        if batch is None:
            raise ValueError(f"GL batch {gl_batch_id} not found")

        # Build CSV
        output = io.StringIO()
        writer = csv.writer(output)

        # Header
        writer.writerow([
            "Account",
            "Debit",
            "Credit",
            "Description",
            "Reference",
            "Date",
        ])

        # Lines
        check_date = (
            batch.pay_run.pay_period.check_date
            if batch.pay_run and batch.pay_run.pay_period
            else date.today()
        )

        for line in batch.lines:
            writer.writerow([
                line.account_string,
                str(line.debit) if line.debit > 0 else "",
                str(line.credit) if line.credit > 0 else "",
                f"Payroll {batch.pay_run_id}",
                str(line.gl_journal_line_id),
                check_date.isoformat(),
            ])

        # Mark as exported
        batch.status = "exported"

        return output.getvalue()

    async def _generate_lines_for_statement(
        self,
        batch: GLJournalBatch,
        statement: PayStatement,
        config: GLConfig,
    ) -> None:
        """Generate GL journal lines for a pay statement."""
        # Load line items for statement
        items_result = await self.session.execute(
            select(PayLineItem)
            .where(PayLineItem.pay_statement_id == statement.pay_statement_id)
            .options(
                selectinload(PayLineItem.earning_code),
                selectinload(PayLineItem.deduction_code),
            )
        )
        line_items = list(items_result.scalars().all())

        for item in line_items:
            # Find matching GL mapping rule
            rule = await self._find_mapping_rule(config, item)

            if rule is None:
                # Use default accounts if no specific rule
                debit_account = self._get_default_debit_account(item.line_type)
                credit_account = self._get_default_credit_account(item.line_type)
            else:
                debit_account = rule.debit_account
                credit_account = rule.credit_account

            # Create journal entries (always balanced debit/credit)
            amount = abs(item.amount)

            if amount == 0:
                continue

            # Determine which is debit vs credit based on line type
            if item.line_type in ("EARNING", "REIMBURSEMENT"):
                # Expense (debit) / Wages Payable (credit)
                self._add_journal_line(batch, debit_account, amount, Decimal("0"), item)
                self._add_journal_line(batch, credit_account, Decimal("0"), amount, item)

            elif item.line_type in ("DEDUCTION", "TAX"):
                # Wages Payable (debit) / Liability (credit)
                self._add_journal_line(batch, debit_account, amount, Decimal("0"), item)
                self._add_journal_line(batch, credit_account, Decimal("0"), amount, item)

            elif item.line_type == "EMPLOYER_TAX":
                # Tax Expense (debit) / Tax Payable (credit)
                self._add_journal_line(batch, debit_account, amount, Decimal("0"), item)
                self._add_journal_line(batch, credit_account, Decimal("0"), amount, item)

    def _add_journal_line(
        self,
        batch: GLJournalBatch,
        account: str,
        debit: Decimal,
        credit: Decimal,
        source_item: PayLineItem,
    ) -> None:
        """Add a journal line to the batch."""
        line = GLJournalLine(
            gl_journal_batch_id=batch.gl_journal_batch_id,
            account_string=account,
            debit=debit,
            credit=credit,
            source_pay_line_item_id=source_item.pay_line_item_id,
        )
        self.session.add(line)

    async def _get_gl_config(self, legal_entity_id: UUID) -> GLConfig | None:
        """Get GL config for a legal entity."""
        result = await self.session.execute(
            select(GLConfig)
            .where(GLConfig.legal_entity_id == legal_entity_id)
            .options(selectinload(GLConfig.mapping_rules))
        )
        return result.scalar_one_or_none()

    async def _find_mapping_rule(
        self, config: GLConfig, item: PayLineItem
    ) -> GLMappingRule | None:
        """Find the most specific mapping rule for a line item."""
        for rule in config.mapping_rules:
            if rule.line_type != item.line_type:
                continue

            # Check for specific code match
            if item.earning_code_id and rule.earning_code_id:
                if rule.earning_code_id == item.earning_code_id:
                    return rule
            elif item.deduction_code_id and rule.deduction_code_id:
                if rule.deduction_code_id == item.deduction_code_id:
                    return rule
            elif rule.earning_code_id is None and rule.deduction_code_id is None:
                # Generic rule for line type
                return rule

        return None

    def _get_default_debit_account(self, line_type: str) -> str:
        """Get default debit account for a line type."""
        defaults = {
            "EARNING": "6000-WAGES-EXP",
            "DEDUCTION": "2100-WAGES-PAY",
            "TAX": "2100-WAGES-PAY",
            "EMPLOYER_TAX": "6100-PAYROLL-TAX-EXP",
            "REIMBURSEMENT": "6200-REIMB-EXP",
            "ROUNDING": "6900-MISC-EXP",
        }
        return defaults.get(line_type, "9999-SUSPENSE")

    def _get_default_credit_account(self, line_type: str) -> str:
        """Get default credit account for a line type."""
        defaults = {
            "EARNING": "2100-WAGES-PAY",
            "DEDUCTION": "2200-DEDUCTION-PAY",
            "TAX": "2300-TAX-PAY",
            "EMPLOYER_TAX": "2300-TAX-PAY",
            "REIMBURSEMENT": "2100-WAGES-PAY",
            "ROUNDING": "2100-WAGES-PAY",
        }
        return defaults.get(line_type, "9999-SUSPENSE")

    async def _load_pay_run(self, pay_run_id: UUID) -> PayRun | None:
        """Load pay run with employees and statements."""
        result = await self.session.execute(
            select(PayRun)
            .where(PayRun.pay_run_id == pay_run_id)
            .options(
                selectinload(PayRun.employees).selectinload(PayRunEmployee.statement),
                selectinload(PayRun.pay_period),
            )
        )
        return result.scalar_one_or_none()
