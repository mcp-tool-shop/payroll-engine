"""FedNow stub provider for development and testing.

FedNow is the Federal Reserve's instant payment service.
Unlike ACH, FedNow provides near-real-time settlement.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

from payroll_engine.psp.providers.base import (
    CancelResult,
    RailCapabilities,
    SettlementRecord,
    StatusResult,
    SubmitResult,
)


class FedNowStubProvider:
    """Stub FedNow provider for development.

    In production, this would:
    - Connect to FedNow service via ISO 20022 messages
    - Handle instant settlement responses
    - Process rejects and timeouts
    - Implement fraud controls and limits
    """

    provider_name = "fednow_stub"

    def __init__(self, auto_settle: bool = True):
        """Initialize stub provider.

        Args:
            auto_settle: If True, payments immediately settle (like real FedNow).
        """
        self.auto_settle = auto_settle
        self._submitted: dict[str, dict[str, Any]] = {}

    def capabilities(self) -> RailCapabilities:
        """Return FedNow capabilities."""
        return RailCapabilities(
            ach_credit=False,
            ach_debit=False,
            wire=False,
            rtp=False,
            fednow=True,
            check=False,
            cutoffs_json={
                # FedNow operates 24/7/365
                "availability": "24/7/365",
            },
            limits_json={
                # FedNow has $500K limit per transaction (as of 2024)
                "fednow_max": "500000.00",
            },
            settlement_timelines_json={
                # FedNow settles in seconds
                "fednow_credit": "instant",
            },
        )

    def submit(self, instruction: dict[str, Any]) -> SubmitResult:
        """Submit FedNow payment (stub implementation).

        FedNow uses ISO 20022 message format (pacs.008).
        Response is typically within seconds.
        """
        idempotency_key = instruction.get("idempotency_key", str(uuid.uuid4()))
        payment_id = instruction.get("payment_instruction_id", str(uuid.uuid4()))

        # Generate stub message ID (real FedNow uses UETR format)
        message_id = f"FEDNOW{uuid.uuid4().hex[:20].upper()}"
        provider_request_id = f"FEDNOW-{idempotency_key}"

        # FedNow settles instantly
        settlement_date = datetime.date.today()

        # Check amount limits
        amount = Decimal(str(instruction.get("amount", "0")))
        if amount > Decimal("500000"):
            return SubmitResult(
                provider_request_id=provider_request_id,
                accepted=False,
                message="FedNow limit exceeded: max $500,000 per transaction",
                trace_id=None,
            )

        # Track submission
        self._submitted[provider_request_id] = {
            "instruction": instruction,
            "message_id": message_id,
            "submitted_at": datetime.datetime.now(datetime.timezone.utc),
            "settlement_date": settlement_date,
            "status": "settled" if self.auto_settle else "accepted",
        }

        return SubmitResult(
            provider_request_id=provider_request_id,
            accepted=True,
            message="FedNow stub accepted - instant settlement",
            trace_id=message_id,
            estimated_settlement_date=settlement_date,
        )

    def get_status(self, provider_request_id: str) -> StatusResult:
        """Get status of submitted payment.

        FedNow provides synchronous confirmation, so status
        is typically final within seconds of submission.
        """
        if provider_request_id not in self._submitted:
            return StatusResult(
                status="unknown",
                message=f"Payment {provider_request_id} not found",
            )

        record = self._submitted[provider_request_id]
        return StatusResult(
            status=record["status"],
            message="FedNow stub status",
            external_trace_id=record["message_id"],
            effective_date=record["settlement_date"],
        )

    def cancel(self, provider_request_id: str) -> CancelResult:
        """Attempt to cancel payment.

        FedNow is instant-settlement, so cancellation is generally
        not possible after acceptance. Returns must go through
        a separate recall process.
        """
        if provider_request_id not in self._submitted:
            return CancelResult(
                success=False,
                message=f"Payment {provider_request_id} not found",
            )

        record = self._submitted[provider_request_id]
        if record["status"] == "settled":
            return CancelResult(
                success=False,
                message="FedNow payments cannot be cancelled after settlement. Use recall process.",
                can_retry=False,
            )

        return CancelResult(
            success=False,
            message="FedNow payments settle instantly and cannot be cancelled",
            can_retry=False,
        )

    def reconcile(self, date: datetime.date) -> list[SettlementRecord]:
        """Return settlement records for a date.

        For FedNow, reconciliation is mostly confirmatory since
        settlement happens in real-time. This would be used to
        verify end-of-day positions.
        """
        records = []
        for req_id, data in self._submitted.items():
            if data.get("settlement_date") == date:
                instruction = data["instruction"]
                records.append(
                    SettlementRecord(
                        external_trace_id=data["message_id"],
                        effective_date=date,
                        status=data["status"],
                        amount=Decimal(str(instruction.get("amount", "0"))),
                        currency=instruction.get("currency", "USD"),
                        direction=instruction.get("direction", "outbound"),
                        raw_payload={"provider_request_id": req_id},
                    )
                )
        return records

    def simulate_reject(
        self,
        provider_request_id: str,
        reject_code: str = "NARR",
        reason: str = "Narrative - general reject",
    ) -> None:
        """Simulate a FedNow rejection (for testing).

        Common rejection reasons:
        - AC01: Incorrect Account Number
        - AC04: Closed Account Number
        - AM02: Not Allowed Amount
        - BE04: Missing Creditor Address
        - NARR: Narrative (general)
        - RJCT: Rejected by receiving bank
        """
        if provider_request_id in self._submitted:
            self._submitted[provider_request_id]["status"] = "rejected"
            self._submitted[provider_request_id]["reject_code"] = reject_code
            self._submitted[provider_request_id]["reject_reason"] = reason
