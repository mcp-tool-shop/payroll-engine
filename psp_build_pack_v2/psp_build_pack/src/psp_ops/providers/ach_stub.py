from __future__ import annotations

import datetime
from .base import PaymentRailProvider, RailCapabilities, SubmitResult, StatusResult, SettlementRecord

class AchStubProvider:
    """Stub provider for local development.
    Replace with a real NACHA file builder or bank API adapter.
    """
    provider_name = "ach_stub"

    def capabilities(self) -> RailCapabilities:
        return RailCapabilities(ach_credit=True, ach_debit=True)

    def submit(self, instruction: dict) -> SubmitResult:
        # instruction should include idempotency_key and amount
        req_id = f"ACHSTUB-{instruction.get('idempotency_key','')}"
        return SubmitResult(provider_request_id=req_id, accepted=True, message="stub accepted")

    def get_status(self, provider_request_id: str) -> StatusResult:
        return StatusResult(status="settled", message="stub settled")

    def cancel(self, provider_request_id: str) -> StatusResult:
        return StatusResult(status="canceled", message="stub canceled")

    def reconcile(self, date: datetime.date):
        # In real providers, pull bank settlement results.
        return []
