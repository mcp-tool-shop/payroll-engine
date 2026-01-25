"""Pay run service - main orchestrator for payroll operations."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from payroll_engine.database import acquire_advisory_lock, release_advisory_lock
from payroll_engine.models import (
    AuditEvent,
    PayRun,
    PayRunEmployee,
    PayStatement,
)
from payroll_engine.services.locking_service import LockingService
from payroll_engine.services.state_machine import (
    InvalidTransitionError,
    PayRunStateMachine,
    PayRunStatus,
)

if TYPE_CHECKING:
    from payroll_engine.calculators.engine import CalculationResult


class PayRunService:
    """Service for managing pay run lifecycle.

    Operations:
    - preview_pay_run: Calculate gross/net for all employees
    - approve_pay_run: Lock inputs and transition to approved
    - commit_pay_run: Persist statements/line items and finalize
    - reopen_pay_run: Transition approved → preview, unlocking inputs
    - void_pay_run: Mark as voided with reversal mechanics
    """

    def __init__(self, session: AsyncSession):
        self.session = session
        self.locking_service = LockingService(session)

    async def get_pay_run(
        self,
        pay_run_id: UUID,
        load_employees: bool = True,
        load_period: bool = True,
    ) -> PayRun | None:
        """Load a pay run with optional relationships."""
        options = []
        if load_employees:
            options.append(selectinload(PayRun.employees))
        if load_period:
            options.append(selectinload(PayRun.pay_period))

        result = await self.session.execute(
            select(PayRun).where(PayRun.pay_run_id == pay_run_id).options(*options)
        )
        return result.scalar_one_or_none()

    async def transition_status(
        self,
        pay_run: PayRun,
        to_status: str,
        actor_user_id: UUID | None = None,
        reason: str | None = None,
    ) -> PayRun:
        """Transition a pay run to a new status.

        Handles all side effects of transitions:
        - approved: lock inputs
        - preview (from approved): unlock inputs, increment reopen_count
        - committed: set committed_at
        - voided: requires reason

        Raises InvalidTransitionError if transition is not allowed.
        """
        from_status = pay_run.status

        # Validate transition
        errors = PayRunStateMachine.validate_pay_run_for_transition(pay_run, to_status)
        if errors:
            raise InvalidTransitionError(from_status, to_status, "; ".join(errors))

        # Handle transition side effects
        if to_status == PayRunStatus.APPROVED:
            await self._handle_approval(pay_run, actor_user_id)

        elif PayRunStateMachine.is_reopen(from_status, to_status):
            await self._handle_reopen(pay_run)

        elif to_status == PayRunStatus.COMMITTED:
            pay_run.committed_at = datetime.utcnow()

        elif to_status == PayRunStatus.VOIDED:
            if not reason:
                raise InvalidTransitionError(from_status, to_status, "Void requires a reason")

        # Update status
        old_status = pay_run.status
        pay_run.status = to_status

        # Record audit event
        await self._record_audit(
            pay_run=pay_run,
            action=f"status_change:{old_status}:{to_status}",
            actor_user_id=actor_user_id,
            details={"reason": reason} if reason else None,
        )

        return pay_run

    async def approve_pay_run(
        self,
        pay_run_id: UUID,
        actor_user_id: UUID | None = None,
    ) -> PayRun:
        """Approve a pay run, locking all inputs."""
        pay_run = await self.get_pay_run(pay_run_id)
        if pay_run is None:
            raise ValueError(f"Pay run {pay_run_id} not found")

        return await self.transition_status(
            pay_run, PayRunStatus.APPROVED, actor_user_id
        )

    async def commit_pay_run(
        self,
        pay_run_id: UUID,
        calculation_results: dict[UUID, CalculationResult],
        actor_user_id: UUID | None = None,
    ) -> PayRun:
        """Commit a pay run with idempotent statement persistence.

        Args:
            pay_run_id: The pay run to commit
            calculation_results: Results from the calculation engine
            actor_user_id: User performing the commit

        Returns:
            The committed pay run

        This method:
        1. Acquires advisory lock for concurrency control
        2. Validates status is approved and locks are intact
        3. Persists statements/line items idempotently
        4. Finalizes run status with conditional update
        """
        # Import here to avoid circular imports
        from payroll_engine.services.commit_service import CommitService

        pay_run = await self.get_pay_run(pay_run_id)
        if pay_run is None:
            raise ValueError(f"Pay run {pay_run_id} not found")

        # Validate status
        if pay_run.status != PayRunStatus.APPROVED:
            raise InvalidTransitionError(
                pay_run.status,
                PayRunStatus.COMMITTED,
                f"Pay run must be approved to commit (current: {pay_run.status})",
            )

        # Acquire advisory lock
        lock_acquired = await acquire_advisory_lock(self.session, str(pay_run_id))
        if not lock_acquired:
            raise RuntimeError(f"Could not acquire lock for pay run {pay_run_id}")

        try:
            # Verify locks are intact
            lock_errors = await self.locking_service.verify_locks_intact(pay_run)
            if lock_errors:
                raise InvalidTransitionError(
                    pay_run.status,
                    PayRunStatus.COMMITTED,
                    f"Lock verification failed: {'; '.join(lock_errors)}",
                )

            # Check no employees have errors
            error_employees = [e for e in pay_run.employees if e.status == "error"]
            if error_employees:
                raise InvalidTransitionError(
                    pay_run.status,
                    PayRunStatus.COMMITTED,
                    f"{len(error_employees)} employee(s) have errors",
                )

            # Persist statements idempotently
            commit_service = CommitService(self.session)
            await commit_service.commit_all_statements(pay_run, calculation_results)

            # Finalize run status with conditional update
            result = await self.session.execute(
                update(PayRun)
                .where(
                    PayRun.pay_run_id == pay_run_id,
                    PayRun.status == PayRunStatus.APPROVED,
                )
                .values(
                    status=PayRunStatus.COMMITTED,
                    committed_at=datetime.utcnow(),
                )
            )

            if result.rowcount == 0:
                # Either already committed or status changed
                await self.session.refresh(pay_run)
                if pay_run.status == PayRunStatus.COMMITTED:
                    # Already committed (idempotent success)
                    pass
                else:
                    raise InvalidTransitionError(
                        pay_run.status,
                        PayRunStatus.COMMITTED,
                        "Status changed during commit",
                    )
            else:
                pay_run.status = PayRunStatus.COMMITTED
                pay_run.committed_at = datetime.utcnow()

            # Record audit event
            await self._record_audit(
                pay_run=pay_run,
                action="committed",
                actor_user_id=actor_user_id,
            )

            return pay_run

        finally:
            await release_advisory_lock(self.session, str(pay_run_id))

    async def reopen_pay_run(
        self,
        pay_run_id: UUID,
        actor_user_id: UUID | None = None,
        reason: str | None = None,
    ) -> PayRun:
        """Reopen an approved pay run for modifications."""
        pay_run = await self.get_pay_run(pay_run_id)
        if pay_run is None:
            raise ValueError(f"Pay run {pay_run_id} not found")

        if pay_run.status != PayRunStatus.APPROVED:
            raise InvalidTransitionError(
                pay_run.status,
                PayRunStatus.PREVIEW,
                "Can only reopen from approved status",
            )

        return await self.transition_status(
            pay_run, PayRunStatus.PREVIEW, actor_user_id, reason
        )

    async def void_pay_run(
        self,
        pay_run_id: UUID,
        reason: str,
        actor_user_id: UUID | None = None,
    ) -> PayRun:
        """Void a committed or paid pay run."""
        pay_run = await self.get_pay_run(pay_run_id)
        if pay_run is None:
            raise ValueError(f"Pay run {pay_run_id} not found")

        return await self.transition_status(
            pay_run, PayRunStatus.VOIDED, actor_user_id, reason
        )

    async def _handle_approval(
        self,
        pay_run: PayRun,
        actor_user_id: UUID | None,
    ) -> None:
        """Handle side effects of approval transition."""
        # Lock all inputs
        locked_count = await self.locking_service.lock_inputs_for_run(pay_run)

        # Set approval timestamp
        pay_run.approved_at = datetime.utcnow()
        pay_run.approved_by_user_id = actor_user_id

    async def _handle_reopen(self, pay_run: PayRun) -> None:
        """Handle side effects of reopen (approved → preview)."""
        # Unlock inputs
        await self.locking_service.unlock_inputs_for_run(pay_run)

        # Increment reopen count
        pay_run.reopen_count += 1

        # Clear approval timestamp
        pay_run.approved_at = None
        pay_run.approved_by_user_id = None

    async def _record_audit(
        self,
        pay_run: PayRun,
        action: str,
        actor_user_id: UUID | None = None,
        details: dict | None = None,
    ) -> None:
        """Record an audit event for a pay run action."""
        # Get tenant_id from legal entity
        legal_entity = pay_run.legal_entity
        if legal_entity is None:
            # Load it
            from sqlalchemy.orm import selectinload
            result = await self.session.execute(
                select(PayRun)
                .where(PayRun.pay_run_id == pay_run.pay_run_id)
                .options(selectinload(PayRun.legal_entity))
            )
            pay_run = result.scalar_one()
            legal_entity = pay_run.legal_entity

        event = AuditEvent(
            tenant_id=legal_entity.tenant_id,
            actor_user_id=actor_user_id,
            entity_type="pay_run",
            entity_id=pay_run.pay_run_id,
            action=action,
            after_json=details,
        )
        self.session.add(event)
