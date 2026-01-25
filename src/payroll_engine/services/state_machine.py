"""Pay run state machine with transition validation."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from payroll_engine.models import PayRun


class PayRunStatus(str, Enum):
    """Pay run status values."""

    DRAFT = "draft"
    PREVIEW = "preview"
    APPROVED = "approved"
    COMMITTED = "committed"
    PAID = "paid"
    VOIDED = "voided"


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, from_status: str, to_status: str, reason: str | None = None):
        self.from_status = from_status
        self.to_status = to_status
        self.reason = reason
        msg = f"Invalid transition from '{from_status}' to '{to_status}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class PayRunStateMachine:
    """State machine for pay run status transitions.

    Allowed transitions:
    - draft → preview
    - preview → approved
    - approved → preview (reopen)
    - approved → committed
    - committed → paid
    - committed → voided
    - paid → voided
    """

    # Define valid transitions: {from_status: [allowed_to_statuses]}
    VALID_TRANSITIONS: dict[str, list[str]] = {
        PayRunStatus.DRAFT: [PayRunStatus.PREVIEW],
        PayRunStatus.PREVIEW: [PayRunStatus.APPROVED],
        PayRunStatus.APPROVED: [PayRunStatus.PREVIEW, PayRunStatus.COMMITTED],
        PayRunStatus.COMMITTED: [PayRunStatus.PAID, PayRunStatus.VOIDED],
        PayRunStatus.PAID: [PayRunStatus.VOIDED],
        PayRunStatus.VOIDED: [],  # Terminal state
    }

    # Statuses where recalculation is allowed
    CALCULATION_ALLOWED = {
        PayRunStatus.DRAFT,
        PayRunStatus.PREVIEW,
        PayRunStatus.APPROVED,
    }

    # Statuses where inputs can be modified
    INPUTS_MUTABLE = {
        PayRunStatus.DRAFT,
        PayRunStatus.PREVIEW,
    }

    # Statuses where results are immutable
    RESULTS_IMMUTABLE = {
        PayRunStatus.COMMITTED,
        PayRunStatus.PAID,
        PayRunStatus.VOIDED,
    }

    @classmethod
    def can_transition(cls, from_status: str, to_status: str) -> bool:
        """Check if a transition is valid."""
        allowed = cls.VALID_TRANSITIONS.get(from_status, [])
        return to_status in allowed

    @classmethod
    def validate_transition(cls, from_status: str, to_status: str) -> None:
        """Validate a transition, raising InvalidTransitionError if invalid."""
        if not cls.can_transition(from_status, to_status):
            raise InvalidTransitionError(from_status, to_status)

    @classmethod
    def can_calculate(cls, status: str) -> bool:
        """Check if calculation/preview is allowed in this status."""
        return status in cls.CALCULATION_ALLOWED

    @classmethod
    def can_modify_inputs(cls, status: str) -> bool:
        """Check if inputs (time entries, adjustments) can be modified."""
        return status in cls.INPUTS_MUTABLE

    @classmethod
    def are_results_immutable(cls, status: str) -> bool:
        """Check if results (statements, line items) are immutable."""
        return status in cls.RESULTS_IMMUTABLE

    @classmethod
    def is_reopen(cls, from_status: str, to_status: str) -> bool:
        """Check if this transition is a reopen (approved → preview)."""
        return from_status == PayRunStatus.APPROVED and to_status == PayRunStatus.PREVIEW

    @classmethod
    def get_next_statuses(cls, current_status: str) -> list[str]:
        """Get list of valid next statuses from current status."""
        return cls.VALID_TRANSITIONS.get(current_status, [])

    @classmethod
    def requires_lock_verification(cls, status: str) -> bool:
        """Check if this status requires lock verification before commit."""
        return status == PayRunStatus.APPROVED

    @classmethod
    def validate_pay_run_for_transition(
        cls, pay_run: PayRun, to_status: str
    ) -> list[str]:
        """Validate a pay run for a specific transition, returning any errors.

        Returns list of error messages (empty if valid).
        """
        errors: list[str] = []
        from_status = pay_run.status

        # Basic transition check
        if not cls.can_transition(from_status, to_status):
            errors.append(f"Cannot transition from '{from_status}' to '{to_status}'")
            return errors

        # Transition-specific validations
        if to_status == PayRunStatus.APPROVED:
            # Must have at least one included employee
            included = [e for e in pay_run.employees if e.status == "included"]
            if not included:
                errors.append("Pay run has no included employees")

            # All included employees must not have error status
            error_employees = [e for e in pay_run.employees if e.status == "error"]
            if error_employees:
                errors.append(f"{len(error_employees)} employee(s) have calculation errors")

        elif to_status == PayRunStatus.COMMITTED:
            # Must be approved
            if from_status != PayRunStatus.APPROVED:
                errors.append("Can only commit from approved status")

            # All included employees must have passed validation
            error_employees = [e for e in pay_run.employees if e.status == "error"]
            if error_employees:
                errors.append(f"{len(error_employees)} employee(s) have errors")

        elif to_status == PayRunStatus.VOIDED:
            # Must provide reason (enforced at service level)
            pass

        return errors
