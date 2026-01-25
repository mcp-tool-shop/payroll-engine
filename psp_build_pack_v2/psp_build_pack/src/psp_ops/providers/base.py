from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Optional, List
import datetime

@dataclass(frozen=True)
class RailCapabilities:
    ach_credit: bool = False
    ach_debit: bool = False
    wire: bool = False
    rtp: bool = False
    fednow: bool = False
    check: bool = False
    # Optional: cutoffs, limits, and settlement expectations
    cutoffs_json: dict | None = None
    limits_json: dict | None = None

@dataclass(frozen=True)
class SubmitResult:
    provider_request_id: str
    accepted: bool
    message: str = ""

@dataclass(frozen=True)
class StatusResult:
    status: str  # created/submitted/accepted/settled/failed/reversed
    message: str = ""

@dataclass(frozen=True)
class SettlementRecord:
    external_trace_id: str
    effective_date: datetime.date | None
    status: str  # accepted/settled/failed/returned
    amount: str  # decimal as string
    currency: str = "USD"
    raw_payload: dict | None = None

class PaymentRailProvider(Protocol):
    provider_name: str

    def capabilities(self) -> RailCapabilities: ...
    def submit(self, instruction: dict) -> SubmitResult: ...
    def get_status(self, provider_request_id: str) -> StatusResult: ...
    def cancel(self, provider_request_id: str) -> StatusResult: ...
    def reconcile(self, date: datetime.date) -> List[SettlementRecord]: ...
