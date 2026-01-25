"""Payment rail provider adapters."""

from payroll_engine.psp.providers.base import (
    PaymentRailProvider,
    RailCapabilities,
    SubmitResult,
    StatusResult,
    SettlementRecord,
)
from payroll_engine.psp.providers.ach_stub import AchStubProvider
from payroll_engine.psp.providers.fednow_stub import FedNowStubProvider

__all__ = [
    "PaymentRailProvider",
    "RailCapabilities",
    "SubmitResult",
    "StatusResult",
    "SettlementRecord",
    "AchStubProvider",
    "FedNowStubProvider",
]
