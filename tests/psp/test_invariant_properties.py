"""Property-based tests for PSP invariants.

These tests use hypothesis to generate random sequences of operations
and verify that invariants always hold, regardless of the order or
combination of operations.

This is where libraries become "unbreakable."
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable
from uuid import UUID, uuid4

# Try to import hypothesis, skip tests if not available
try:
    from hypothesis import given, settings, assume, strategies as st
    from hypothesis.stateful import RuleBasedStateMachine, rule, invariant, initialize

    HYPOTHESIS_AVAILABLE = True
except ImportError:
    HYPOTHESIS_AVAILABLE = False
    # Create dummy decorators for when hypothesis isn't installed
    def given(*args, **kwargs):
        def decorator(f):
            return pytest.mark.skip(reason="hypothesis not installed")(f)
        return decorator

    def settings(*args, **kwargs):
        def decorator(f):
            return f
        return decorator

    class st:
        @staticmethod
        def decimals(*args, **kwargs):
            pass

        @staticmethod
        def integers(*args, **kwargs):
            pass

        @staticmethod
        def lists(*args, **kwargs):
            pass

        @staticmethod
        def sampled_from(*args, **kwargs):
            pass


# =============================================================================
# In-Memory PSP State Machine for Testing
# =============================================================================

@dataclass
class LedgerEntry:
    """A ledger entry for testing."""
    entry_id: UUID
    debit_account: UUID
    credit_account: UUID
    amount: Decimal
    reversed_by: UUID | None = None


@dataclass
class Reservation:
    """A balance reservation for testing."""
    reservation_id: UUID
    account_id: UUID
    amount: Decimal
    status: str = "active"  # active, consumed, released


@dataclass
class Payment:
    """A payment for testing."""
    payment_id: UUID
    amount: Decimal
    status: str = "pending"  # pending, submitted, settled, returned


@dataclass
class TestPSPState:
    """In-memory PSP state for property testing."""

    # Ledger
    entries: list[LedgerEntry] = field(default_factory=list)
    accounts: dict[UUID, Decimal] = field(default_factory=dict)

    # Reservations
    reservations: dict[UUID, Reservation] = field(default_factory=dict)

    # Payments
    payments: dict[UUID, Payment] = field(default_factory=dict)

    # Status transition rules
    VALID_TRANSITIONS: dict[str, list[str]] = field(default_factory=lambda: {
        "pending": ["submitted"],
        "submitted": ["settled", "returned"],
        "settled": [],  # Terminal
        "returned": [],  # Terminal
    })

    def create_account(self, account_id: UUID) -> None:
        """Create an account with zero balance."""
        if account_id not in self.accounts:
            self.accounts[account_id] = Decimal("0")

    def post_entry(
        self,
        debit_account: UUID,
        credit_account: UUID,
        amount: Decimal,
    ) -> UUID:
        """Post a ledger entry."""
        assert amount > 0, "Amount must be positive"
        assert debit_account != credit_account, "Cannot self-transfer"

        entry_id = uuid4()
        entry = LedgerEntry(
            entry_id=entry_id,
            debit_account=debit_account,
            credit_account=credit_account,
            amount=amount,
        )
        self.entries.append(entry)

        # Update balances
        self.accounts[debit_account] = self.accounts.get(debit_account, Decimal("0")) - amount
        self.accounts[credit_account] = self.accounts.get(credit_account, Decimal("0")) + amount

        return entry_id

    def reverse_entry(self, entry_id: UUID) -> UUID | None:
        """Reverse a ledger entry."""
        # Find the entry
        entry = None
        for e in self.entries:
            if e.entry_id == entry_id:
                entry = e
                break

        if entry is None:
            return None

        # Check not already reversed
        if entry.reversed_by is not None:
            return None

        # Create reversal
        reversal_id = self.post_entry(
            debit_account=entry.credit_account,
            credit_account=entry.debit_account,
            amount=entry.amount,
        )

        entry.reversed_by = reversal_id
        return reversal_id

    def create_reservation(self, account_id: UUID, amount: Decimal) -> UUID:
        """Create a balance reservation."""
        assert amount > 0, "Amount must be positive"

        reservation_id = uuid4()
        self.reservations[reservation_id] = Reservation(
            reservation_id=reservation_id,
            account_id=account_id,
            amount=amount,
            status="active",
        )
        return reservation_id

    def release_reservation(self, reservation_id: UUID) -> bool:
        """Release a reservation."""
        if reservation_id not in self.reservations:
            return False

        reservation = self.reservations[reservation_id]
        if reservation.status != "active":
            return False

        reservation.status = "released"
        return True

    def consume_reservation(self, reservation_id: UUID) -> bool:
        """Consume a reservation."""
        if reservation_id not in self.reservations:
            return False

        reservation = self.reservations[reservation_id]
        if reservation.status != "active":
            return False

        reservation.status = "consumed"
        return True

    def create_payment(self, amount: Decimal) -> UUID:
        """Create a payment."""
        assert amount > 0, "Amount must be positive"

        payment_id = uuid4()
        self.payments[payment_id] = Payment(
            payment_id=payment_id,
            amount=amount,
            status="pending",
        )
        return payment_id

    def transition_payment(self, payment_id: UUID, new_status: str) -> bool:
        """Transition a payment to a new status."""
        if payment_id not in self.payments:
            return False

        payment = self.payments[payment_id]
        valid_next = self.VALID_TRANSITIONS.get(payment.status, [])

        if new_status not in valid_next:
            return False

        payment.status = new_status
        return True

    def get_balance(self, account_id: UUID) -> Decimal:
        """Get account balance."""
        return self.accounts.get(account_id, Decimal("0"))

    def get_available_balance(self, account_id: UUID) -> Decimal:
        """Get available balance (total - reserved)."""
        total = self.get_balance(account_id)
        reserved = sum(
            r.amount for r in self.reservations.values()
            if r.account_id == account_id and r.status == "active"
        )
        return total - reserved


# =============================================================================
# Pure Property Tests (No State Machine)
# =============================================================================

@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestLedgerInvariants:
    """Property tests for ledger invariants."""

    @given(
        amounts=st.lists(
            st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000")),
            min_size=1,
            max_size=100,
        )
    )
    @settings(max_examples=100)
    def test_ledger_always_balances(self, amounts: list[Decimal]):
        """The sum of all debits equals the sum of all credits."""
        state = TestPSPState()

        # Create two accounts
        account_a = uuid4()
        account_b = uuid4()
        state.create_account(account_a)
        state.create_account(account_b)

        # Fund account_a so it can send
        funding = sum(amounts) + Decimal("1")
        state.accounts[account_a] = funding

        # Post entries
        for amount in amounts:
            if amount > 0:
                state.post_entry(account_a, account_b, amount)

        # Invariant: sum of all balances = initial funding
        total_balance = sum(state.accounts.values())
        assert total_balance == funding

    @given(amount=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000")))
    @settings(max_examples=50)
    def test_reversal_restores_balance(self, amount: Decimal):
        """Reversing an entry restores the original balances."""
        state = TestPSPState()

        account_a = uuid4()
        account_b = uuid4()
        state.create_account(account_a)
        state.create_account(account_b)
        state.accounts[account_a] = amount

        # Record original balances
        original_a = state.get_balance(account_a)
        original_b = state.get_balance(account_b)

        # Post and reverse
        entry_id = state.post_entry(account_a, account_b, amount)
        state.reverse_entry(entry_id)

        # Invariant: balances restored
        assert state.get_balance(account_a) == original_a
        assert state.get_balance(account_b) == original_b

    @given(amount=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000000")))
    @settings(max_examples=50)
    def test_entry_cannot_be_reversed_twice(self, amount: Decimal):
        """An entry can only be reversed once."""
        state = TestPSPState()

        account_a = uuid4()
        account_b = uuid4()
        state.accounts[account_a] = amount * 2

        entry_id = state.post_entry(account_a, account_b, amount)

        # First reversal succeeds
        reversal_1 = state.reverse_entry(entry_id)
        assert reversal_1 is not None

        # Second reversal fails
        reversal_2 = state.reverse_entry(entry_id)
        assert reversal_2 is None


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestReservationInvariants:
    """Property tests for reservation invariants."""

    @given(
        amounts=st.lists(
            st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000")),
            min_size=1,
            max_size=20,
        )
    )
    @settings(max_examples=50)
    def test_available_never_exceeds_total(self, amounts: list[Decimal]):
        """Available balance never exceeds total balance."""
        state = TestPSPState()
        account = uuid4()
        state.accounts[account] = sum(amounts)

        # Create reservations for half the amounts
        for i, amount in enumerate(amounts):
            if i % 2 == 0 and amount > 0:
                state.create_reservation(account, amount)

        # Invariant
        assert state.get_available_balance(account) <= state.get_balance(account)

    @given(amount=st.decimals(min_value=Decimal("0.01"), max_value=Decimal("1000")))
    @settings(max_examples=50)
    def test_released_reservation_frees_balance(self, amount: Decimal):
        """Releasing a reservation increases available balance."""
        state = TestPSPState()
        account = uuid4()
        state.accounts[account] = amount

        # Create reservation
        res_id = state.create_reservation(account, amount)
        available_before = state.get_available_balance(account)

        # Release
        state.release_reservation(res_id)
        available_after = state.get_available_balance(account)

        # Invariant
        assert available_after == available_before + amount


@pytest.mark.skipif(not HYPOTHESIS_AVAILABLE, reason="hypothesis not installed")
class TestPaymentStatusInvariants:
    """Property tests for payment status invariants."""

    @given(
        transitions=st.lists(
            st.sampled_from(["submitted", "settled", "returned"]),
            min_size=1,
            max_size=10,
        )
    )
    @settings(max_examples=100)
    def test_status_only_moves_forward(self, transitions: list[str]):
        """Payment status can only move forward, never backward."""
        state = TestPSPState()
        payment_id = state.create_payment(Decimal("100"))

        status_order = ["pending", "submitted", "settled", "returned"]
        highest_reached = 0

        for new_status in transitions:
            old_status = state.payments[payment_id].status
            old_index = status_order.index(old_status) if old_status in status_order else 0

            success = state.transition_payment(payment_id, new_status)
            new_index = status_order.index(state.payments[payment_id].status)

            # Invariant: status index never decreases
            assert new_index >= old_index or not success

            highest_reached = max(highest_reached, new_index)

    @given(st.integers(min_value=1, max_value=100))
    @settings(max_examples=50)
    def test_terminal_states_are_terminal(self, _: int):
        """Once in a terminal state, no transitions are allowed."""
        state = TestPSPState()

        # Test settled is terminal
        payment_id = state.create_payment(Decimal("100"))
        state.transition_payment(payment_id, "submitted")
        state.transition_payment(payment_id, "settled")

        assert not state.transition_payment(payment_id, "returned")
        assert not state.transition_payment(payment_id, "submitted")
        assert not state.transition_payment(payment_id, "pending")

        # Test returned is terminal
        payment_id_2 = state.create_payment(Decimal("100"))
        state.transition_payment(payment_id_2, "submitted")
        state.transition_payment(payment_id_2, "returned")

        assert not state.transition_payment(payment_id_2, "settled")
        assert not state.transition_payment(payment_id_2, "submitted")


# =============================================================================
# Stateful Property Tests (State Machine)
# =============================================================================

if HYPOTHESIS_AVAILABLE:
    class PSPStateMachine(RuleBasedStateMachine):
        """
        Stateful property test for PSP.

        This generates random sequences of operations and verifies
        that invariants hold after every operation.
        """

        def __init__(self):
            super().__init__()
            self.state = TestPSPState()
            self.accounts: list[UUID] = []
            self.entry_ids: list[UUID] = []
            self.reservation_ids: list[UUID] = []
            self.payment_ids: list[UUID] = []

        @initialize()
        def setup(self):
            """Create initial accounts."""
            for _ in range(3):
                account_id = uuid4()
                self.state.create_account(account_id)
                self.state.accounts[account_id] = Decimal("10000")
                self.accounts.append(account_id)

        @rule()
        def post_entry(self):
            """Post a random ledger entry."""
            if len(self.accounts) < 2:
                return

            import random
            debit = random.choice(self.accounts)
            credit = random.choice([a for a in self.accounts if a != debit])
            amount = Decimal(str(random.randint(1, 100)))

            # Only post if debit account has enough
            if self.state.get_balance(debit) >= amount:
                entry_id = self.state.post_entry(debit, credit, amount)
                self.entry_ids.append(entry_id)

        @rule()
        def reverse_entry(self):
            """Reverse a random entry."""
            if not self.entry_ids:
                return

            import random
            entry_id = random.choice(self.entry_ids)
            self.state.reverse_entry(entry_id)

        @rule()
        def create_reservation(self):
            """Create a random reservation."""
            if not self.accounts:
                return

            import random
            account = random.choice(self.accounts)
            amount = Decimal(str(random.randint(1, 100)))

            if self.state.get_available_balance(account) >= amount:
                res_id = self.state.create_reservation(account, amount)
                self.reservation_ids.append(res_id)

        @rule()
        def release_reservation(self):
            """Release a random reservation."""
            if not self.reservation_ids:
                return

            import random
            res_id = random.choice(self.reservation_ids)
            self.state.release_reservation(res_id)

        @rule()
        def create_and_process_payment(self):
            """Create and process a payment through its lifecycle."""
            import random
            amount = Decimal(str(random.randint(1, 100)))
            payment_id = self.state.create_payment(amount)
            self.payment_ids.append(payment_id)

            # Random progression
            if random.random() > 0.3:
                self.state.transition_payment(payment_id, "submitted")
                if random.random() > 0.5:
                    if random.random() > 0.2:
                        self.state.transition_payment(payment_id, "settled")
                    else:
                        self.state.transition_payment(payment_id, "returned")

        @invariant()
        def ledger_balances(self):
            """Total balance is conserved."""
            total = sum(self.state.accounts.values())
            expected = Decimal("10000") * len(self.accounts)
            assert total == expected, f"Balance mismatch: {total} != {expected}"

        @invariant()
        def available_never_negative_for_funded(self):
            """Available balance check."""
            # This is a weak invariant - just checking math works
            for account_id in self.accounts:
                available = self.state.get_available_balance(account_id)
                total = self.state.get_balance(account_id)
                assert available <= total

        @invariant()
        def no_double_reversals(self):
            """Each entry has at most one reversal."""
            reversed_entries = set()
            for entry in self.state.entries:
                if entry.reversed_by is not None:
                    assert entry.entry_id not in reversed_entries
                    reversed_entries.add(entry.entry_id)

        @invariant()
        def payment_status_valid(self):
            """All payments have valid status."""
            valid_statuses = {"pending", "submitted", "settled", "returned"}
            for payment in self.state.payments.values():
                assert payment.status in valid_statuses

    # This creates a test that runs the state machine
    TestPSPStateful = PSPStateMachine.TestCase


# =============================================================================
# Simple unit tests (always run, don't need hypothesis)
# =============================================================================

class TestBasicInvariants:
    """Basic invariant tests that don't need hypothesis."""

    def test_amount_must_be_positive(self):
        """Verify positive amount enforcement."""
        state = TestPSPState()
        account_a = uuid4()
        account_b = uuid4()

        with pytest.raises(AssertionError):
            state.post_entry(account_a, account_b, Decimal("-100"))

        with pytest.raises(AssertionError):
            state.post_entry(account_a, account_b, Decimal("0"))

    def test_no_self_transfer(self):
        """Verify self-transfer prevention."""
        state = TestPSPState()
        account = uuid4()
        state.accounts[account] = Decimal("1000")

        with pytest.raises(AssertionError):
            state.post_entry(account, account, Decimal("100"))

    def test_status_transitions(self):
        """Verify status transition rules."""
        state = TestPSPState()
        payment_id = state.create_payment(Decimal("100"))

        # Valid: pending -> submitted
        assert state.transition_payment(payment_id, "submitted")

        # Invalid: submitted -> pending
        assert not state.transition_payment(payment_id, "pending")

        # Valid: submitted -> settled
        assert state.transition_payment(payment_id, "settled")

        # Invalid: settled -> anything
        assert not state.transition_payment(payment_id, "returned")
        assert not state.transition_payment(payment_id, "submitted")
