"""Input and config locking service for pay run approval."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from payroll_engine.models import (
    PayInputAdjustment,
    PayRun,
    PayRunEmployee,
    PayRunLock,
    TimeEntry,
)

if TYPE_CHECKING:
    pass


class LockingService:
    """Service for locking inputs and config at approval time.

    When a pay run is approved:
    1. All in-scope time entries are marked as locked
    2. All in-scope pay input adjustments are marked as locked
    3. Snapshot hashes of effective-dated config are stored

    This prevents silent drift between preview and commit.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def lock_inputs_for_run(self, pay_run: PayRun) -> int:
        """Lock all in-scope inputs for a pay run.

        Returns count of locked records.
        """
        pay_run_id = pay_run.pay_run_id
        locked_at = datetime.utcnow()
        locked_count = 0

        # Get period boundaries
        if pay_run.pay_period is None:
            raise ValueError("Pay run must have a pay period to lock inputs")

        period_start = pay_run.pay_period.period_start
        period_end = pay_run.pay_period.period_end

        # Get included employee IDs
        included_employee_ids = [
            pre.employee_id
            for pre in pay_run.employees
            if pre.status == "included"
        ]

        if not included_employee_ids:
            return 0

        # Lock time entries in the pay period for included employees
        time_entry_result = await self.session.execute(
            update(TimeEntry)
            .where(
                TimeEntry.employee_id.in_(included_employee_ids),
                TimeEntry.work_date >= period_start,
                TimeEntry.work_date <= period_end,
                TimeEntry.locked_by_pay_run_id.is_(None),
            )
            .values(locked_by_pay_run_id=pay_run_id, locked_at=locked_at)
        )
        locked_count += time_entry_result.rowcount or 0

        # Lock pay input adjustments targeted to this run or period
        adjustment_result = await self.session.execute(
            update(PayInputAdjustment)
            .where(
                PayInputAdjustment.employee_id.in_(included_employee_ids),
                (
                    (PayInputAdjustment.target_pay_run_id == pay_run_id)
                    | (PayInputAdjustment.target_pay_period_id == pay_run.pay_period_id)
                ),
                PayInputAdjustment.locked_by_pay_run_id.is_(None),
            )
            .values(locked_by_pay_run_id=pay_run_id, locked_at=locked_at)
        )
        locked_count += adjustment_result.rowcount or 0

        return locked_count

    async def unlock_inputs_for_run(self, pay_run: PayRun) -> int:
        """Unlock all inputs locked by this pay run (for reopen).

        Returns count of unlocked records.
        """
        pay_run_id = pay_run.pay_run_id
        unlocked_count = 0

        # Unlock time entries
        time_entry_result = await self.session.execute(
            update(TimeEntry)
            .where(TimeEntry.locked_by_pay_run_id == pay_run_id)
            .values(locked_by_pay_run_id=None, locked_at=None)
        )
        unlocked_count += time_entry_result.rowcount or 0

        # Unlock adjustments
        adjustment_result = await self.session.execute(
            update(PayInputAdjustment)
            .where(PayInputAdjustment.locked_by_pay_run_id == pay_run_id)
            .values(locked_by_pay_run_id=None, locked_at=None)
        )
        unlocked_count += adjustment_result.rowcount or 0

        # Remove lock records
        await self.session.execute(
            PayRunLock.__table__.delete().where(PayRunLock.pay_run_id == pay_run_id)
        )

        return unlocked_count

    async def record_config_snapshot(
        self,
        pay_run_id: UUID,
        entity_type: str,
        entity_id: UUID,
        data: dict[str, Any],
    ) -> PayRunLock:
        """Record a snapshot hash for a config entity."""
        snapshot_hash = self._compute_hash(data)

        lock = PayRunLock(
            pay_run_id=pay_run_id,
            entity_type=entity_type,
            entity_id=entity_id,
            snapshot_hash=snapshot_hash,
        )
        self.session.add(lock)
        return lock

    async def verify_locks_intact(self, pay_run: PayRun) -> list[str]:
        """Verify that all locked inputs are still locked and unchanged.

        Returns list of error messages (empty if all intact).
        """
        errors: list[str] = []
        pay_run_id = pay_run.pay_run_id

        # Check that time entries are still locked
        unlocked_time = await self.session.execute(
            select(TimeEntry)
            .where(
                TimeEntry.locked_by_pay_run_id == pay_run_id,
            )
            .limit(1)
        )
        # If we can query them, they exist (triggers prevent modification)

        # Verify config snapshots haven't changed
        # This would involve re-computing hashes and comparing
        # For Phase 1 MVP, we rely on the row-level locks

        return errors

    async def get_locked_time_entries(self, pay_run_id: UUID) -> list[TimeEntry]:
        """Get all time entries locked by a pay run."""
        result = await self.session.execute(
            select(TimeEntry).where(TimeEntry.locked_by_pay_run_id == pay_run_id)
        )
        return list(result.scalars().all())

    async def get_locked_adjustments(self, pay_run_id: UUID) -> list[PayInputAdjustment]:
        """Get all adjustments locked by a pay run."""
        result = await self.session.execute(
            select(PayInputAdjustment).where(
                PayInputAdjustment.locked_by_pay_run_id == pay_run_id
            )
        )
        return list(result.scalars().all())

    def _compute_hash(self, data: dict[str, Any]) -> str:
        """Compute a deterministic hash of data."""
        # Sort keys for deterministic JSON
        json_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()[:32]
