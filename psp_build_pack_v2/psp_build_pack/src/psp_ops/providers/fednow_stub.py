from __future__ import annotations

import datetime
from .base import RailCapabilities, SubmitResult, StatusResult

class FedNowStubProvider:
    provider_name = "fednow_stub"

    def capabilities(self) -> RailCapabilities:
        return RailCapabilities(fednow=True)

    def submit(self, instruction: dict) -> SubmitResult:
        req_id = f"FEDNOWSTUB-{instruction.get('idempotency_key','')}"
        return SubmitResult(provider_request_id=req_id, accepted=True, message="stub accepted")

    def get_status(self, provider_request_id: str) -> StatusResult:
        return StatusResult(status="settled", message="stub settled")

    def cancel(self, provider_request_id: str) -> StatusResult:
        return StatusResult(status="canceled", message="stub canceled")

    def reconcile(self, date: datetime.date):
        return []
