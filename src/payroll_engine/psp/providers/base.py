"""Base protocol and types for payment rail providers.

All provider adapters must implement PaymentRailProvider protocol.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol


@dataclass(frozen=True)
class RailCapabilities:
    """Capabilities supported by a payment rail provider."""

    ach_credit: bool = False
    ach_debit: bool = False
    wire: bool = False
    rtp: bool = False
    fednow: bool = False
    check: bool = False

    # Bank-specific configurations
    cutoffs_json: dict[str, Any] | None = None
    limits_json: dict[str, Any] | None = None
    settlement_timelines_json: dict[str, Any] | None = None


@dataclass(frozen=True)
class SubmitResult:
    """Result of submitting a payment to a provider."""

    provider_request_id: str
    accepted: bool
    message: str = ""
    trace_id: str | None = None
    estimated_settlement_date: datetime.date | None = None


@dataclass(frozen=True)
class StatusResult:
    """Result of checking payment status."""

    status: str  # created/submitted/accepted/settled/failed/reversed/returned
    message: str = ""
    external_trace_id: str | None = None
    effective_date: datetime.date | None = None
    return_code: str | None = None  # ACH R01, R02, etc.


@dataclass(frozen=True)
class CancelResult:
    """Result of cancel request."""

    success: bool
    message: str = ""
    can_retry: bool = False


@dataclass(frozen=True)
class SettlementRecord:
    """Settlement record from reconciliation."""

    external_trace_id: str
    effective_date: datetime.date | None
    status: str  # accepted/settled/failed/returned
    amount: Decimal
    currency: str = "USD"
    direction: str = "outbound"  # inbound/outbound
    raw_payload: dict[str, Any] = field(default_factory=dict)
    return_code: str | None = None
    original_trace_id: str | None = None  # For returns, reference to original


class PaymentRailProvider(Protocol):
    """Protocol for payment rail provider adapters.

    Each bank/processor has its own adapter implementing this protocol.
    The orchestrator uses these adapters without knowing bank-specific details.
    """

    provider_name: str

    def capabilities(self) -> RailCapabilities:
        """Return capabilities supported by this provider."""
        ...

    def submit(self, instruction: dict[str, Any]) -> SubmitResult:
        """Submit a payment instruction to the provider.

        Args:
            instruction: Payment details including:
                - payment_instruction_id: UUID
                - idempotency_key: str
                - amount: Decimal as string
                - currency: str (e.g., "USD")
                - direction: "outbound" | "inbound"
                - payee_type: str
                - payee_ref_id: UUID
                - payee_routing: str (tokenized)
                - payee_account: str (tokenized)
                - requested_settlement_date: date | None
                - metadata: dict

        Returns:
            SubmitResult with provider_request_id and acceptance status.
        """
        ...

    def get_status(self, provider_request_id: str) -> StatusResult:
        """Get current status of a submitted payment.

        Args:
            provider_request_id: The ID returned from submit()

        Returns:
            StatusResult with current status and details.
        """
        ...

    def cancel(self, provider_request_id: str) -> CancelResult:
        """Attempt to cancel a payment (if supported by rail).

        Args:
            provider_request_id: The ID returned from submit()

        Returns:
            CancelResult indicating success/failure.
        """
        ...

    def reconcile(self, date: datetime.date) -> list[SettlementRecord]:
        """Fetch settlement records for a given date.

        This is called by the daily reconciliation job to pull
        actual settlement results from the bank/processor.

        Args:
            date: The settlement date to reconcile.

        Returns:
            List of SettlementRecord objects representing
            settled, failed, or returned payments.
        """
        ...
