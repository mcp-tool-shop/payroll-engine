"""Payment batch generation service."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from payroll_engine.models import (
    PaymentBatch,
    PaymentBatchItem,
    PayRun,
    PayRunEmployee,
    PayStatement,
)

if TYPE_CHECKING:
    pass


class PaymentService:
    """Service for generating payment batches.

    Payment batches are created for committed pay runs and contain
    individual payment items for each employee's net pay.

    Constraints:
    - One batch per pay_run + processor (idempotent)
    - Only committed/paid runs can have batches
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def generate_payment_batch(
        self,
        pay_run_id: UUID,
        processor: str = "stub",
    ) -> PaymentBatch:
        """Generate a payment batch for a pay run.

        Args:
            pay_run_id: The committed pay run
            processor: Payment processor name (default "stub" for Phase 1)

        Returns:
            The created or existing PaymentBatch

        Raises:
            ValueError: If pay run is not in committed/paid status
        """
        # Load pay run with employees and statements
        pay_run = await self._load_pay_run(pay_run_id)

        if pay_run is None:
            raise ValueError(f"Pay run {pay_run_id} not found")

        if pay_run.status not in ("committed", "paid"):
            raise ValueError(
                f"Cannot generate payment batch for pay run in status '{pay_run.status}'"
            )

        # Try to insert batch (idempotent)
        batch_insert = (
            insert(PaymentBatch)
            .values(
                pay_run_id=pay_run_id,
                processor=processor,
                status="created",
                total_amount=Decimal("0"),
            )
            .on_conflict_do_nothing(index_elements=["pay_run_id", "processor"])
        )
        await self.session.execute(batch_insert)

        # Get the batch (whether new or existing)
        batch_result = await self.session.execute(
            select(PaymentBatch).where(
                PaymentBatch.pay_run_id == pay_run_id,
                PaymentBatch.processor == processor,
            )
        )
        batch = batch_result.scalar_one()

        # Generate batch items for each statement
        total_amount = Decimal("0")

        for pre in pay_run.employees:
            if pre.status != "included" or pre.statement is None:
                continue

            statement = pre.statement
            if statement.net_pay <= 0:
                continue

            # Try to insert batch item (idempotent)
            item_insert = (
                insert(PaymentBatchItem)
                .values(
                    payment_batch_id=batch.payment_batch_id,
                    pay_statement_id=statement.pay_statement_id,
                    amount=statement.net_pay,
                    status="queued",
                )
                .on_conflict_do_nothing(
                    index_elements=["payment_batch_id", "pay_statement_id"]
                )
            )
            await self.session.execute(item_insert)

            total_amount += statement.net_pay

        # Update batch total
        batch.total_amount = total_amount

        return batch

    async def get_batch_summary(
        self, payment_batch_id: UUID
    ) -> dict[str, any]:
        """Get summary of a payment batch."""
        batch_result = await self.session.execute(
            select(PaymentBatch)
            .where(PaymentBatch.payment_batch_id == payment_batch_id)
            .options(selectinload(PaymentBatch.items))
        )
        batch = batch_result.scalar_one_or_none()

        if batch is None:
            raise ValueError(f"Payment batch {payment_batch_id} not found")

        items_by_status = {}
        for item in batch.items:
            status = item.status
            if status not in items_by_status:
                items_by_status[status] = {"count": 0, "amount": Decimal("0")}
            items_by_status[status]["count"] += 1
            items_by_status[status]["amount"] += item.amount

        return {
            "batch_id": batch.payment_batch_id,
            "pay_run_id": batch.pay_run_id,
            "processor": batch.processor,
            "status": batch.status,
            "total_amount": batch.total_amount,
            "item_count": len(batch.items),
            "items_by_status": items_by_status,
        }

    async def mark_batch_submitted(self, payment_batch_id: UUID) -> None:
        """Mark a batch as submitted to processor."""
        batch_result = await self.session.execute(
            select(PaymentBatch).where(
                PaymentBatch.payment_batch_id == payment_batch_id
            )
        )
        batch = batch_result.scalar_one()
        batch.status = "submitted"

    async def mark_batch_settled(self, payment_batch_id: UUID) -> None:
        """Mark a batch as settled (funds disbursed)."""
        batch_result = await self.session.execute(
            select(PaymentBatch)
            .where(PaymentBatch.payment_batch_id == payment_batch_id)
            .options(selectinload(PaymentBatch.items))
        )
        batch = batch_result.scalar_one()
        batch.status = "settled"

        # Mark all items as settled
        for item in batch.items:
            if item.status != "failed":
                item.status = "settled"

    async def _load_pay_run(self, pay_run_id: UUID) -> PayRun | None:
        """Load pay run with employees and statements."""
        result = await self.session.execute(
            select(PayRun)
            .where(PayRun.pay_run_id == pay_run_id)
            .options(
                selectinload(PayRun.employees).selectinload(PayRunEmployee.statement)
            )
        )
        return result.scalar_one_or_none()
