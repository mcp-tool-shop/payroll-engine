"""Tests for pay run state machine."""

import pytest

from payroll_engine.services.state_machine import (
    InvalidTransitionError,
    PayRunStateMachine,
    PayRunStatus,
)


class TestPayRunStateMachine:
    """Test state machine transitions."""

    def test_valid_transitions(self):
        """Test that valid transitions are allowed."""
        # draft → preview
        assert PayRunStateMachine.can_transition("draft", "preview") is True

        # preview → approved
        assert PayRunStateMachine.can_transition("preview", "approved") is True

        # approved → committed
        assert PayRunStateMachine.can_transition("approved", "committed") is True

        # approved → preview (reopen)
        assert PayRunStateMachine.can_transition("approved", "preview") is True

        # committed → paid
        assert PayRunStateMachine.can_transition("committed", "paid") is True

        # committed → voided
        assert PayRunStateMachine.can_transition("committed", "voided") is True

        # paid → voided
        assert PayRunStateMachine.can_transition("paid", "voided") is True

    def test_invalid_transitions(self):
        """Test that invalid transitions are blocked."""
        # Can't skip preview
        assert PayRunStateMachine.can_transition("draft", "approved") is False

        # Can't go backwards (except approved → preview)
        assert PayRunStateMachine.can_transition("preview", "draft") is False
        assert PayRunStateMachine.can_transition("committed", "approved") is False
        assert PayRunStateMachine.can_transition("paid", "committed") is False

        # Voided is terminal
        assert PayRunStateMachine.can_transition("voided", "draft") is False
        assert PayRunStateMachine.can_transition("voided", "paid") is False

    def test_validate_transition_raises(self):
        """Test that validate_transition raises for invalid transitions."""
        with pytest.raises(InvalidTransitionError) as exc_info:
            PayRunStateMachine.validate_transition("draft", "committed")

        assert exc_info.value.from_status == "draft"
        assert exc_info.value.to_status == "committed"

    def test_is_reopen(self):
        """Test reopen detection."""
        assert PayRunStateMachine.is_reopen("approved", "preview") is True
        assert PayRunStateMachine.is_reopen("preview", "draft") is False
        assert PayRunStateMachine.is_reopen("draft", "preview") is False

    def test_can_calculate(self):
        """Test calculation allowed statuses."""
        assert PayRunStateMachine.can_calculate("draft") is True
        assert PayRunStateMachine.can_calculate("preview") is True
        assert PayRunStateMachine.can_calculate("approved") is True
        assert PayRunStateMachine.can_calculate("committed") is False
        assert PayRunStateMachine.can_calculate("paid") is False

    def test_can_modify_inputs(self):
        """Test input modification allowed statuses."""
        assert PayRunStateMachine.can_modify_inputs("draft") is True
        assert PayRunStateMachine.can_modify_inputs("preview") is True
        assert PayRunStateMachine.can_modify_inputs("approved") is False
        assert PayRunStateMachine.can_modify_inputs("committed") is False

    def test_are_results_immutable(self):
        """Test result immutability statuses."""
        assert PayRunStateMachine.are_results_immutable("draft") is False
        assert PayRunStateMachine.are_results_immutable("preview") is False
        assert PayRunStateMachine.are_results_immutable("approved") is False
        assert PayRunStateMachine.are_results_immutable("committed") is True
        assert PayRunStateMachine.are_results_immutable("paid") is True
        assert PayRunStateMachine.are_results_immutable("voided") is True

    def test_get_next_statuses(self):
        """Test getting allowed next statuses."""
        assert set(PayRunStateMachine.get_next_statuses("draft")) == {"preview"}
        assert set(PayRunStateMachine.get_next_statuses("approved")) == {"preview", "committed"}
        assert PayRunStateMachine.get_next_statuses("voided") == []
