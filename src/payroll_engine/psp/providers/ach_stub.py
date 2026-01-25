"""ACH stub provider for local development and testing.

Replace with a real NACHA file builder or bank API adapter for production.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

from payroll_engine.psp.providers.base import (
    CancelResult,
    PaymentRailProvider,
    RailCapabilities,
    SettlementRecord,
    StatusResult,
    SubmitResult,
)


class AchStubProvider:
    """Stub ACH provider for development.

    In production, this would:
    - Generate NACHA files for batch submission
    - Or call bank API for single-entry ACH
    - Handle return codes (R01-R99)
    - Process NOC (Notification of Change) updates
    """

    provider_name = "ach_stub"

    def __init__(self, auto_settle: bool = True):
        """Initialize stub provider.

        Args:
            auto_settle: If True, payments immediately report as settled.
                        If False, payments stay in 'accepted' state.
        """
        self.auto_settle = auto_settle
        # In-memory tracking for stub
        self._submitted: dict[str, dict[str, Any]] = {}

    def capabilities(self) -> RailCapabilities:
        """Return ACH capabilities."""
        return RailCapabilities(
            ach_credit=True,
            ach_debit=True,
            wire=False,
            rtp=False,
            fednow=False,
            check=False,
            cutoffs_json={
                "ach_same_day": "14:00 CT",
                "ach_standard": "17:00 CT",
            },
            limits_json={
                "ach_same_day_max": "1000000.00",
                "ach_standard_max": "99999999.99",
            },
            settlement_timelines_json={
                "ach_credit_same_day": "same_day",
                "ach_credit_standard": "t+1",
                "ach_debit_standard": "t+2",
            },
        )

    def submit(self, instruction: dict[str, Any]) -> SubmitResult:
        """Submit ACH payment (stub implementation)."""
        idempotency_key = instruction.get("idempotency_key", str(uuid.uuid4()))
        payment_id = instruction.get("payment_instruction_id", str(uuid.uuid4()))

        # Generate stub trace number (real ACH uses 15-digit trace)
        trace_id = f"ACHSTUB{datetime.date.today().strftime('%Y%m%d')}{payment_id[:8].upper()}"
        provider_request_id = f"ACHSTUB-{idempotency_key}"

        # Calculate estimated settlement
        requested_date = instruction.get("requested_settlement_date")
        if requested_date:
            est_settlement = (
                requested_date
                if isinstance(requested_date, datetime.date)
                else datetime.date.fromisoformat(str(requested_date))
            )
        else:
            # Standard ACH is T+1 for credits
            est_settlement = datetime.date.today() + datetime.timedelta(days=1)

        # Track submission
        self._submitted[provider_request_id] = {
            "instruction": instruction,
            "trace_id": trace_id,
            "submitted_at": datetime.datetime.now(datetime.timezone.utc),
            "estimated_settlement": est_settlement,
            "status": "settled" if self.auto_settle else "accepted",
        }

        return SubmitResult(
            provider_request_id=provider_request_id,
            accepted=True,
            message="ACH stub accepted",
            trace_id=trace_id,
            estimated_settlement_date=est_settlement,
        )

    def get_status(self, provider_request_id: str) -> StatusResult:
        """Get status of submitted payment."""
        if provider_request_id not in self._submitted:
            return StatusResult(
                status="unknown",
                message=f"Payment {provider_request_id} not found",
            )

        record = self._submitted[provider_request_id]
        return StatusResult(
            status=record["status"],
            message="ACH stub status",
            external_trace_id=record["trace_id"],
            effective_date=record["estimated_settlement"],
        )

    def cancel(self, provider_request_id: str) -> CancelResult:
        """Attempt to cancel payment.

        Note: Real ACH has very limited cancellation windows.
        Same-day ACH can sometimes be recalled before cutoff.
        Standard ACH typically cannot be cancelled after submission.
        """
        if provider_request_id not in self._submitted:
            return CancelResult(
                success=False,
                message=f"Payment {provider_request_id} not found",
            )

        record = self._submitted[provider_request_id]
        if record["status"] in ("settled", "failed"):
            return CancelResult(
                success=False,
                message="Cannot cancel settled/failed payment",
                can_retry=False,
            )

        # Stub allows cancellation
        record["status"] = "canceled"
        return CancelResult(
            success=True,
            message="ACH stub canceled",
        )

    def reconcile(self, date: datetime.date) -> list[SettlementRecord]:
        """Return settlement records for a date.

        In production, this would:
        - Parse return files from the bank
        - Match trace numbers to submitted payments
        - Handle NOC updates
        """
        records = []
        for req_id, data in self._submitted.items():
            if data.get("estimated_settlement") == date:
                instruction = data["instruction"]
                records.append(
                    SettlementRecord(
                        external_trace_id=data["trace_id"],
                        effective_date=date,
                        status=data["status"],
                        amount=Decimal(str(instruction.get("amount", "0"))),
                        currency=instruction.get("currency", "USD"),
                        direction=instruction.get("direction", "outbound"),
                        raw_payload={"provider_request_id": req_id},
                    )
                )
        return records

    def simulate_settlement(
        self,
        provider_request_id: str,
        settlement_date: datetime.date | None = None,
    ) -> None:
        """Simulate ACH settlement (for testing).

        Args:
            provider_request_id: The payment to settle
            settlement_date: Optional settlement date (defaults to today)
        """
        if provider_request_id in self._submitted:
            self._submitted[provider_request_id]["status"] = "settled"
            if settlement_date:
                self._submitted[provider_request_id]["estimated_settlement"] = settlement_date

    def simulate_return(
        self,
        provider_request_id: str,
        return_code: str = "R01",
        reason: str = "Insufficient Funds",
    ) -> None:
        """Simulate an ACH return (for testing).

        Common return codes:
        - R01: Insufficient Funds
        - R02: Account Closed
        - R03: No Account/Unable to Locate
        - R04: Invalid Account Number
        - R08: Payment Stopped
        - R10: Customer Advises Unauthorized
        - R29: Corporate Customer Advises Not Authorized
        """
        if provider_request_id in self._submitted:
            self._submitted[provider_request_id]["status"] = "returned"
            self._submitted[provider_request_id]["return_code"] = return_code
            self._submitted[provider_request_id]["return_reason"] = reason
