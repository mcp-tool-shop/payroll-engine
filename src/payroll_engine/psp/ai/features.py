"""
Deterministic Feature Extraction for PSP AI Advisory.

CRITICAL: All features must be:
1. Reproducible - Same inputs always produce same outputs
2. Derivable from event store - No external state
3. Versioned - Schema changes are tracked

If a feature can't be recomputed from history, it doesn't belong here.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Sequence
from uuid import UUID
import hashlib
import json


# Feature schema version - bump when adding/changing features
RETURN_FEATURE_SCHEMA_VERSION = "1.0.0"
FUNDING_RISK_FEATURE_SCHEMA_VERSION = "1.0.0"


@dataclass(frozen=True)
class ReturnFeatures:
    """
    Features extracted for return root-cause analysis.

    All fields are deterministically derivable from:
    - The return event itself
    - Historical events for the tenant/employee
    - Provider metadata
    """
    # Core return info
    tenant_id: UUID
    payment_id: UUID
    return_code: str
    payment_rail: str
    amount: Decimal
    original_payment_date: datetime
    return_date: datetime

    # Derived temporal features
    days_since_payment: int
    is_same_day_return: bool
    is_weekend_return: bool

    # Employee/payee history
    payee_account_age_days: int
    payee_prior_returns_30d: int
    payee_prior_returns_90d: int
    payee_is_new_account: bool  # < 14 days old

    # Tenant history
    tenant_return_rate_30d: float  # Returns / total payments
    tenant_return_rate_90d: float
    tenant_funding_blocks_90d: int

    # Provider reliability
    provider_name: str
    provider_return_rate_90d: float
    provider_avg_settlement_days: float

    # Payment context
    payment_purpose: Optional[str]  # "payroll", "bonus", "expense", etc.
    batch_size: int  # Number of payments in the same batch

    @property
    def schema_version(self) -> str:
        return RETURN_FEATURE_SCHEMA_VERSION

    @property
    def schema_hash(self) -> str:
        """Hash of field names for versioning."""
        fields = sorted(self.__dataclass_fields__.keys())
        return hashlib.sha256(
            json.dumps(fields).encode()
        ).hexdigest()[:16]

    def to_dict(self) -> dict:
        """Convert to dictionary for model input."""
        return {
            "tenant_id": str(self.tenant_id),
            "payment_id": str(self.payment_id),
            "return_code": self.return_code,
            "payment_rail": self.payment_rail,
            "amount": float(self.amount),
            "days_since_payment": self.days_since_payment,
            "is_same_day_return": self.is_same_day_return,
            "is_weekend_return": self.is_weekend_return,
            "payee_account_age_days": self.payee_account_age_days,
            "payee_prior_returns_30d": self.payee_prior_returns_30d,
            "payee_prior_returns_90d": self.payee_prior_returns_90d,
            "payee_is_new_account": self.payee_is_new_account,
            "tenant_return_rate_30d": self.tenant_return_rate_30d,
            "tenant_return_rate_90d": self.tenant_return_rate_90d,
            "tenant_funding_blocks_90d": self.tenant_funding_blocks_90d,
            "provider_name": self.provider_name,
            "provider_return_rate_90d": self.provider_return_rate_90d,
            "provider_avg_settlement_days": self.provider_avg_settlement_days,
            "payment_purpose": self.payment_purpose,
            "batch_size": self.batch_size,
        }


@dataclass(frozen=True)
class FundingRiskFeatures:
    """
    Features extracted for funding risk prediction.

    All fields are deterministically derivable from:
    - Upcoming payroll data
    - Historical payroll/funding events
    - Tenant configuration
    """
    # Core payroll info
    tenant_id: UUID
    payroll_batch_id: Optional[UUID]
    payroll_amount: Decimal
    payment_count: int
    scheduled_date: datetime

    # Historical payroll patterns
    avg_payroll_amount_90d: Decimal
    stddev_payroll_amount_90d: Decimal
    spike_ratio: float  # current / avg (>1.5 is concerning)
    max_payroll_amount_90d: Decimal

    # Funding history
    days_since_last_funding_block: Optional[int]  # None if never blocked
    funding_blocks_30d: int
    funding_blocks_90d: int
    historical_block_rate: float  # Blocks / total payroll runs

    # Settlement reliability
    avg_settlement_delay_days: float
    p95_settlement_delay_days: float
    pending_settlements_count: int
    pending_settlements_amount: Decimal

    # Account balance context
    current_available_balance: Decimal
    current_reserved_balance: Decimal
    funding_headroom: Decimal  # available - (payroll + buffer)

    # Tenant configuration
    funding_model: str  # "prefunded", "just_in_time", "credit_line"
    has_backup_funding: bool

    @property
    def schema_version(self) -> str:
        return FUNDING_RISK_FEATURE_SCHEMA_VERSION

    @property
    def schema_hash(self) -> str:
        """Hash of field names for versioning."""
        fields = sorted(self.__dataclass_fields__.keys())
        return hashlib.sha256(
            json.dumps(fields).encode()
        ).hexdigest()[:16]

    def to_dict(self) -> dict:
        """Convert to dictionary for model input."""
        return {
            "tenant_id": str(self.tenant_id),
            "payroll_batch_id": str(self.payroll_batch_id) if self.payroll_batch_id else None,
            "payroll_amount": float(self.payroll_amount),
            "payment_count": self.payment_count,
            "avg_payroll_amount_90d": float(self.avg_payroll_amount_90d),
            "stddev_payroll_amount_90d": float(self.stddev_payroll_amount_90d),
            "spike_ratio": self.spike_ratio,
            "max_payroll_amount_90d": float(self.max_payroll_amount_90d),
            "days_since_last_funding_block": self.days_since_last_funding_block,
            "funding_blocks_30d": self.funding_blocks_30d,
            "funding_blocks_90d": self.funding_blocks_90d,
            "historical_block_rate": self.historical_block_rate,
            "avg_settlement_delay_days": self.avg_settlement_delay_days,
            "p95_settlement_delay_days": self.p95_settlement_delay_days,
            "pending_settlements_count": self.pending_settlements_count,
            "pending_settlements_amount": float(self.pending_settlements_amount),
            "current_available_balance": float(self.current_available_balance),
            "current_reserved_balance": float(self.current_reserved_balance),
            "funding_headroom": float(self.funding_headroom),
            "funding_model": self.funding_model,
            "has_backup_funding": self.has_backup_funding,
        }


class FeatureExtractor:
    """
    Extracts features from PSP domain events.

    This class queries the event store and computes features
    deterministically. The same events always produce the same features.
    """

    def __init__(self, event_store, lookback_days: int = 90):
        """
        Initialize feature extractor.

        Args:
            event_store: PSP event store for querying historical events
            lookback_days: How far back to look for historical features
        """
        self._event_store = event_store
        self._lookback_days = lookback_days

    def extract_return_features(
        self,
        tenant_id: UUID,
        payment_id: UUID,
        return_code: str,
        return_date: datetime,
    ) -> ReturnFeatures:
        """
        Extract features for a payment return.

        Args:
            tenant_id: The tenant ID
            payment_id: The payment instruction ID
            return_code: ACH/FedNow return code
            return_date: When the return occurred

        Returns:
            ReturnFeatures with all fields populated
        """
        # Get the original payment event
        payment_event = self._get_payment_event(payment_id)
        if not payment_event:
            raise ValueError(f"Payment {payment_id} not found in event store")

        # Calculate lookback windows
        now = return_date
        window_30d = now - timedelta(days=30)
        window_90d = now - timedelta(days=self._lookback_days)

        # Get payee history
        payee_id = payment_event.get("payee_id")
        payee_account_created = self._get_payee_account_created(tenant_id, payee_id)
        payee_account_age = (now - payee_account_created).days if payee_account_created else 0

        payee_returns_30d = self._count_payee_returns(tenant_id, payee_id, window_30d, now)
        payee_returns_90d = self._count_payee_returns(tenant_id, payee_id, window_90d, now)

        # Get tenant history
        tenant_payments_30d = self._count_tenant_payments(tenant_id, window_30d, now)
        tenant_returns_30d = self._count_tenant_returns(tenant_id, window_30d, now)
        tenant_payments_90d = self._count_tenant_payments(tenant_id, window_90d, now)
        tenant_returns_90d = self._count_tenant_returns(tenant_id, window_90d, now)
        tenant_blocks_90d = self._count_funding_blocks(tenant_id, window_90d, now)

        # Get provider stats
        provider_name = payment_event.get("provider_name", "unknown")
        provider_returns_90d = self._count_provider_returns(provider_name, window_90d, now)
        provider_payments_90d = self._count_provider_payments(provider_name, window_90d, now)
        provider_settlement_days = self._avg_provider_settlement_days(provider_name, window_90d, now)

        # Calculate rates (avoid division by zero)
        tenant_return_rate_30d = (
            tenant_returns_30d / tenant_payments_30d
            if tenant_payments_30d > 0 else 0.0
        )
        tenant_return_rate_90d = (
            tenant_returns_90d / tenant_payments_90d
            if tenant_payments_90d > 0 else 0.0
        )
        provider_return_rate_90d = (
            provider_returns_90d / provider_payments_90d
            if provider_payments_90d > 0 else 0.0
        )

        # Build features
        original_payment_date = datetime.fromisoformat(payment_event["created_at"])
        days_since_payment = (return_date - original_payment_date).days

        return ReturnFeatures(
            tenant_id=tenant_id,
            payment_id=payment_id,
            return_code=return_code,
            payment_rail=payment_event.get("rail", "ach"),
            amount=Decimal(str(payment_event.get("amount", 0))),
            original_payment_date=original_payment_date,
            return_date=return_date,
            days_since_payment=days_since_payment,
            is_same_day_return=days_since_payment == 0,
            is_weekend_return=return_date.weekday() >= 5,
            payee_account_age_days=payee_account_age,
            payee_prior_returns_30d=payee_returns_30d,
            payee_prior_returns_90d=payee_returns_90d,
            payee_is_new_account=payee_account_age < 14,
            tenant_return_rate_30d=tenant_return_rate_30d,
            tenant_return_rate_90d=tenant_return_rate_90d,
            tenant_funding_blocks_90d=tenant_blocks_90d,
            provider_name=provider_name,
            provider_return_rate_90d=provider_return_rate_90d,
            provider_avg_settlement_days=provider_settlement_days,
            payment_purpose=payment_event.get("purpose"),
            batch_size=payment_event.get("batch_size", 1),
        )

    def extract_funding_risk_features(
        self,
        tenant_id: UUID,
        payroll_amount: Decimal,
        payment_count: int,
        scheduled_date: datetime,
        payroll_batch_id: Optional[UUID] = None,
    ) -> FundingRiskFeatures:
        """
        Extract features for funding risk prediction.

        Args:
            tenant_id: The tenant ID
            payroll_amount: Total payroll amount
            payment_count: Number of payments in batch
            scheduled_date: When payroll is scheduled
            payroll_batch_id: Optional batch ID

        Returns:
            FundingRiskFeatures with all fields populated
        """
        now = scheduled_date
        window_30d = now - timedelta(days=30)
        window_90d = now - timedelta(days=self._lookback_days)

        # Get historical payroll stats
        payroll_history = self._get_payroll_history(tenant_id, window_90d, now)
        amounts = [Decimal(str(p.get("amount", 0))) for p in payroll_history]

        if amounts:
            avg_amount = sum(amounts) / len(amounts)
            variance = sum((a - avg_amount) ** 2 for a in amounts) / len(amounts)
            stddev_amount = Decimal(str(variance ** Decimal("0.5")))
            max_amount = max(amounts)
        else:
            avg_amount = Decimal("0")
            stddev_amount = Decimal("0")
            max_amount = Decimal("0")

        spike_ratio = float(payroll_amount / avg_amount) if avg_amount > 0 else 1.0

        # Get funding block history
        blocks_30d = self._count_funding_blocks(tenant_id, window_30d, now)
        blocks_90d = self._count_funding_blocks(tenant_id, window_90d, now)
        total_payrolls_90d = len(payroll_history) or 1
        historical_block_rate = blocks_90d / total_payrolls_90d

        last_block = self._get_last_funding_block(tenant_id, now)
        days_since_block = (now - last_block).days if last_block else None

        # Get settlement stats
        settlement_delays = self._get_settlement_delays(tenant_id, window_90d, now)
        if settlement_delays:
            avg_delay = sum(settlement_delays) / len(settlement_delays)
            sorted_delays = sorted(settlement_delays)
            p95_idx = int(len(sorted_delays) * 0.95)
            p95_delay = sorted_delays[min(p95_idx, len(sorted_delays) - 1)]
        else:
            avg_delay = 0.0
            p95_delay = 0.0

        pending = self._get_pending_settlements(tenant_id)
        pending_count = len(pending)
        pending_amount = sum(Decimal(str(p.get("amount", 0))) for p in pending)

        # Get balance info
        balance_info = self._get_balance_info(tenant_id)
        available = balance_info.get("available", Decimal("0"))
        reserved = balance_info.get("reserved", Decimal("0"))

        # Get tenant config
        tenant_config = self._get_tenant_config(tenant_id)
        funding_model = tenant_config.get("funding_model", "prefunded")
        has_backup = tenant_config.get("has_backup_funding", False)

        # Calculate headroom (10% buffer)
        buffer = payroll_amount * Decimal("0.1")
        headroom = available - payroll_amount - buffer

        return FundingRiskFeatures(
            tenant_id=tenant_id,
            payroll_batch_id=payroll_batch_id,
            payroll_amount=payroll_amount,
            payment_count=payment_count,
            scheduled_date=scheduled_date,
            avg_payroll_amount_90d=avg_amount,
            stddev_payroll_amount_90d=stddev_amount,
            spike_ratio=spike_ratio,
            max_payroll_amount_90d=max_amount,
            days_since_last_funding_block=days_since_block,
            funding_blocks_30d=blocks_30d,
            funding_blocks_90d=blocks_90d,
            historical_block_rate=historical_block_rate,
            avg_settlement_delay_days=avg_delay,
            p95_settlement_delay_days=p95_delay,
            pending_settlements_count=pending_count,
            pending_settlements_amount=pending_amount,
            current_available_balance=available,
            current_reserved_balance=reserved,
            funding_headroom=headroom,
            funding_model=funding_model,
            has_backup_funding=has_backup,
        )

    # =========================================================================
    # Event store query methods (to be implemented with real event store)
    # =========================================================================

    def _get_payment_event(self, payment_id: UUID) -> Optional[dict]:
        """Get the original payment creation event."""
        events = self._event_store.get_events(
            event_type="PaymentInstructionCreated",
            filters={"payment_id": str(payment_id)},
            limit=1,
        )
        return events[0] if events else None

    def _get_payee_account_created(
        self, tenant_id: UUID, payee_id: Optional[str]
    ) -> Optional[datetime]:
        """Get when payee account was first used."""
        if not payee_id:
            return None
        events = self._event_store.get_events(
            tenant_id=tenant_id,
            event_type="PaymentInstructionCreated",
            filters={"payee_id": payee_id},
            order="asc",
            limit=1,
        )
        if events:
            return datetime.fromisoformat(events[0]["created_at"])
        return None

    def _count_payee_returns(
        self, tenant_id: UUID, payee_id: Optional[str], start: datetime, end: datetime
    ) -> int:
        """Count returns for a specific payee."""
        if not payee_id:
            return 0
        events = self._event_store.get_events(
            tenant_id=tenant_id,
            event_type="PaymentReturned",
            filters={"payee_id": payee_id},
            start_time=start,
            end_time=end,
        )
        return len(events)

    def _count_tenant_payments(
        self, tenant_id: UUID, start: datetime, end: datetime
    ) -> int:
        """Count total payments for tenant."""
        events = self._event_store.get_events(
            tenant_id=tenant_id,
            event_type="PaymentSubmitted",
            start_time=start,
            end_time=end,
        )
        return len(events)

    def _count_tenant_returns(
        self, tenant_id: UUID, start: datetime, end: datetime
    ) -> int:
        """Count returns for tenant."""
        events = self._event_store.get_events(
            tenant_id=tenant_id,
            event_type="PaymentReturned",
            start_time=start,
            end_time=end,
        )
        return len(events)

    def _count_funding_blocks(
        self, tenant_id: UUID, start: datetime, end: datetime
    ) -> int:
        """Count funding gate blocks."""
        events = self._event_store.get_events(
            tenant_id=tenant_id,
            event_type="FundingBlocked",
            start_time=start,
            end_time=end,
        )
        return len(events)

    def _count_provider_returns(
        self, provider_name: str, start: datetime, end: datetime
    ) -> int:
        """Count returns for a provider."""
        events = self._event_store.get_events(
            event_type="PaymentReturned",
            filters={"provider_name": provider_name},
            start_time=start,
            end_time=end,
        )
        return len(events)

    def _count_provider_payments(
        self, provider_name: str, start: datetime, end: datetime
    ) -> int:
        """Count payments for a provider."""
        events = self._event_store.get_events(
            event_type="PaymentSubmitted",
            filters={"provider_name": provider_name},
            start_time=start,
            end_time=end,
        )
        return len(events)

    def _avg_provider_settlement_days(
        self, provider_name: str, start: datetime, end: datetime
    ) -> float:
        """Calculate average settlement time for provider."""
        events = self._event_store.get_events(
            event_type="PaymentSettled",
            filters={"provider_name": provider_name},
            start_time=start,
            end_time=end,
        )
        if not events:
            return 2.0  # Default assumption

        delays = []
        for e in events:
            submitted = e.get("submitted_at")
            settled = e.get("settled_at")
            if submitted and settled:
                diff = (
                    datetime.fromisoformat(settled) -
                    datetime.fromisoformat(submitted)
                ).days
                delays.append(diff)

        return sum(delays) / len(delays) if delays else 2.0

    def _get_payroll_history(
        self, tenant_id: UUID, start: datetime, end: datetime
    ) -> list[dict]:
        """Get historical payroll batches."""
        events = self._event_store.get_events(
            tenant_id=tenant_id,
            event_type="PayrollBatchCommitted",
            start_time=start,
            end_time=end,
        )
        return events

    def _get_last_funding_block(
        self, tenant_id: UUID, before: datetime
    ) -> Optional[datetime]:
        """Get the most recent funding block."""
        events = self._event_store.get_events(
            tenant_id=tenant_id,
            event_type="FundingBlocked",
            end_time=before,
            order="desc",
            limit=1,
        )
        if events:
            return datetime.fromisoformat(events[0]["created_at"])
        return None

    def _get_settlement_delays(
        self, tenant_id: UUID, start: datetime, end: datetime
    ) -> list[float]:
        """Get settlement delay times in days."""
        events = self._event_store.get_events(
            tenant_id=tenant_id,
            event_type="PaymentSettled",
            start_time=start,
            end_time=end,
        )
        delays = []
        for e in events:
            submitted = e.get("submitted_at")
            settled = e.get("settled_at")
            if submitted and settled:
                diff = (
                    datetime.fromisoformat(settled) -
                    datetime.fromisoformat(submitted)
                ).total_seconds() / 86400  # Convert to days
                delays.append(diff)
        return delays

    def _get_pending_settlements(self, tenant_id: UUID) -> list[dict]:
        """Get payments submitted but not yet settled."""
        # This would query for payments in SUBMITTED or ACCEPTED status
        # Implementation depends on how status is tracked
        return []

    def _get_balance_info(self, tenant_id: UUID) -> dict:
        """Get current balance information."""
        # This would query the ledger service
        return {"available": Decimal("0"), "reserved": Decimal("0")}

    def _get_tenant_config(self, tenant_id: UUID) -> dict:
        """Get tenant configuration."""
        # This would query tenant settings
        return {"funding_model": "prefunded", "has_backup_funding": False}
