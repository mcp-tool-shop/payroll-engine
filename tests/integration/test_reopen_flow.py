"""Test 6: Reopen + change + reapprove from TEST_PLAN.md.

Goal: Reopening creates new preview identity; commit requires reapproval.
"""

import pytest
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from payroll_engine.models.payroll import PayRun, TimeEntry
from payroll_engine.services.locking_service import LockingService
from payroll_engine.services.pay_run_service import PayRunService
from payroll_engine.services.state_machine import PayRunStateMachine, StateTransitionError

from .conftest import DRAFT_PAY_RUN_ID, ALICE_TIME_ENTRY_ID


pytestmark = pytest.mark.asyncio


class TestReopenFromApproved:
    """Test reopening an approved (not committed) pay run."""

    async def test_can_reopen_approved_to_preview(self, seeded_db: AsyncSession):
        """Can transition from approved back to preview."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)

        # draft -> preview -> approved
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await seeded_db.commit()

        assert pay_run.status == "approved"

        # approved -> preview (reopen)
        state_machine.transition_to("preview")

        assert pay_run.status == "preview", "Should be back in preview"

    async def test_reopen_unlocks_inputs(self, seeded_db: AsyncSession):
        """Reopening should unlock time entries and adjustments."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        # Approve and lock
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Verify locked
        time_entry = await seeded_db.get(TimeEntry, ALICE_TIME_ENTRY_ID)
        assert time_entry.locked_by_pay_run_id is not None

        # Reopen
        state_machine.transition_to("preview")
        await locking_service.unlock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Verify unlocked
        await seeded_db.refresh(time_entry)
        assert time_entry.locked_by_pay_run_id is None

    async def test_can_modify_inputs_after_reopen(self, seeded_db: AsyncSession):
        """After reopening, inputs can be modified."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        # Approve, lock, then reopen
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        state_machine.transition_to("preview")
        await locking_service.unlock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Now modify time entry
        time_entry = await seeded_db.get(TimeEntry, ALICE_TIME_ENTRY_ID)
        original_hours = time_entry.hours
        time_entry.hours = original_hours + Decimal("8")
        await seeded_db.commit()

        await seeded_db.refresh(time_entry)
        assert time_entry.hours == original_hours + Decimal("8")

    async def test_preview_changes_after_modification(self, seeded_db: AsyncSession):
        """Preview outputs should change after modifying inputs."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)
        service = PayRunService(seeded_db)

        # First preview
        state_machine.transition_to("preview")
        result1 = await service.preview(DRAFT_PAY_RUN_ID)
        original_gross = result1.total_gross
        original_calc_id = result1.calculation_id

        # Approve, lock, then reopen
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        state_machine.transition_to("preview")
        await locking_service.unlock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Modify hours
        time_entry = await seeded_db.get(TimeEntry, ALICE_TIME_ENTRY_ID)
        time_entry.hours = time_entry.hours + Decimal("8")
        await seeded_db.commit()

        # Preview again
        result2 = await service.preview(DRAFT_PAY_RUN_ID)

        # Should have different calculation ID and gross
        assert result2.calculation_id != original_calc_id, \
            "New preview should have different calculation_id"
        assert result2.total_gross != original_gross, \
            "Total gross should change after adding hours"


class TestCannotReopenCommitted:
    """Test that committed pay runs cannot be reopened."""

    async def test_cannot_reopen_committed_to_preview(self, seeded_db: AsyncSession):
        """Cannot transition from committed back to preview."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        # Go all the way to committed
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        from payroll_engine.services.commit_service import CommitService
        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        # Try to reopen
        with pytest.raises(StateTransitionError) as exc_info:
            state_machine.transition_to("preview")

        assert "preview" in str(exc_info.value).lower() or \
               "invalid" in str(exc_info.value).lower()

    async def test_cannot_reopen_committed_to_approved(self, seeded_db: AsyncSession):
        """Cannot transition from committed back to approved."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        from payroll_engine.services.commit_service import CommitService
        commit_service = CommitService(seeded_db)
        await commit_service.commit(DRAFT_PAY_RUN_ID)

        with pytest.raises(StateTransitionError):
            state_machine.transition_to("approved")


class TestReapprovalRequired:
    """Test that reapproval is required after reopening."""

    async def test_must_reapprove_before_commit(self, seeded_db: AsyncSession):
        """After reopening, must approve again before commit."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        # Approve
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Reopen
        state_machine.transition_to("preview")
        await locking_service.unlock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Try to commit without reapproval
        from payroll_engine.services.commit_service import CommitService
        commit_service = CommitService(seeded_db)

        try:
            await commit_service.commit(DRAFT_PAY_RUN_ID)
            # If commit proceeds, check status
            await seeded_db.refresh(pay_run)
            if pay_run.status == "committed":
                pytest.fail("Should require reapproval before commit")
        except (StateTransitionError, Exception) as e:
            # Good - commit should fail without approval
            pass

    async def test_can_commit_after_reapproval(self, seeded_db: AsyncSession):
        """After reopening and reapproving, can commit."""
        pay_run = await seeded_db.get(PayRun, DRAFT_PAY_RUN_ID)
        state_machine = PayRunStateMachine(pay_run)
        locking_service = LockingService(seeded_db)

        # First approval cycle
        state_machine.transition_to("preview")
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Reopen
        state_machine.transition_to("preview")
        await locking_service.unlock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Reapprove
        state_machine.transition_to("approved")
        await locking_service.lock_inputs(DRAFT_PAY_RUN_ID)
        await seeded_db.commit()

        # Now commit should work
        from payroll_engine.services.commit_service import CommitService
        commit_service = CommitService(seeded_db)
        result = await commit_service.commit(DRAFT_PAY_RUN_ID)

        await seeded_db.refresh(pay_run)
        assert pay_run.status == "committed"
